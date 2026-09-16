"""Exact fixtures and conservation laws, independent of simulator rollouts."""
import json
import math
from dataclasses import asdict, dataclass

import pytest

from twinloop.actions.executor import execute_action
from twinloop.actions.schema import MigrateService, RerouteTraffic, RestartService, ThrottleService
from twinloop.actions.validator import validate_action
from twinloop.agent.base import context_from_sim
from twinloop.agent.llm_agent import LLMAgent
from twinloop.config import ActionsConfig, SimConfig, SLOConfig, TopologyConfig
from twinloop.faults.schedule import FaultEvent, FaultSchedule
from twinloop.sim.engine import NetworkSim, Topology, build_topology
from twinloop.sim.metrics import TickMetrics
from twinloop.sim.routing import has_alternative_path, path_error, paths
from twinloop.sim.service import Request
from twinloop.telemetry.collector import Collector, summarize_topology
from twinloop.telemetry.slo import SLOEvaluator


@dataclass
class FixedWorkload:
    count: int

    def sample(self, generator, tick_seconds):
        return self.count

    def mean_rate(self):
        return float(self.count)

    def copy(self):
        return FixedWorkload(self.count)


def network(*, arrivals=0, events=(), redundant=False, queue_cap=128, services=1):
    cfg = SimConfig(queue_cap=queue_cap)
    top = build_topology(TopologyConfig(n_services=services, redundant_links=redundant), cfg)
    top.workloads = {s.id: FixedWorkload(arrivals) for s in top.services}
    return NetworkSim(top, cfg, seed=23, schedule=FaultSchedule(list(events)))


@pytest.mark.parametrize("loss", ["backlog", "overflow", "zero_cap", "restart", "migration",
    "crash", "link", "memory", "throttle", "combined"])
def test_request_conservation_for_every_loss_path(loss):
    sim = network(arrivals=4 if loss != "backlog" else 0,
                  queue_cap=0 if loss == "zero_cap" else 1)
    svc = sim.state.services["svc0"]
    svc.queue = [Request(0), Request(0)]
    svc.in_service = Request(0, work=200)
    if loss == "crash":
        sim.state.nodes["edge0"].status = "down"
    if loss in {"link", "combined"}:
        sim.state.links["l_gw_dev0"].status = "down"
    if loss == "memory":
        svc.mem_footprint = 101
    if loss in {"throttle", "combined"}:
        assert execute_action(sim, ThrottleService(service_id="svc0", rate_limit=2), ActionsConfig()).success
    if loss in {"restart", "combined"}:
        assert execute_action(sim, RestartService(service_id="svc0"), ActionsConfig()).success
    if loss == "migration":
        assert execute_action(sim, MigrateService(service_id="svc0", target_node_id="edge1"), ActionsConfig()).success
    opening = 3  # Two waiting requests and one request already processing.
    first = None
    for _ in range(4):
        metric = sim.step()
        first = first or metric
        assert opening + metric.service_arrivals["svc0"] == (
            metric.service_completed["svc0"] + metric.service_dropped["svc0"]
            + metric.service_outstanding["svc0"])
        rate = metric.service_drop_rate["svc0"]
        assert math.isfinite(rate) and 0 <= rate <= 1
        opening = metric.service_outstanding["svc0"]
    if loss in {"restart", "migration", "crash", "memory", "combined"}:
        assert first.service_dropped["svc0"] == 7
    if loss == "zero_cap":
        assert first.service_dropped["svc0"] == 6
        assert first.service_outstanding["svc0"] == 1


@pytest.mark.parametrize("kind", ["restart", "migration"])
def test_pending_losses_snapshot_restore_count_exactly_once(kind):
    sim = network()
    sim.state.services["svc0"].queue = [Request(0), Request(0)]
    sim.state.services["svc0"].in_service = Request(0, work=200)
    action = RestartService(service_id="svc0") if kind == "restart" else MigrateService(service_id="svc0", target_node_id="edge1")
    execute_action(sim, action, ActionsConfig())
    before = sim.snapshot()
    child = sim.fork()
    a = sim.step()
    assert a.service_dropped["svc0"] == 3
    assert child.step().service_dropped["svc0"] == 3
    sim.restore(before)
    assert sim.step() == a
    assert sim.step().service_dropped["svc0"] == 0
    assert before.services["svc0"].pending_request_losses == 3


