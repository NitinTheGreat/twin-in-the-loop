import numpy as np

from twinloop.actions.executor import ActionResult, execute_action
from twinloop.actions.schema import (
    MigrateService,
    NoOp,
    ParseError,
    RerouteTraffic,
    RestartService,
    ScaleService,
    ThrottleService,
    parse_action,
)
from twinloop.actions.validator import validate_action
from twinloop.config import ActionsConfig, SimConfig, TopologyConfig
from twinloop.faults.schedule import FaultEvent, FaultSchedule
from twinloop.sim.engine import NetworkSim, Topology, build_topology
from twinloop.sim.link import Link
from twinloop.sim.node import Node
from twinloop.sim.service import Service
from twinloop.sim.workload import PoissonWorkload


def _steps(sim, n):
    return [sim.step() for _ in range(n)]


def _mean(metrics, key, sid):
    return float(np.mean([getattr(m, key)[sid] for m in metrics]))


def _two_node_topology(rate=40.0):
    gateway = Node("gw0", "gateway", 100.0, 100.0)
    busy = Node("busy", "edge", 100.0, 100.0)
    free = Node("free", "edge", 100.0, 100.0)
    device = Node("dev0", "device", 10.0, 10.0)
    svc_a = Service("svcA", "busy", 1.0, 5.0)
    svc_n = Service("svcN", "busy", 1.0, 5.0)
    links = [
        Link("l_busy", ("gw0", "busy"), 1000.0, 5.0, 5.0),
        Link("l_free", ("gw0", "free"), 1000.0, 5.0, 5.0),
        Link("l_dev", ("gw0", "dev0"), 1000.0, 5.0, 5.0),
    ]
    routes = {"svcA": ["l_dev", "l_busy"], "svcN": ["l_dev", "l_busy"]}
    workloads = {"svcA": PoissonWorkload(rate), "svcN": PoissonWorkload(rate)}
    return Topology([gateway, busy, free, device], links, [svc_a, svc_n], routes, workloads)


def _single_service_topology(rate=90.0, capacity=100.0):
    gateway = Node("gw0", "gateway", capacity, 100.0)
    edge = Node("edge0", "edge", capacity, 100.0)
    device = Node("dev0", "device", 10.0, 10.0)
    svc = Service("svc0", "edge0", 1.0, 5.0)
    links = [
        Link("l_edge", ("gw0", "edge0"), 1000.0, 5.0, 5.0),
        Link("l_dev", ("gw0", "dev0"), 1000.0, 5.0, 5.0),
    ]
    routes = {"svc0": ["l_dev", "l_edge"]}
    workloads = {"svc0": PoissonWorkload(rate)}
    return Topology([gateway, edge, device], links, [svc], routes, workloads)


def test_no_op_changes_nothing():
    sim = NetworkSim(build_topology(TopologyConfig(), SimConfig()), SimConfig(), seed=1)
    _steps(sim, 10)
    before = sim.snapshot()
    result = execute_action(sim, NoOp(), ActionsConfig())
    assert result.success and result.action_type == "no_op" and result.cost == 0.0
    assert sim.snapshot() == before


def test_reroute_mutates_route():
    sim = NetworkSim(build_topology(TopologyConfig(), SimConfig()), SimConfig(), seed=1)
    action = RerouteTraffic(service_id="svc2", path_hint=["l_gw_dev0", "l_gw_edge2"])
    assert validate_action(action, sim.state, ActionsConfig()).valid
    execute_action(sim, action, ActionsConfig())
    assert sim.state.routes["svc2"] == ["l_gw_dev0", "l_gw_edge2"]


def test_migration_downtime_then_improvement():
    config = ActionsConfig()
    sim = NetworkSim(_two_node_topology(rate=40.0), SimConfig(), seed=2)
    warm = _steps(sim, 60)
    baseline_p95 = _mean(warm[-20:], "service_p95", "svcA")

    action = MigrateService(service_id="svcA", target_node_id="free")
    assert validate_action(action, sim.state, config).valid
    result = execute_action(sim, action, config)
    downtime = result.details["downtime"]
    assert result.cost > 0.0

    during = _steps(sim, downtime - 1)
    assert all(m.service_throughput["svcA"] == 0.0 for m in during)
    assert during[-1].service_drop_rate["svcA"] > 0.9

    after = _steps(sim, 60)
    after_p95 = _mean(after[-20:], "service_p95", "svcA")
    assert after_p95 < baseline_p95


def test_restart_clears_leak_and_leak_returns_if_fault_active():
    config = ActionsConfig()
    schedule = FaultSchedule([FaultEvent("service_memory_leak", "svc0", 5, 400, 5.0)])
    sim = NetworkSim(
        build_topology(TopologyConfig(), SimConfig()), SimConfig(), seed=3, schedule=schedule
    )
    _steps(sim, 40)
    leaked = sim.state.services["svc0"].mem_footprint
    assert leaked > 100.0

    result = execute_action(sim, RestartService(service_id="svc0"), config)
    _steps(sim, result.details["downtime"])
    reset = sim.state.services["svc0"].mem_footprint
    assert reset < 30.0

    _steps(sim, 20)
    regrown = sim.state.services["svc0"].mem_footprint
    assert regrown > reset + 20.0


