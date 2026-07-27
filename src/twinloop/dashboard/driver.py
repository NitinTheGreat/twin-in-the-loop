from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from ..actions.executor import execute_action
from ..actions.schema import NoOp
from ..actions.validator import validate_action
from ..agent.base import context_from_sim
from ..agent.llm_agent import LLMAgent
from ..agent.null_agent import NullAgent
from ..agent.rule_agent import RuleAgent
from ..config import ExperimentConfig, GraphConfig, SimConfig, TwinConfig
from ..experiment.counterfactual import evaluate_decision
from ..faults.schedule import FaultSchedule, targets_from_topology
from ..llm.budget import BudgetGuard
from ..llm.cache import ResponseCache
from ..llm.client import LLMClient
from ..llm.providers import GeminiProvider, LocalProvider, ProviderResponse
from ..sim.engine import build_topology, NetworkSim
from ..telemetry.collector import Collector, summarize_topology
from ..telemetry.slo import SLOEvaluator
from ..telemetry.summarizer import Summarizer
from ..twin.fidelity import fidelity_to_config
from ..twin.validator import TwinValidator, feedback_from_verdict


def _first_violator(prompt):
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped.startswith("svc") and "(host" in stripped:
            return stripped.split(" ")[0]
    return "svc0"


class ScriptedProvider:
    def complete(self, messages, model, temperature, timeout):
        prompt = "\n".join(m["content"] for m in messages)
        if "VIOLATIONS:" not in prompt:
            reply = (
                '{"thought": "Every service is within its SLO right now, so the safest '
                'and cheapest choice is to do nothing.", "action": {"type": "no_op"}}'
            )
        elif "TOOL RESULT" not in prompt:
            victim = _first_violator(prompt)
            reply = (
                '{"thought": "' + victim + ' is breaching its SLO. Before acting I will '
                'pull its recent history to see whether latency or availability is the problem.", '
                '"tool": "get_service_history", "tool_input": {"service_id": "' + victim + '"}}'
            )
        else:
            victim = _first_violator(prompt)
            reply = (
                '{"thought": "Latency is elevated but availability looks intact and the host has '
                'spare capacity, so I will add a replica rather than risk migration downtime.", '
                '"action": {"type": "scale_service", "service_id": "' + victim + '", "delta_replicas": 1}}'
            )
        return ProviderResponse(
            text=reply, tokens_in=max(1, len(prompt) // 4), tokens_out=max(1, len(reply) // 4)
        )


@dataclass
class TickSnapshot:
    tick: int
    node_util: dict = field(default_factory=dict)
    node_status: dict = field(default_factory=dict)
    link_latency: dict = field(default_factory=dict)
    link_status: dict = field(default_factory=dict)
    service_host: dict = field(default_factory=dict)
    service_p95_ms: dict = field(default_factory=dict)
    service_drop: dict = field(default_factory=dict)
    service_compliant: dict = field(default_factory=dict)
    active_faults: list = field(default_factory=list)
    violations_now: int = 0
    cumulative_violation_ticks: int = 0
    is_decision: bool = False


@dataclass
class ProposalView:
    action: dict
    retry_index: int
    reasoning: list = field(default_factory=list)
    tools_called: list = field(default_factory=list)
    approved: Optional[bool] = None
    twin_reason: Optional[str] = None
    twin_pred_action_vt: Optional[int] = None
    twin_pred_noop_vt: Optional[int] = None
    twin_action_p95: list = field(default_factory=list)
    twin_noop_p95: list = field(default_factory=list)
    cf_action_vt: int = 0
    cf_noop_vt: int = 0
    harm_delta: int = 0
    harmful: bool = False
    was_applied: bool = False


@dataclass
class DecisionView:
    tick: int
    agent_type: str
    observation_text: str
    proposals: list = field(default_factory=list)
    applied_action: dict = field(default_factory=dict)
    exhausted: bool = False
    slo_before: int = 0
    slo_after: int = 0


@dataclass
class EpisodeView:
    arm_label: str
    agent_kind: str
    gate_enabled: bool
    fidelity: Optional[float]
    seed: int
    nodes: list = field(default_factory=list)
    links: list = field(default_factory=list)
    services: list = field(default_factory=list)
    ticks: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    total_violation_ticks: int = 0
    proposals: int = 0
    harmful_proposals: int = 0
    harmful_blocked: int = 0
    llm_calls: int = 0
    llm_tokens: int = 0


def build_config(
    episode_ticks=120,
    interval=10,
    retry_cap=2,
    horizon=20,
    harm_threshold=3,
    fidelity=1.0,
    arrival_rate=25.0,
):
    config = ExperimentConfig(
        sim=SimConfig(episode_ticks=episode_ticks),
        graph=GraphConfig(decision_interval_ticks=interval, retry_cap=retry_cap),
        twin=TwinConfig(fidelity=fidelity, horizon_ticks=horizon),
    )
    config.evaluation.harm_threshold_ticks = harm_threshold
    return config


def _provider(provider_name, config):
    if provider_name == "gemini":
        return GeminiProvider(os.environ.get("GEMINI_API_KEY"))
    if provider_name == "local":
        return LocalProvider(config.llm)
    return ScriptedProvider()


def _build_agent(agent_kind, provider_name, config, cache_dir):
    if agent_kind == "null":
        return NullAgent()
    if agent_kind == "rule":
        return RuleAgent(config.rule_agent, config.actions)
    if provider_name == "gemini":
        config.llm.model = "gemini-3.6-flash"
    provider = _provider(provider_name, config)
    client = LLMClient(
        provider,
        config.llm,
        cache=ResponseCache(f"{cache_dir}/cache.json"),
        budget=BudgetGuard(config.llm.max_calls, config.llm.max_tokens),
        log_path=f"{cache_dir}/calls.jsonl",
    )
    return LLMAgent(client, config.llm, config.slo, config.actions)


def _mean(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def _trajectory_p95(metrics):
    return [_mean(m.service_p95.values()) * 1000.0 for m in metrics]


def run_instrumented_episode(
    agent_kind="rule",
    gate_enabled=False,
    fidelity=1.0,
    provider_name="scripted",
    seed=0,
    episode_ticks=120,
    interval=10,
    horizon=20,
    harm_threshold=3,
    arrival_rate=25.0,
    cache_dir="results/dashboard_cache",
):
    config = build_config(
        episode_ticks=episode_ticks,
        interval=interval,
        horizon=horizon,
        harm_threshold=harm_threshold,
        fidelity=fidelity,
        arrival_rate=arrival_rate,
    )
    topology = build_topology(config.topology, config.sim, arrival_rate=arrival_rate)
    schedule = FaultSchedule.generate(seed, config.fault, targets_from_topology(topology))
    sim = NetworkSim(topology, config.sim, seed=seed, schedule=schedule)

    agent = _build_agent(agent_kind, provider_name, config, cache_dir)
    validator = None
    if gate_enabled:
        twin_config = fidelity_to_config(
            fidelity, config.twin.horizon_ticks, config.twin.tolerance_margin
        )
        validator = TwinValidator(
            twin_config,
            seed=seed,
            actions_config=config.actions,
            slo_config=config.slo,
            log_path=f"{cache_dir}/twin.jsonl",
        )

    collector = Collector(summarize_topology(topology), config.slo)
    summarizer = Summarizer(config.slo)

    view = EpisodeView(
        arm_label=_arm_label(agent_kind, gate_enabled, fidelity),
        agent_kind=agent_kind,
        gate_enabled=gate_enabled,
        fidelity=fidelity if gate_enabled else None,
        seed=seed,
        nodes=[(n.id, n.role) for n in topology.nodes],
        links=[(l.id, l.endpoints[0], l.endpoints[1]) for l in topology.links],
        services=[(s.id, s.host_node_id) for s in topology.services],
    )

    retry_cap = config.graph.retry_cap
    for _ in range(episode_ticks):
        metrics = sim.step()
        obs = collector.observe(metrics)
        tick = metrics.tick

        active = []
        if sim._injector is not None:
            active = [
                {"type": e.type, "target": e.target, "magnitude": round(e.magnitude, 1)}
                for e in sim._injector.active_events(tick)
            ]

        snapshot = TickSnapshot(
            tick=tick,
            node_util={nid: metrics.node_utilisation.get(nid, 0.0) for nid, _ in view.nodes},
            node_status={nid: sim.state.nodes[nid].status for nid, _ in view.nodes},
            link_latency=dict(metrics.link_latency),
            link_status={lid: sim.state.links[lid].status for lid, _, _ in view.links},
            service_host={sid: sim.state.services[sid].host_node_id for sid, _ in view.services},
            service_p95_ms={sid: metrics.service_p95.get(sid, 0.0) * 1000.0 for sid, _ in view.services},
            service_drop={sid: metrics.service_drop_rate.get(sid, 0.0) for sid, _ in view.services},
            service_compliant={sid: obs.slo_status[sid].compliant for sid, _ in view.services},
            active_faults=active,
            violations_now=sum(1 for s in obs.slo_status.values() if not s.compliant),
            cumulative_violation_ticks=collector.evaluator.total_violation_ticks,
            is_decision=(tick % interval == 0),
        )
        view.ticks.append(snapshot)

        if tick % interval != 0:
            continue

        pre_action_sim = sim.fork()
        agent.set_context(context_from_sim(sim))
        decision = DecisionView(
            tick=tick,
            agent_type=agent_kind,
            observation_text=summarizer.render(obs),
            slo_before=collector.evaluator.total_violation_ticks,
        )

        feedback = None
        proposal_records = []
        applied_action = None
        exhausted = False
        retries = 0

        while True:
            action = agent.decide(obs, feedback)
            reasoning, tools = _agent_thinking(agent)
            verdict = validator.validate(sim, action, obs) if gate_enabled else None
            proposal = ProposalView(
                action=action.model_dump(),
                retry_index=retries,
                reasoning=reasoning,
                tools_called=tools,
            )
            if verdict is not None:
                proposal.approved = verdict.approved
                proposal.twin_reason = verdict.reason
                proposal.twin_pred_action_vt = verdict.action_violation_ticks
                proposal.twin_pred_noop_vt = verdict.noop_violation_ticks
                proposal.twin_action_p95 = _trajectory_p95(verdict.action_metrics)
                proposal.twin_noop_p95 = _trajectory_p95(verdict.noop_metrics)
            proposal_records.append(
                {"action": action, "retry_index": retries, "verdict": verdict, "was_applied": False, "view": proposal}
            )

            if verdict is None or verdict.approved:
                applied_action = action
                proposal_records[-1]["was_applied"] = True
                break
            if retries >= retry_cap:
                applied_action = NoOp()
                exhausted = True
                break
            retries += 1
            feedback = feedback_from_verdict(verdict)

        ground_truth = evaluate_decision(pre_action_sim, proposal_records, config)
        for record, gt in zip(proposal_records, ground_truth):
            pv = record["view"]
            pv.cf_action_vt = gt.cf_action_violation_ticks
            pv.cf_noop_vt = gt.cf_noop_violation_ticks
            pv.harm_delta = gt.harm_delta
            pv.harmful = gt.harmful
            pv.was_applied = record["was_applied"]
            decision.proposals.append(pv)

        semantic = validate_action(applied_action, sim.state, config.actions)
        applied = applied_action if semantic.valid else NoOp()
        execute_action(sim, applied, config.actions)

        decision.applied_action = applied.model_dump()
        decision.exhausted = exhausted
        view.decisions.append(decision)

    _fill_slo_after(view, interval)
    view.total_violation_ticks = collector.evaluator.total_violation_ticks
    view.proposals = sum(len(d.proposals) for d in view.decisions)
    view.harmful_proposals = sum(1 for d in view.decisions for p in d.proposals if p.harmful)
    view.harmful_blocked = sum(
        1 for d in view.decisions for p in d.proposals if p.harmful and p.approved is False
    )
    client = getattr(agent, "client", None)
    if client is not None:
        view.llm_calls = len(client.records)
        view.llm_tokens = sum(r.tokens_in + r.tokens_out for r in client.records)
    return view


def _agent_thinking(agent):
    trace = getattr(agent, "last_trace", None)
    if trace is not None:
        return list(trace.get("reasoning", [])), list(trace.get("tools_called", []))
    reason = getattr(agent, "last_reason", None)
    if reason:
        return [reason], []
    return [], []


def _fill_slo_after(view, interval):
    cumulative = [t.cumulative_violation_ticks for t in view.ticks]
    for decision in view.decisions:
        end = min(decision.tick + interval - 1, len(cumulative) - 1)
        decision.slo_after = cumulative[end] if 0 <= end < len(cumulative) else decision.slo_before


def _arm_label(agent_kind, gate_enabled, fidelity):
    names = {"null": "Do nothing", "rule": "Rule agent", "llm": "LLM agent"}
    base = names.get(agent_kind, agent_kind)
    if gate_enabled:
        return f"{base} + twin (fidelity {fidelity:.2f})"
    return base


def compare_arms(seed=0, episode_ticks=120, interval=10, horizon=20, low_fidelity=0.4):
    specs = [
        ("A0", "null", False, 1.0),
        ("A1", "rule", False, 1.0),
        ("A2", "llm", False, 1.0),
        ("A3 (twin, low fidelity)", "llm", True, low_fidelity),
        ("A3 (twin, perfect)", "llm", True, 1.0),
        ("A4 (rule + twin)", "rule", True, low_fidelity),
    ]
    rows = []
    for arm_id, kind, gate, fidelity in specs:
        view = run_instrumented_episode(
            agent_kind=kind,
            gate_enabled=gate,
            fidelity=fidelity,
            provider_name="scripted",
            seed=seed,
            episode_ticks=episode_ticks,
            interval=interval,
            horizon=horizon,
        )
        rows.append(
            {
                "arm": arm_id,
                "label": view.arm_label,
                "violation_ticks": view.total_violation_ticks,
                "proposals": view.proposals,
                "harmful": view.harmful_proposals,
                "blocked": view.harmful_blocked,
            }
        )
    return rows