@pytest.mark.parametrize("windows,expected", [
    ([(0, 2), (0, 4)], ["down"] * 4 + ["degraded"] * 2),
    ([(0, 4), (2, 2)], ["down"] * 4 + ["degraded"] * 2),
    ([(0, 2), (2, 2)], ["down"] * 4 + ["degraded"] * 2),
    ([(0, 2), (1, 3), (3, 2)], ["down"] * 5 + ["degraded"]),
])
@pytest.mark.parametrize("reverse", [False, True])
def test_crash_windows_exact_union_and_baseline_cleanup(windows, expected, reverse):
    events = [FaultEvent("node_crash", "edge0", start, duration, 1) for start, duration in windows]
    sim = network(events=events[::-1] if reverse else events)
    sim.state.nodes["edge0"].status = "degraded"
    sim.step()
    checkpoint = sim.snapshot()
    child = sim.fork()
    actual = [sim.state.nodes["edge0"].status]
    for _ in range(5):
        assert sim.step() == child.step()
        actual.append(sim.state.nodes["edge0"].status)
    assert actual == expected
    assert not sim.state.active_faults and not sim.state.fault_baselines
    sim.restore(checkpoint)
    restored = []
    for _ in range(5):
        sim.step()
        restored.append(sim.state.nodes["edge0"].status)
    assert restored == expected[1:]


@pytest.mark.parametrize("reverse", [False, True])
def test_three_link_degradations_preserve_nondefault_baseline(reverse):
    events = [FaultEvent("link_degradation", "l_gw_edge0", start, duration, magnitude)
              for start, duration, magnitude in [(0, 3, 2), (0, 2, 3), (1, 2, 4)]]
    sim = network(events=events[::-1] if reverse else events)
    link = sim.state.links["l_gw_edge0"]
    link.latency_multiplier, link.loss_rate = 1.5, .05
    values = []
    for _ in range(4):
        sim.step()
        values.append((link.latency_multiplier, round(link.loss_rate, 2)))
    assert values == [(9, .55), (36, .95), (12, .65), (1.5, .05)]
    assert not sim.state.active_faults and not sim.state.fault_baselines


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("kind", ["node_cpu_saturation", "traffic_surge", "link_failure"])
def test_three_same_type_contributions_exact_trace(kind, reverse):
    target = {"node_cpu_saturation": "edge0", "traffic_surge": "svc0", "link_failure": "l_gw_edge0"}[kind]
    events = [FaultEvent(kind, target, start, duration, magnitude)
              for start, duration, magnitude in [(0, 3, 2), (0, 2, 3), (1, 2, 4)]]
    sim = network(events=events[::-1] if reverse else events)
    if kind == "traffic_surge":
        from twinloop.sim.workload import PoissonWorkload
        sim.state.workloads["svc0"] = PoissonWorkload(10)
    sim.state.nodes["edge0"].cpu_reserved = 10
    actual = []
    for _ in range(4):
        sim.step()
        if kind == "node_cpu_saturation":
            actual.append(sim.state.nodes["edge0"].cpu_reserved)
        elif kind == "traffic_surge":
            actual.append(sim.state.workloads["svc0"].rate)
        else:
            actual.append(sim.state.links["l_gw_edge0"].status)
    expected = {"node_cpu_saturation": [98, 98, 98, 10],
                "traffic_surge": [60, 240, 80, 10], "link_failure": ["down"] * 3 + ["up"]}
    assert actual == expected[kind]
    assert not sim.state.active_faults and not sim.state.fault_baselines


@pytest.mark.parametrize("magnitudes", [[2], [2, 3], [2, 3, 4]])
def test_restart_during_leaks_resets_all_then_regrows(magnitudes):
    sim = network(events=[FaultEvent("service_memory_leak", "svc0", 0, 5, m) for m in magnitudes])
    sim.step()
    assert sim.state.services["svc0"].mem_footprint == 5 + sum(magnitudes)
    execute_action(sim, RestartService(service_id="svc0"), ActionsConfig(restart_downtime=1))
    sim.step()
    assert sim.state.services["svc0"].mem_footprint == 5
    sim.step()
    assert sim.state.services["svc0"].mem_footprint == 5 + sum(magnitudes)
    for _ in range(3):
        sim.step()
    assert sim.state.services["svc0"].mem_footprint == 5
    assert not sim.state.active_faults and not sim.state.fault_baselines


@pytest.mark.parametrize("path", [[], ["ghost"], ["l_gw_dev1", "l_gw_edge0"],
    ["l_gw_edge0", "l_gw_dev0"], ["l_gw_dev0", "l_gw_edge1", "l_gw_edge0"],
    ["l_gw_dev0", "l_gw_dev0", "l_gw_dev0", "l_gw_edge0"],
    ["l_gw_dev0", "l_gw_edge1"]])
def test_bad_routes_have_no_side_effect(path):
    sim = network()
    before = sim.snapshot()
    action = RerouteTraffic(service_id="svc0", path_hint=path)
    assert not validate_action(action, sim.state, ActionsConfig()).valid
    assert not execute_action(sim, action, ActionsConfig()).success
    assert sim.snapshot() == before