def test_throttle_trades_latency_for_availability():
    config = ActionsConfig()
    sim = NetworkSim(_single_service_topology(rate=90.0), SimConfig(), seed=4)
    warm = _steps(sim, 80)
    before_p95 = _mean(warm[-20:], "service_p95", "svc0")
    before_drop = _mean(warm[-20:], "service_drop_rate", "svc0")

    execute_action(sim, ThrottleService(service_id="svc0", rate_limit=50.0), config)
    after = _steps(sim, 80)
    after_p95 = _mean(after[-20:], "service_p95", "svc0")
    after_drop = _mean(after[-20:], "service_drop_rate", "svc0")

    assert after_p95 < before_p95
    assert after_drop > before_drop


def test_scale_up_improves_contended_service():
    config = ActionsConfig()
    sim = NetworkSim(_two_node_topology(rate=40.0), SimConfig(), seed=5)
    warm = _steps(sim, 60)
    before_p95 = _mean(warm[-20:], "service_p95", "svcA")

    action = ScaleService(service_id="svcA", delta_replicas=1)
    assert validate_action(action, sim.state, config).valid
    execute_action(sim, action, config)
    assert sim.state.services["svcA"].replicas == 2

    after = _steps(sim, 60)
    after_p95 = _mean(after[-20:], "service_p95", "svcA")
    assert after_p95 < before_p95


def test_semantic_validation_rejects_each_invalid_case():
    config = ActionsConfig()
    sim = NetworkSim(build_topology(TopologyConfig(), SimConfig()), SimConfig(), seed=6)
    state = sim.state
    reasons = []

    reasons.append(
        validate_action(
            MigrateService(service_id="ghost", target_node_id="edge1"), state, config
        )
    )
    reasons.append(
        validate_action(
            MigrateService(service_id="svc0", target_node_id="ghostnode"), state, config
        )
    )
    state.nodes["edge1"].status = "down"
    reasons.append(
        validate_action(
            MigrateService(service_id="svc0", target_node_id="edge1"), state, config
        )
    )
    state.nodes["edge1"].status = "healthy"
    state.nodes["edge3"].mem_capacity = 1.0
    reasons.append(
        validate_action(
            MigrateService(service_id="svc0", target_node_id="edge3"), state, config
        )
    )
    reasons.append(
        validate_action(ScaleService(service_id="svc0", delta_replicas=-1), state, config)
    )
    reasons.append(
        validate_action(ScaleService(service_id="svc0", delta_replicas=10), state, config)
    )
    state.links["l_gw_edge2"].status = "down"
    reasons.append(
        validate_action(
            RerouteTraffic(service_id="svc2", path_hint=["l_gw_dev2", "l_gw_edge2"]),
            state,
            config,
        )
    )
    state.links["l_gw_edge2"].status = "up"
    reasons.append(
        validate_action(
            RerouteTraffic(service_id="svc2", path_hint=["l_gw_dev0", "l_gw_edge3"]),
            state,
            config,
        )
    )

    assert all(not r.valid for r in reasons)
    messages = [r.reason for r in reasons]
    assert len(set(messages)) == len(messages)


def test_malformed_schema_rejected_without_raising():
    assert isinstance(parse_action({"type": "nuke_everything"}), ParseError)
    assert isinstance(parse_action({"type": "migrate_service"}), ParseError)
    assert isinstance(
        parse_action(
            {"type": "migrate_service", "service_id": "s", "target_node_id": "n", "x": 1}
        ),
        ParseError,
    )
    assert isinstance(parse_action("not a dict"), ParseError)
    assert isinstance(parse_action({"type": "no_op"}), NoOp)
    good = parse_action(
        {"type": "migrate_service", "service_id": "svc0", "target_node_id": "edge1"}
    )
    assert isinstance(good, MigrateService)
    assert good.service_id == "svc0" and good.target_node_id == "edge1"


def test_action_identical_in_sim_and_fork():
    config = ActionsConfig()
    sim = NetworkSim(_two_node_topology(rate=40.0), SimConfig(), seed=7)
    _steps(sim, 40)
    fork = sim.fork()
    execute_action(sim, MigrateService(service_id="svcA", target_node_id="free"), config)
    execute_action(fork, MigrateService(service_id="svcA", target_node_id="free"), config)
    assert _steps(sim, 50) == _steps(fork, 50)


def test_pending_migration_survives_fork():
    config = ActionsConfig()

    def _at_fork_point(seed):
        sim = NetworkSim(_two_node_topology(rate=40.0), SimConfig(), seed=seed)
        _steps(sim, 40)
        execute_action(
            sim, MigrateService(service_id="svcA", target_node_id="free"), config
        )
        _steps(sim, 3)
        return sim

    control = _at_fork_point(8)
    control_seq = _steps(control, 30)

    sim = _at_fork_point(8)
    child = sim.fork()
    child_seq = _steps(child, 30)
    parent_seq = _steps(sim, 30)

    assert child_seq == control_seq
    assert parent_seq == control_seq
