"""Known-value regressions for architectural audit Priority 1."""
import json
from dataclasses import asdict

import pytest

from twinloop.actions.executor import execute_action
from twinloop.actions.schema import MigrateService, NoOp, RerouteTraffic, RestartService
from twinloop.actions.validator import validate_action
from twinloop.agent.base import context_from_sim
from twinloop.agent.llm_agent import LLMAgent
from twinloop.config import ActionsConfig, ExperimentConfig, SimConfig, SLOConfig, TopologyConfig
from twinloop.diagnostics.enumerator import candidates, enumerate_opportunities
from twinloop.experiment.counterfactual import evaluate_decision
from twinloop.faults.schedule import FaultEvent, FaultSchedule
from twinloop.sim.engine import NetworkSim, build_topology
from twinloop.sim.routing import has_alternative_path, path_error
from twinloop.sim.service import Request
from twinloop.telemetry.collector import Collector, summarize_topology
from twinloop.telemetry.slo import SLOEvaluator
from twinloop.twin.rollout import rollout, FAULT_MODE_SCHEDULED


def sim(events=(), rate=25, redundant=False, seed=0):
    config = SimConfig()
    topology = build_topology(TopologyConfig(redundant_links=redundant), config, arrival_rate=rate)
    return NetworkSim(topology, config, seed, FaultSchedule(list(events)))


def test_exact_migration_probe_and_live_placement_tools():
    n = sim()
    collector = Collector(summarize_topology(n.topology), SLOConfig())
    execute_action(n, MigrateService(service_id="svc0", target_node_id="edge1"), ActionsConfig())
    for _ in range(10):
        obs = collector.observe(n.step())
    service = n.state.services["svc0"]
    assert service.host_node_id == "edge1"
    assert n.state.routes["svc0"] == ["l_gw_dev0", "l_gw_edge1"]
    assert not path_error(n.state, n.state.routes["svc0"], "dev0", "edge1")
    assert obs.topology.service_hosts["svc0"] == "edge1"
    agent = LLMAgent(None)
    agent.set_context(context_from_sim(n))
    assert "svc0" in agent._node_metrics("edge1", obs)["services"]
    assert "svc0" not in agent._node_metrics("edge0", obs)["services"]
    assert next(s for s in agent._topology()["services"] if s["id"] == "svc0")["host_node_id"] == "edge1"


@pytest.mark.parametrize("path", [
    ["l_gw_edge0"], ["l_gw_edge0", "l_gw_dev0"],
    ["l_gw_dev1", "l_gw_edge0"],
    ["l_gw_dev0", "l_gw_edge1", "l_gw_edge0"],
])
def test_invalid_ordered_routes_rejected(path):
    assert not validate_action(RerouteTraffic(service_id="svc0", path_hint=path),
                               sim().state, ActionsConfig()).valid


@pytest.mark.parametrize("reverse", [False, True])
def test_exact_overlapping_crashes_union(reverse):
    events = [FaultEvent("node_crash", "edge0", 0, 10, 1),
              FaultEvent("node_crash", "edge0", 5, 10, 1)]
    n = sim(events[::-1] if reverse else events)
    statuses = []
    for _ in range(17):
        n.step()
        statuses.append(n.state.nodes["edge0"].status)
    assert statuses == ["down"] * 15 + ["healthy"] * 2


def test_overlapping_leaks_compose_and_restart_reclaims_each_contribution():
    events = [FaultEvent("service_memory_leak", "svc0", 0, 4, 2),
              FaultEvent("service_memory_leak", "svc0", 2, 4, 3)]
    n = sim(events)
    footprints = []
    for _ in range(7):
        n.step()
        footprints.append(n.state.services["svc0"].mem_footprint)
    assert footprints == [7, 9, 14, 19, 14, 17, 5]
    n = sim([FaultEvent("service_memory_leak", "svc0", 0, 10, 2),
             FaultEvent("service_memory_leak", "svc0", 0, 10, 3)])
    n.step()
    execute_action(n, RestartService(service_id="svc0"), ActionsConfig())
    for _ in range(3):
        n.step()
    assert n.state.services["svc0"].mem_footprint == 5
    n.step()
    assert n.state.services["svc0"].mem_footprint == 10


def test_cross_type_node_precedence_and_fork_composition():
    n = sim([FaultEvent("node_crash", "edge0", 0, 3, 1),
             FaultEvent("node_cpu_saturation", "edge0", 1, 4, 2)])
    n.step()
    before = n.snapshot()
    child = n.fork()
    assert child.step() == n.step()
    assert child.state.nodes["edge0"].status == "down"
    n.restore(before)
    statuses = []
    for _ in range(5):
        n.step()
        statuses.append(n.state.nodes["edge0"].status)
    assert statuses == ["down", "down", "degraded", "degraded", "healthy"]
    assert n.state.nodes["edge0"].cpu_reserved == 0