def test_current_route_is_valid_idempotent_and_down_path_is_rejected():
    sim = network()
    action = RerouteTraffic(service_id="svc0", path_hint=sim.state.routes["svc0"])
    before = sim.snapshot()
    assert execute_action(sim, action, ActionsConfig()).success
    assert sim.snapshot() == before
    sim.state.links["l_gw_edge0"].status = "down"
    assert not validate_action(action, sim.state, ActionsConfig()).valid


def test_tree_mesh_and_physically_present_but_failed_alternatives():
    tree = network()
    assert list(paths(tree.state, "dev0", "edge0")) == [["l_gw_dev0", "l_gw_edge0"]]
    assert not has_alternative_path(tree.state, "svc0")
    mesh = network(redundant=True)
    alternatives = list(paths(mesh.state, "dev0", "edge0"))
    assert len(alternatives) >= 3
    assert all(not path_error(mesh.state, p, "dev0", "edge0") for p in alternatives)
    for lid, link in mesh.state.links.items():
        if lid not in mesh.state.routes["svc0"]:
            link.status = "down"
    assert has_alternative_path(mesh.state, "svc0", operational=False)
    assert not has_alternative_path(mesh.state, "svc0", operational=True)


def test_down_transit_node_is_not_operational():
    sim = network(arrivals=4)
    sim.state.nodes["gw0"].status = "down"
    assert list(paths(sim.state, "dev0", "edge0")) == []
    assert list(paths(sim.state, "dev0", "edge0", operational=False))
    assert path_error(sim.state, sim.state.routes["svc0"], "dev0", "edge0")
    metrics = sim.step()
    assert metrics.service_dropped["svc0"] == 4
    assert metrics.service_completed["svc0"] == 0


@pytest.mark.parametrize("kind,target", [("node_crash", "edge1"), ("link_failure", "l_gw_edge1")])
def test_destination_failure_on_completion_retains_old_route(kind, target):
    sim = network(events=[FaultEvent(kind, target, 1, 4, 1)])
    before = sim.snapshot()
    child = sim.fork()
    execute_action(child, MigrateService(service_id="svc0", target_node_id="edge1"),
                   ActionsConfig(migration_min_downtime=2, migration_downtime_per_mem=0))
    child.step()
    child.step()
    assert child.state.services["svc0"].host_node_id == "edge0"
    assert child.state.routes["svc0"] == ["l_gw_dev0", "l_gw_edge0"]
    assert not child.state.pending
    assert sim.snapshot() == before


@pytest.mark.parametrize("kind", ["restart", "migration"])
@pytest.mark.parametrize("boundary", ["begins", "ends"])
def test_one_tick_downtime_at_fault_boundary(kind, boundary):
    # Host crash begins/ends on the unavailable processing tick; completion is at its end.
    events = [FaultEvent("node_crash", "edge0", 1 if boundary == "begins" else 0, 1, 1)]
    sim = network(events=events, arrivals=4)
    sim.step()
    action = RestartService(service_id="svc0") if kind == "restart" else MigrateService(service_id="svc0", target_node_id="edge1")
    execute_action(sim, action, ActionsConfig(restart_downtime=1, migration_min_downtime=1, migration_downtime_per_mem=0))
    assert sim.state.services["svc0"].status == "down"
    metrics = sim.step()
    assert metrics.service_completed["svc0"] == 0
    assert metrics.service_dropped["svc0"] == 4
    assert sim.state.services["svc0"].status == "healthy"
    assert not sim.state.pending
    assert sim.step().service_completed["svc0"] == 4


@pytest.mark.parametrize("memory,replicas,pressure", [(100, 1, False), (100.0001, 1, True),
    (50, 2, False), (50.0001, 2, True)])
def test_exact_memory_capacity_boundary(memory, replicas, pressure):
    sim = network()
    service = sim.state.services["svc0"]
    service.mem_footprint, service.replicas = memory, replicas
    service.in_service = Request(0, work=1)
    metrics = sim.step()
    assert metrics.service_dropped["svc0"] == int(pressure)
    assert metrics.service_completed["svc0"] == int(not pressure)
    assert metrics.node_memory_utilisation["edge0"] == memory * replicas / 100


