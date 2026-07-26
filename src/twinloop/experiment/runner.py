from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

from ..actions.validator import validate_action
from ..agent.graph import run_episode
from ..agent.llm_agent import LLMAgent
from ..agent.null_agent import NullAgent
from ..agent.rule_agent import RuleAgent
from ..faults.schedule import FaultSchedule, targets_from_topology
from ..llm.budget import BudgetGuard
from ..llm.cache import ResponseCache
from ..llm.client import LLMClient
from ..llm.providers import CloudProvider, LocalProvider
from ..sim.engine import NetworkSim, build_topology
from ..telemetry.slo import SLOEvaluator
from ..twin.fidelity import fidelity_to_config
from ..twin.validator import TwinValidator
from .arms import default_arms, expand_runs
from .counterfactual import make_counterfactual_hook
from .logging import (
    append_jsonl,
    completed_keys,
    episode_summary,
    proposal_record,
    read_jsonl,
)


@dataclass
class SweepPlan:
    planned_runs: int
    estimated_llm_calls: int
    executed: int = 0


def _fid_str(fidelity) -> str:
    return "none" if fidelity is None else f"{fidelity:.2f}"


def _default_provider_factory(config):
    def factory():
        if config.llm.provider == "cloud":
            return CloudProvider(config.llm)
        return LocalProvider(config.llm)

    return factory


def _make_agent(run, config, provider_factory, budget, cache, log_path):
    if run.agent_kind == "null":
        return NullAgent()
    if run.agent_kind == "rule":
        return RuleAgent(config.rule_agent, config.actions)
    provider = provider_factory()
    client = LLMClient(provider, config.llm, cache=cache, budget=budget, log_path=log_path)
    return LLMAgent(client, config.llm, config.slo, config.actions)


def _cumulative(slo_config, metrics) -> list[int]:
    evaluator = SLOEvaluator(slo_config)
    cumulative = []
    for tick_metrics in metrics:
        evaluator.evaluate(tick_metrics)
        cumulative.append(evaluator.total_violation_ticks)
    return cumulative


def _schedule_for(config, seed):
    topology = build_topology(config.topology, config.sim)
    schedule = FaultSchedule.generate(
        seed, config.fault, targets_from_topology(topology)
    )
    return topology, schedule