@pytest.mark.parametrize("kind,target,field,expected", [
    ("traffic_surge", "svc0", "rate", [50, 150, 75, 25]),
    ("link_degradation", "l_gw_edge0", "latency_multiplier", [2, 6, 3, 1]),
    ("node_cpu_saturation", "edge0", "cpu_reserved", [40, 98, 60, 0]),
])
def test_other_overlapping_fault_contributions(kind, target, field, expected):
    n = sim([FaultEvent(kind, target, 0, 2, 2), FaultEvent(kind, target, 1, 2, 3)])
    collection = (n.state.workloads if kind == "traffic_surge" else
                  n.state.links if kind == "link_degradation" else n.state.nodes)
    actual = []
    for _ in range(4):
        n.step()
        actual.append(getattr(collection[target], field))
    assert actual == expected


def test_overlapping_link_failure_and_degradation():
    n = sim([FaultEvent("link_failure", "l_gw_edge0", 0, 2, 1),
             FaultEvent("link_degradation", "l_gw_edge0", 1, 2, 3)])
    actual = []
    for _ in range(4):
        n.step()
        link = n.state.links["l_gw_edge0"]
        actual.append((link.status, link.latency_multiplier, round(link.loss_rate, 2)))
    assert actual == [("down", 1, 0), ("down", 3, .3), ("up", 3, .3), ("up", 1, 0)]


def test_migration_losing_destination_path_keeps_consistent_old_placement():
    n = sim([FaultEvent("link_failure", "l_gw_edge1", 5, 20, 1)])
    execute_action(n, MigrateService(service_id="svc0", target_node_id="edge1"), ActionsConfig())
    for _ in range(10):
        n.step()
    assert n.state.services["svc0"].host_node_id == "edge0"
    assert n.state.routes["svc0"] == ["l_gw_dev0", "l_gw_edge0"]


@pytest.mark.parametrize("action", [RestartService(service_id="svc0"),
    MigrateService(service_id="svc0", target_node_id="edge1")])
def test_action_loss_with_zero_arrivals_and_fork(action):
    n = sim(rate=0)
    service = n.state.services["svc0"]
    service.queue = [Request(0), Request(0)]
    service.in_service = Request(0, work=1)
    execute_action(n, action, ActionsConfig())
    assert service.total_request_losses == service.pending_request_losses == 3
    child = n.fork()
    metrics = n.step()
    assert child.step() == metrics
    assert metrics.service_dropped["svc0"] == 3
    assert metrics.service_drop_rate["svc0"] == 1
    assert metrics.service_outstanding["svc0"] == 0
    assert n.step().service_dropped["svc0"] == 0


def test_exact_zero_arrival_crash_with_backlog():
    n = sim([FaultEvent("node_crash", "edge0", 0, 10, 1)], rate=0)
    n.state.services["svc0"].queue = [Request(0)] * 5
    n.state.services["svc0"].in_service = Request(0, work=3)
    metrics = n.step()
    assert metrics.service_dropped["svc0"] == 6
    assert metrics.service_drop_rate["svc0"] == 1
    assert metrics.service_p95["svc0"] is None
    assert not SLOEvaluator(SLOConfig()).evaluate(metrics)["svc0"].compliant
    json.dumps(asdict(metrics), allow_nan=False)


def test_request_conservation_through_overflow_crash_and_actions():
    n = sim([FaultEvent("node_crash", "edge0", 8, 4, 1)], rate=70)
    n.config.queue_cap = 8
    previous = {sid: 0 for sid in n.state.services}
    for tick in range(24):
        if tick == 5:
            execute_action(n, RestartService(service_id="svc0"), ActionsConfig())
        if tick == 16:
            execute_action(n, MigrateService(service_id="svc1", target_node_id="edge2"), ActionsConfig())
        metrics = n.step()
        for sid in n.state.services:
            assert previous[sid] + metrics.service_arrivals[sid] == (
                metrics.service_completed[sid] + metrics.service_dropped[sid]
                + metrics.service_outstanding[sid])
            assert 0 <= metrics.service_drop_rate[sid] <= 1
        previous = metrics.service_outstanding


@pytest.mark.parametrize("action,downtime", [
    (RestartService(service_id="svc0"), 3),
    (MigrateService(service_id="svc0", target_node_id="edge1"), 10),
])
def test_exact_processing_downtime(action, downtime):
    n = sim()
    execute_action(n, action, ActionsConfig())
    for _ in range(downtime):
        metrics = n.step()
        assert metrics.service_completed["svc0"] == 0
        assert metrics.service_p95["svc0"] is None
        assert not SLOEvaluator(SLOConfig()).evaluate(metrics)["svc0"].compliant
    assert n.step().service_completed["svc0"] > 0