def test_joint_memory_pressure_migration_and_telemetry_agree():
    sim = network(arrivals=4, services=2)
    for service in sim.state.services.values():
        service.host_node_id = "edge0"
        service.mem_footprint = 60
        sim.state.routes[service.id] = [f"l_gw_{service.source_node_id}", "l_gw_edge0"]
    collector = Collector(summarize_topology(sim.topology), SLOConfig())
    previous = collector.observe(sim.step())
    assert previous.metrics.service_dropped == {"svc0": 4, "svc1": 4}
    execute_action(sim, MigrateService(service_id="svc0", target_node_id="edge1"),
                   ActionsConfig(migration_min_downtime=1, migration_downtime_per_mem=0))
    obs = collector.observe(sim.step())
    assert obs.topology.service_hosts["svc0"] == "edge1"
    assert previous.topology.service_hosts["svc0"] == "edge0"
    assert obs.metrics.node_memory_utilisation["edge0"] == .6
    assert obs.metrics.node_memory_utilisation["edge1"] == .6
    assert sim.state.nodes["edge1"].mem_used == 60
    agent = LLMAgent(None)
    agent.set_context(context_from_sim(sim))
    node = agent._node_metrics("edge1", obs)
    assert node["memory_utilisation"] == sum(node["service_memory"].values()) / node["mem_capacity"]
    assert sim.state.routes["svc0"] == ["l_gw_dev0", "l_gw_edge1"]
    assert sim.step().service_completed == {"svc0": 4, "svc1": 4}


def test_restart_completion_memory_sensor_matches_live_state():
    sim = network()
    sim.state.services["svc0"].mem_footprint = 101
    execute_action(sim, RestartService(service_id="svc0"), ActionsConfig(restart_downtime=1))
    metrics = sim.step()
    assert sim.state.services["svc0"].mem_footprint == 5
    assert metrics.node_memory_utilisation["edge0"] == .05
    assert sim.state.nodes["edge0"].mem_used == 5


@pytest.mark.parametrize("arrivals,backlog", [(4, False), (0, False), (0, True)])
def test_no_completions_are_missing_not_zero_latency(arrivals, backlog):
    sim = network(arrivals=arrivals)
    if backlog:
        sim.state.services["svc0"].queue = [Request(0)]
    if arrivals or backlog:
        sim.state.nodes["edge0"].status = "down"
    metric = sim.step()
    assert metric.service_p50["svc0"] is None and metric.service_p95["svc0"] is None
    encoded = json.dumps(asdict(metric), allow_nan=False)
    assert json.loads(encoded)["service_p95"]["svc0"] is None
    status = SLOEvaluator(SLOConfig()).evaluate(metric)["svc0"]
    assert not status.compliant
    assert status.availability == (0 if arrivals or backlog else 1)


def test_local_empty_route_is_valid_but_unknown_endpoint_is_not():
    sim = network()
    assert path_error(sim.state, [], "edge0", "edge0") == ""
    assert list(paths(sim.state, "edge0", "edge0")) == [[]]
    assert path_error(sim.state, [], "unknown", "unknown")
    assert list(paths(sim.state, "unknown", "edge0")) == []


def test_slo_exact_thresholds_empty_metrics_and_counts():
    evaluator = SLOEvaluator(SLOConfig(p95_target_ms=600, availability_target=.99))
    assert evaluator.evaluate(TickMetrics(tick=0)) == {}
    metric = TickMetrics(tick=1, service_p95={"a": .6, "b": .600001, "c": None},
        service_throughput={"a": 1, "b": 1, "c": 0}, service_drop_rate={"a": .01, "b": .010001, "c": 0})
    statuses = evaluator.evaluate(metric)
    assert statuses["a"].compliant
    assert not statuses["b"].p95_ok and not statuses["b"].availability_ok
    assert not statuses["c"].compliant
    evaluator.evaluate(metric)
    assert evaluator.violation_ticks == {"b": 2, "c": 2}
    assert evaluator.total_violation_ticks == 4


@pytest.mark.parametrize("redundant", [False, True])
@pytest.mark.parametrize("services", [1, 5])
def test_minimum_topology_and_service_wraparound(redundant, services):
    cfg = SimConfig()
    top = build_topology(TopologyConfig(n_edge_servers=1, n_devices=1, n_services=services,
                                       redundant_links=redundant), cfg)
    sim = NetworkSim(top, cfg, seed=2)
    assert len(sim.state.nodes) == 3 and len(sim.state.services) == services
    for service in sim.state.services.values():
        assert service.source_node_id == "dev0" and service.host_node_id == "edge0"
        assert not path_error(sim.state, sim.state.routes[service.id], "dev0", "edge0")
    assert has_alternative_path(sim.state, "svc0") is redundant


@pytest.mark.parametrize("field", ["nodes", "links", "services"])
def test_duplicate_topology_ids_rejected(field):
    top = build_topology(TopologyConfig(), SimConfig())
    data = vars(top).copy()
    data[field] = list(data[field]) + [data[field][0]]
    with pytest.raises(ValueError, match="duplicate"):
        Topology(**data)