def run_single(config, run, output_dir, provider_factory, budget, cache, counterfactual, checkpointer):
    output_dir = Path(output_dir)
    topology, schedule = _schedule_for(config, run.seed)
    sim = NetworkSim(topology, config.sim, seed=run.seed, schedule=schedule)
    agent = _make_agent(
        run, config, provider_factory, budget, cache, str(output_dir / "llm_calls.jsonl")
    )

    validator = None
    if run.gate_enabled:
        twin_config = fidelity_to_config(
            run.fidelity, config.twin.horizon_ticks, config.twin.tolerance_margin
        )
        validator = TwinValidator(
            twin_config,
            seed=run.seed,
            actions_config=config.actions,
            slo_config=config.slo,
            log_path=str(output_dir / "twin.jsonl"),
        )

    episode_id = f"{run.arm_id}_s{run.seed}_f{_fid_str(run.fidelity)}"
    records: list[dict] = []
    last_llm = [0]

    def on_decision(decision_index, record, results, pre_action_sim):
        client = getattr(agent, "client", None)
        if client is not None:
            new = client.records[last_llm[0]:]
            last_llm[0] = len(client.records)
            llm_latency = sum(r.latency_ms for r in new)
            llm_tokens_in = sum(r.tokens_in for r in new)
            llm_tokens_out = sum(r.tokens_out for r in new)
            llm_cache_hit = any(r.cache_hit for r in new)
        else:
            llm_latency = 0.0
            llm_tokens_in = 0
            llm_tokens_out = 0
            llm_cache_hit = False

        for proposal_index, gt in enumerate(results):
            verdict = gt.verdict
            semantically_valid = validate_action(
                gt.action, pre_action_sim.state, config.actions
            ).valid
            records.append(
                proposal_record(
                    episode_id=episode_id,
                    arm=run.arm_id,
                    seed=run.seed,
                    fidelity=run.fidelity,
                    tick=record["tick"],
                    decision_index=decision_index,
                    proposal_index=proposal_index,
                    agent_type=run.agent_kind,
                    prompt_version=record.get("prompt_version"),
                    decision_interval_ticks=record["decision_interval"],
                    proposed_action=gt.action.model_dump(),
                    schema_valid=True,
                    semantically_valid=semantically_valid,
                    twin_verdict=verdict.approved if verdict else None,
                    twin_reason=verdict.reason if verdict else None,
                    twin_predicted_violation_ticks_action=verdict.action_violation_ticks
                    if verdict
                    else None,
                    twin_predicted_violation_ticks_noop=verdict.noop_violation_ticks
                    if verdict
                    else None,
                    counterfactual_violation_ticks_action=gt.cf_action_violation_ticks,
                    counterfactual_violation_ticks_noop=gt.cf_noop_violation_ticks,
                    counterfactual_harm_delta=gt.harm_delta,
                    ground_truth_harmful=gt.harmful,
                    was_applied=gt.was_applied,
                    retry_index=gt.retry_index,
                    exhausted=record["exhausted"],
                    slo_violations_cumulative_before=None,
                    slo_violations_cumulative_after=None,
                    llm_latency_ms=llm_latency,
                    llm_tokens_in=llm_tokens_in,
                    llm_tokens_out=llm_tokens_out,
                    llm_cache_hit=llm_cache_hit,
                    twin_wallclock_ms=verdict.cost_ms if verdict else None,
                    counterfactual_wallclock_ms=gt.cf_wallclock_ms,
                )
            )

    hook = make_counterfactual_hook(config, on_decision) if counterfactual else None
    result = run_episode(
        sim,
        agent,
        validator,
        config,
        run.seed,
        checkpointer=checkpointer,
        decision_hook=hook,
        thread_prefix=episode_id,
    )

    cumulative = _cumulative(config.slo, result.metrics)
    interval = config.graph.decision_interval_ticks
    for record in records:
        tick = record["tick"]
        record["slo_violations_cumulative_before"] = (
            cumulative[tick - 1] if 1 <= tick <= len(cumulative) else 0
        )
        end_index = min(tick + interval - 1, len(cumulative) - 1)
        record["slo_violations_cumulative_after"] = (
            cumulative[end_index] if 0 <= end_index < len(cumulative) else 0
        )

    harmful = sum(1 for r in records if r["ground_truth_harmful"])
    blocked = sum(
        1 for r in records if r["ground_truth_harmful"] and r["twin_verdict"] is False
    )
    summary = episode_summary(
        episode_id=episode_id,
        arm=run.arm_id,
        seed=run.seed,
        fidelity=run.fidelity,
        agent_type=run.agent_kind,
        gate_enabled=run.gate_enabled,
        decision_interval_ticks=interval,
        total_ticks=config.sim.episode_ticks,
        slo_violation_ticks=result.violation_ticks,
        proposals=result.proposals,
        rejections=result.rejections,
        retries=result.retries,
        exhaustions=result.exhaustions,
        harmful_proposals=harmful,
        harmful_proposals_blocked=blocked,
        llm_calls=result.llm_calls,
        llm_tokens=result.llm_tokens,
        llm_tokens_in=result.llm_tokens_in,
        llm_tokens_out=result.llm_tokens_out,
    )
    return summary, result, records


def _planned_decisions(config) -> int:
    return len(
        [t for t in range(config.sim.episode_ticks) if t % config.graph.decision_interval_ticks == 0]
    )


def run_sweep(
    config,
    output_dir,
    provider_factory=None,
    arm_ids=None,
    seeds=None,
    fidelity_levels=None,
    dry_run=False,
    resume=True,
    counterfactual=True,
    checkpointer_factory=None,
    progress=None,
):
    output_dir = Path(output_dir)
    arms = [a for a in default_arms(config) if arm_ids is None or a.arm_id in arm_ids]
    seeds = seeds if seeds is not None else config.seeds
    fidelities = fidelity_levels if fidelity_levels is not None else config.fidelity_levels
    runs = expand_runs(arms, seeds, fidelities)

    decisions = _planned_decisions(config)
    estimated_calls = sum(decisions * 2 for r in runs if r.agent_kind == "llm")

    if dry_run:
        return SweepPlan(planned_runs=len(runs), estimated_llm_calls=estimated_calls, executed=0)

    completed = completed_keys(read_jsonl(output_dir / "summaries.jsonl")) if resume else set()
    provider_factory = provider_factory or _default_provider_factory(config)
    checkpointer_factory = checkpointer_factory or (lambda: MemorySaver())
    budget = BudgetGuard(config.llm.max_calls, config.llm.max_tokens)
    cache = ResponseCache(Path(config.llm.cache_dir) / "cache.json")

    executed = 0
    for run in runs:
        key = (run.arm_id, run.seed, run.fidelity)
        if key in completed:
            continue
        summary, result, records = run_single(
            config, run, output_dir, provider_factory, budget, cache, counterfactual, checkpointer_factory()
        )
        for record in records:
            append_jsonl(output_dir / "proposals.jsonl", record)
        append_jsonl(output_dir / "summaries.jsonl", summary)
        executed += 1
        if progress is not None:
            progress(run, executed, len(runs), summary)

    return SweepPlan(planned_runs=len(runs), estimated_llm_calls=estimated_calls, executed=executed)