def test_memory_sensor_and_preemptive_restart_improve_scored_objective():
    n = sim([FaultEvent("service_memory_leak", "svc0", 0, 80, 2)])
    collector = Collector(summarize_topology(n.topology), SLOConfig())
    for _ in range(40):
        obs = collector.observe(n.step())
    assert obs.metrics.node_memory_utilisation["edge0"] == .9
    agent = LLMAgent(None)
    agent.set_context(context_from_sim(n))
    assert agent._node_metrics("edge0", obs)["memory_utilisation"] == .9
    assert agent._node_metrics("edge0", obs)["service_memory"]["svc0"] == 85
    # Isolate availability from ordinary stochastic latency-threshold crossings.
    slo = SLOConfig(p95_target_ms=100000)
    hold = rollout(n, NoOp(), 40, FAULT_MODE_SCHEDULED, slo_config=slo)
    restart = rollout(n, RestartService(service_id="svc0"), 40, FAULT_MODE_SCHEDULED, slo_config=slo)
    assert hold.violation_ticks > restart.violation_ticks
    assert hold.service_violation_ticks["svc0"] >= 35
    assert restart.service_violation_ticks.get("svc0", 0) == 3
    assert hold.violation_ticks == 70
    assert restart.violation_ticks == 3


def test_tree_feasibility_and_counterfactual_category():
    n = sim([FaultEvent("link_failure", "l_gw_edge0", 0, 20, 1)])
    assert len(n.state.links) == 16
    assert not has_alternative_path(n.state, "svc0")
    cfg = ExperimentConfig()
    cfg.twin.horizon_ticks = 1
    for action in [NoOp(), RerouteTraffic(service_id="svc0", path_hint=n.state.routes["svc0"])]:
        record = evaluate_decision(n, [dict(action=action, retry_index=0,
                                           was_applied=False, verdict=None)], cfg)[0]
        assert record.reroute_feasibility["svc0"] == "topology-infeasible"
    redundant = sim(redundant=True)
    assert has_alternative_path(redundant.state, "svc0")
    reroutes = [a for a in candidates(redundant, ActionsConfig()) if isinstance(a, RerouteTraffic)]
    assert reroutes
    assert all(validate_action(a, redundant.state, ActionsConfig()).valid for a in reroutes)


def test_privileged_enumerator_finds_repair_without_mutating_parent():
    n = sim([FaultEvent("node_crash", "edge0", 0, 40, 1)])
    n.step()
    before = n.snapshot()
    report = enumerate_opportunities(n, horizon=25)
    assert n.snapshot() == before
    assert report.diagnostic_only and report.beneficial_opportunity
    assert report.benefit_vs_hold > 0
    assert report.best_sequence[0]["action"]["type"] == "migrate_service"
    assert report.best_violation_ticks < report.hold_violation_ticks


def test_enumerator_depth_two_and_negative_control():
    # Small zero-demand fixture: no action can create completions, so hold ties best.
    config = SimConfig()
    topology = build_topology(TopologyConfig(n_edge_servers=1, n_devices=1, n_services=1),
                              config, arrival_rate=0)
    n = NetworkSim(topology, config, seed=0)
    report = enumerate_opportunities(n, horizon=5, max_depth=2, sequence_interval=2)
    assert report.candidates_evaluated > 1
    assert report.hold_violation_ticks == report.best_violation_ticks == 5
    assert not report.beneficial_opportunity
    assert report.best_sequence[0]["action"]["type"] == "no_op"


def test_depth_two_finds_two_service_recovery():
    config = SimConfig()
    topology = build_topology(TopologyConfig(n_edge_servers=2, n_devices=2, n_services=2), config)
    topology.services[1].host_node_id = "edge0"
    topology.routes["svc1"] = ["l_gw_dev1", "l_gw_edge0"]
    n = NetworkSim(topology, config, seed=0, schedule=FaultSchedule([
        FaultEvent("node_crash", "edge0", 0, 20, 1)]))
    n.step()
    actions = ActionsConfig(migration_min_downtime=1, migration_downtime_per_mem=0)
    slo = SLOConfig(p95_target_ms=100000)
    single = enumerate_opportunities(n, horizon=6, actions_config=actions, slo_config=slo)
    pair = enumerate_opportunities(n, horizon=6, actions_config=actions, slo_config=slo,
                                   max_depth=2, sequence_interval=1)
    assert single.hold_violation_ticks == pair.hold_violation_ticks == 12
    assert single.best_violation_ticks == 7
    assert pair.best_violation_ticks == 3
    assert len(pair.best_sequence) == 2
