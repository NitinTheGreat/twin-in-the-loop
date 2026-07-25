from twinloop.actions.executor import execute_action
from twinloop.actions.schema import MigrateService, NoOp
from twinloop.actions.validator import validate_action
from twinloop.agent.base import context_from_sim
from twinloop.agent.null_agent import NullAgent
from twinloop.agent.rule_agent import RuleAgent
from twinloop.config import ActionsConfig, RuleAgentConfig, SimConfig, SLOConfig, TopologyConfig
from twinloop.faults.schedule import FaultEvent, FaultSchedule
from twinloop.sim.engine import NetworkSim, Topology, build_topology
from twinloop.sim.link import Link
from twinloop.sim.node import Node
from twinloop.sim.service import Service
from twinloop.sim.workload import PoissonWorkload
from twinloop.telemetry.collector import Collector, summarize_topology


def _recovery_topology(rate=25.0):
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
    routes = {"svcX": ["l_dev", "l_busy"]}
    workloads = {"svcX": PoissonWorkload(rate)}
    return Topology([gateway, busy, spare, device], links, [svc], routes, workloads)


def _drive(sim, agent, ticks, actions_config=None, slo_config=None):
    actions_config = actions_config or ActionsConfig()
    slo_config = slo_config or SLOConfig()
    collector = Collector(summarize_topology(sim.topology), slo_config)
    decisions = []
    observations = []
    for _ in range(ticks):
        metrics = sim.step()
        obs = collector.observe(metrics)
        observations.append(obs)
        agent.set_context(context_from_sim(sim))
        action = agent.decide(obs, None)
        valid = validate_action(action, sim.state, actions_config).valid
        if not isinstance(action, NoOp):
            execute_action(sim, action, actions_config)
        decisions.append((obs.tick, action, valid))
    return decisions, observations


def _all_faults():
    return FaultSchedule(
        [
            FaultEvent("node_cpu_saturation", "edge0", 20, 30, 4.0),
            FaultEvent("node_crash", "edge1", 60, 25, 1.0),
            FaultEvent("link_degradation", "l_gw_edge2", 100, 30, 3.0),
            FaultEvent("link_failure", "l_gw_edge3", 140, 25, 1.0),
            FaultEvent("service_memory_leak", "svc0", 180, 40, 8.0),
            FaultEvent("traffic_surge", "svc2", 230, 30, 3.0),
        ]
    )


def test_null_agent_always_no_op():
    sim = NetworkSim(
        build_topology(TopologyConfig(), SimConfig()), SimConfig(), seed=1, schedule=_all_faults()
    )
    decisions, _ = _drive(sim, NullAgent(), 280)
    assert all(isinstance(action, NoOp) for _, action, _ in decisions)


def test_rule_agent_recovers_cpu_saturation():
    schedule = FaultSchedule([FaultEvent("node_cpu_saturation", "busy", 20, 120, 4.0)])
    sim = NetworkSim(_recovery_topology(), SimConfig(), seed=2, schedule=schedule)
    agent = RuleAgent()
    decisions, observations = _drive(sim, agent, 110)

    assert any(not o.slo_status["svcX"].compliant for o in observations[:60])
    assert any(
        isinstance(action, MigrateService) and action.service_id == "svcX"
        for _, action, _ in decisions
    )
    assert all(o.slo_status["svcX"].compliant for o in observations[-15:])


def test_rule_agent_does_not_thrash():
    schedule = FaultSchedule([FaultEvent("node_cpu_saturation", "busy", 20, 120, 4.0)])
    sim = NetworkSim(_recovery_topology(), SimConfig(), seed=2, schedule=schedule)
    agent = RuleAgent()
    decisions, _ = _drive(sim, agent, 110)

    migrate_ticks = [
        tick
        for tick, action, _ in decisions
        if isinstance(action, MigrateService) and action.service_id == "svcX"
    ]
    assert migrate_ticks
    first = migrate_ticks[0]
    cooldown = agent.config.cooldown_ticks
    for tick, action, _ in decisions:
        if first < tick < first + cooldown:
            assert isinstance(action, NoOp) or action.service_id != "svcX"


def test_rule_agent_ignores_single_tick_spike():
    schedule = FaultSchedule([FaultEvent("link_degradation", "l_gw_edge2", 30, 1, 4.0)])
    sim = NetworkSim(
        build_topology(TopologyConfig(), SimConfig(), arrival_rate=15.0),
        SimConfig(),
        seed=3,
        schedule=schedule,
    )
    decisions, observations = _drive(sim, RuleAgent(), 80)
    spikes = sum(1 for o in observations if o.metrics.link_latency["l_gw_edge2"] > 12.0)
    assert spikes == 1
    assert all(isinstance(action, NoOp) for _, action, _ in decisions)


def test_rule_agent_no_op_when_healthy():
    topology = build_topology(TopologyConfig(), SimConfig(), arrival_rate=15.0)
    sim = NetworkSim(topology, SimConfig(), seed=4)
    decisions, _ = _drive(sim, RuleAgent(), 200)
    assert all(isinstance(action, NoOp) for _, action, _ in decisions)


def test_rule_agent_emits_only_valid_actions_across_seeds():
    for seed in range(8):
        sim = NetworkSim(
            build_topology(TopologyConfig(), SimConfig()),
            SimConfig(),
            seed=seed,
            schedule=_all_faults(),
        )
        decisions, _ = _drive(sim, RuleAgent(), 280)
        assert all(valid for _, _, valid in decisions)
        assert any(not isinstance(action, NoOp) for _, action, _ in decisions)


def test_rule_agent_deterministic():
    def _run(seed):
        sim = NetworkSim(
            build_topology(TopologyConfig(), SimConfig()),
            SimConfig(),
            seed=seed,
            schedule=_all_faults(),
        )
        decisions, _ = _drive(sim, RuleAgent(), 280)
        return [(tick, type(action).__name__, getattr(action, "service_id", None)) for tick, action, _ in decisions]

    assert _run(5) == _run(5)
