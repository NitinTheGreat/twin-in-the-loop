from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

from twinloop.actions.schema import MigrateService, NoOp
from twinloop.agent.graph import run_episode
from twinloop.agent.rule_agent import RuleAgent
from twinloop.config import (
    EvaluationConfig,
    ExperimentConfig,
    GraphConfig,
    LLMConfig,
    SimConfig,
    TopologyConfig,
    TwinConfig,
)
from twinloop.experiment.arms import RunSpec, default_arms, expand_runs
from twinloop.experiment.counterfactual import evaluate_decision, make_counterfactual_hook
from twinloop.experiment.logging import completed_keys, read_jsonl
from twinloop.experiment.runner import run_single, run_sweep
from twinloop.faults.schedule import FaultSchedule, targets_from_topology
from twinloop.llm.budget import BudgetGuard
from twinloop.llm.cache import ResponseCache
from twinloop.llm.providers import ProviderResponse
from twinloop.sim.engine import NetworkSim, Topology, build_topology
from twinloop.sim.link import Link
from twinloop.sim.node import Node
from twinloop.sim.service import Service
from twinloop.sim.workload import PoissonWorkload
from twinloop.twin.validator import TwinValidator, TwinVerdict


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, model, temperature, timeout):
        self.calls += 1
        return ProviderResponse(text='{"action": {"type": "no_op"}}', tokens_in=8, tokens_out=8)


class StubReject:
    def validate(self, sim, action, obs):
        return TwinVerdict(False, "stub reject", 10, 0)


def _config(tmp_path, episode_ticks=40, interval=10, horizon=15, harm_threshold=3, seeds=(0, 1), fids=(0.5, 1.0)):
    return ExperimentConfig(
        seeds=list(seeds),
        fidelity_levels=list(fids),
        sim=SimConfig(episode_ticks=episode_ticks),
        graph=GraphConfig(decision_interval_ticks=interval),
        twin=TwinConfig(horizon_ticks=horizon),
        evaluation=EvaluationConfig(harm_threshold_ticks=harm_threshold, a4_fidelity=0.6),
        llm=LLMConfig(cache_dir=str(tmp_path / "llm_cache")),
    )


def _fake_factory():
    return lambda: FakeProvider()


def _sim(config, seed):
    topology = build_topology(config.topology, config.sim)
    schedule = FaultSchedule.generate(seed, config.fault, targets_from_topology(topology))
    return NetworkSim(topology, config.sim, seed=seed, schedule=schedule)


def _busy_spare():
    gateway = Node("gw0", "gateway", 100.0, 100.0)
    busy = Node("busy", "edge", 100.0, 100.0)
    spare = Node("spare", "edge", 100.0, 100.0)
    device = Node("dev0", "device", 10.0, 10.0)
    svc = Service("svcX", "busy", 1.0, 5.0)
    links = [
        Link("l_busy", ("gw0", "busy"), 1000.0, 5.0, 5.0),
        Link("l_spare", ("gw0", "spare"), 1000.0, 5.0, 5.0),
        Link("l_dev", ("gw0", "dev0"), 1000.0, 5.0, 5.0),
    ]
    return Topology([gateway, busy, spare, device], links, [svc], {"svcX": ["l_dev", "l_busy"]}, {"svcX": PoissonWorkload(25.0)})


def _run_with_cf(config, sim, agent, validator, seed):
    records = []

    def on_decision(decision_index, record, results, pre_action_sim):
        for gt in results:
            records.append(
                {
                    "tick": record["tick"],
                    "action": gt.action.model_dump(),
                    "harm_delta": gt.harm_delta,
                    "harmful": gt.harmful,
                    "was_applied": gt.was_applied,
                    "cf_noop": gt.cf_noop_violation_ticks,
                    "twin_verdict": gt.verdict.approved if gt.verdict else None,
                }
            )

    hook = make_counterfactual_hook(config, on_decision)
    result = run_episode(sim, agent, validator, config, seed, checkpointer=MemorySaver(), decision_hook=hook)
    return result, records


def test_trajectory_non_perturbation(tmp_path):
    config = _config(tmp_path)
    run = RunSpec("A1", "rule", False, 0, None)
    budget = BudgetGuard(10**9, 10**12)
    cache = ResponseCache(tmp_path / "c.json")

    _, on_result, _ = run_single(config, run, tmp_path / "on", _fake_factory(), budget, cache, True, MemorySaver())
    _, off_result, _ = run_single(config, run, tmp_path / "off", _fake_factory(), budget, cache, False, MemorySaver())

    assert on_result.metrics == off_result.metrics
    assert [d["applied_action"] for d in on_result.decisions] == [
        d["applied_action"] for d in off_result.decisions
    ]


def test_fault_schedule_identical_across_arms(tmp_path):
    config = _config(tmp_path)
    targets = targets_from_topology(build_topology(config.topology, config.sim))
    schedules = [
        FaultSchedule.generate(3, config.fault, targets).events for _ in range(5)
    ]
    for events in schedules[1:]:
        assert events == schedules[0]
    assert FaultSchedule.generate(3, config.fault, targets).events != FaultSchedule.generate(
        4, config.fault, targets
    ).events


def test_every_proposal_is_labelled(tmp_path):
    config = _config(tmp_path)
    run = RunSpec("A3", "llm", True, 0, 0.5)
    budget = BudgetGuard(10**9, 10**12)
    cache = ResponseCache(tmp_path / "c.json")
    _, _, records = run_single(config, run, tmp_path / "arm", _fake_factory(), budget, cache, True, MemorySaver())
    assert records
    for record in records:
        assert record["counterfactual_harm_delta"] is not None
        assert record["ground_truth_harmful"] in (True, False)
        assert record["slo_violations_cumulative_before"] is not None


def test_rejected_proposals_have_ground_truth(tmp_path):
    config = _config(tmp_path)
    result, records = _run_with_cf(config, _sim(config, 0), RuleAgent(), StubReject(), 0)
    rejected = [r for r in records if not r["was_applied"]]
    assert rejected
    for record in rejected:
        assert record["harm_delta"] is not None
        assert record["twin_verdict"] is False


def test_shared_baseline_within_decision(tmp_path):
    config = _config(tmp_path)
    _, records = _run_with_cf(config, _sim(config, 0), RuleAgent(), StubReject(), 0)
    by_tick = {}
    for record in records:
        by_tick.setdefault(record["tick"], []).append(record["cf_noop"])
    assert any(len(v) > 1 for v in by_tick.values())
    for noops in by_tick.values():
        assert len(set(noops)) == 1


def test_scheduled_differs_from_blind(tmp_path):
    config = _config(tmp_path, horizon=30)
    schedule = FaultSchedule([__import__("twinloop.faults.schedule", fromlist=["FaultEvent"]).FaultEvent("node_cpu_saturation", "edge2", 20, 15, 4.5)])
    sim = NetworkSim(build_topology(config.topology, config.sim), config.sim, seed=6, schedule=schedule)
    for _ in range(25):
        sim.step()

    validator = TwinValidator(TwinConfig(fidelity=1.0, horizon_ticks=30), seed=0, log_path=str(tmp_path / "t.jsonl"))
    verdict = validator.validate(sim, NoOp(), None)

    proposal = {"action": NoOp(), "retry_index": 0, "verdict": None, "was_applied": True}
    results = evaluate_decision(sim.fork(), [proposal], config)
    assert verdict.noop_violation_ticks != results[0].cf_noop_violation_ticks


def test_threshold_independence(tmp_path):
    low = _config(tmp_path, horizon=30, harm_threshold=0)
    high = _config(tmp_path, horizon=30, harm_threshold=1000)
    sim = NetworkSim(_busy_spare(), SimConfig(), seed=3)
    for _ in range(25):
        sim.step()
    action = MigrateService(service_id="svcX", target_node_id="spare")
    proposal = {"action": action, "retry_index": 0, "verdict": None, "was_applied": False}

    r_low = evaluate_decision(sim.fork(), [proposal], low)[0]
    r_high = evaluate_decision(sim.fork(), [proposal], high)[0]

    assert r_low.harm_delta == r_high.harm_delta
    assert r_low.harmful != r_high.harmful

    validator = TwinValidator(TwinConfig(fidelity=1.0, horizon_ticks=30, tolerance_margin=0.0), seed=0, log_path=str(tmp_path / "t.jsonl"))
    verdict = validator.validate(sim, action, None)
    assert verdict.approved == (
        verdict.action_violation_ticks - verdict.noop_violation_ticks <= 0
    )


def test_resumability(tmp_path):
    config = _config(tmp_path)
    interrupted = tmp_path / "resumed"
    run_sweep(config, interrupted, provider_factory=_fake_factory(), arm_ids=["A0", "A1"], seeds=[0], counterfactual=True)
    plan = run_sweep(config, interrupted, provider_factory=_fake_factory(), arm_ids=["A0", "A1"], seeds=[0, 1], counterfactual=True)
    assert plan.executed == 2

    uninterrupted = tmp_path / "full"
    run_sweep(config, uninterrupted, provider_factory=_fake_factory(), arm_ids=["A0", "A1"], seeds=[0, 1], counterfactual=True)

    resumed_keys = completed_keys(read_jsonl(interrupted / "summaries.jsonl"))
    full_keys = completed_keys(read_jsonl(uninterrupted / "summaries.jsonl"))
    assert resumed_keys == full_keys


def test_determinism_across_sweeps(tmp_path):
    config = _config(tmp_path, seeds=(0,), fids=(1.0,))

    run_sweep(config, tmp_path / "s1", provider_factory=_fake_factory(), arm_ids=["A2"], seeds=[0])
    run_sweep(config, tmp_path / "s2", provider_factory=_fake_factory(), arm_ids=["A2"], seeds=[0])

    def _project(path):
        return [
            (r["arm"], r["seed"], r["tick"], r["proposed_action"], r["counterfactual_harm_delta"], r["ground_truth_harmful"])
            for r in read_jsonl(path / "proposals.jsonl")
        ]

    assert _project(tmp_path / "s1") == _project(tmp_path / "s2")


def test_dry_run_executes_nothing(tmp_path):
    config = _config(tmp_path)
    provider = FakeProvider()
    plan = run_sweep(config, tmp_path / "dry", provider_factory=lambda: provider, seeds=[0, 1], dry_run=True)
    assert plan.executed == 0
    assert provider.calls == 0
    expected = len(expand_runs(default_arms(config), [0, 1], config.fidelity_levels))
    assert plan.planned_runs == expected


def test_all_five_arms_short_sweep(tmp_path):
    config = _config(tmp_path)
    plan = run_sweep(config, tmp_path / "sweep", provider_factory=_fake_factory(), seeds=[0, 1])
    summaries = read_jsonl(tmp_path / "sweep" / "summaries.jsonl")
    assert len(summaries) == plan.planned_runs == plan.executed
    assert {s["arm"] for s in summaries} == {"A0", "A1", "A2", "A3", "A4"}
