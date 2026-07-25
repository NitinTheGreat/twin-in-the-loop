import numpy as np

from twinloop.config import FaultConfig, SimConfig, TopologyConfig
from twinloop.faults.schedule import FaultEvent, FaultSchedule, targets_from_topology
from twinloop.sim.engine import NetworkSim, build_topology


def _topology():
    return build_topology(TopologyConfig(), SimConfig())


def _sim(schedule=None, seed=3):
    return NetworkSim(_topology(), SimConfig(), seed=seed, schedule=schedule)


def _collect(sim, ticks):
    return [sim.step() for _ in range(ticks)]


def _event(fault_type, target, magnitude, start=20, duration=30):
    return FaultEvent(
        type=fault_type,
        target=target,
        start_tick=start,
        duration=duration,
        magnitude=magnitude,
    )


def test_schedule_deterministic_from_seed():
    targets = targets_from_topology(_topology())
    config = FaultConfig()
    same_a = FaultSchedule.generate(123, config, targets).events
    same_b = FaultSchedule.generate(123, config, targets).events
    other = FaultSchedule.generate(999, config, targets).events
    assert same_a == same_b
    assert same_a != other


def test_schedule_independent_of_sim_state():
    topology = _topology()
    targets = targets_from_topology(topology)
    config = FaultConfig()
    fresh = FaultSchedule.generate(7, config, targets).events

    advanced = NetworkSim(topology, SimConfig(), seed=7)
    _collect(advanced, 200)
    after = FaultSchedule.generate(7, config, targets).events

    assert fresh == after


def _window_mean(metrics, start, duration, selector):
    values = [selector(metrics[t]) for t in range(start + 5, start + duration)]
    return float(np.mean(values))


def test_cpu_saturation_raises_utilisation():
    start, duration = 20, 30
    faulted = _collect(
        _sim(FaultSchedule([_event("node_cpu_saturation", "edge0", 4.0)])),
        start + duration,
    )
    baseline = _collect(_sim(), start + duration)
    f = _window_mean(faulted, start, duration, lambda m: m.node_utilisation["edge0"])
    b = _window_mean(baseline, start, duration, lambda m: m.node_utilisation["edge0"])
    assert f > b + 0.2


def test_node_crash_zeroes_throughput():
    start, duration = 20, 30
    faulted = _collect(
        _sim(FaultSchedule([_event("node_crash", "edge2", 1.0)])),
        start + duration,
    )
    baseline = _collect(_sim(), start + duration)
    f = _window_mean(faulted, start, duration, lambda m: m.service_throughput["svc2"])
    b = _window_mean(baseline, start, duration, lambda m: m.service_throughput["svc2"])
    assert b > 10.0
    assert f < 1.0


def test_link_degradation_raises_latency():
    start, duration = 20, 30
    faulted = _collect(
        _sim(FaultSchedule([_event("link_degradation", "l_gw_edge0", 3.0)])),
        start + duration,
    )
    baseline = _collect(_sim(), start + duration)
    f = _window_mean(faulted, start, duration, lambda m: m.link_latency["l_gw_edge0"])
    b = _window_mean(baseline, start, duration, lambda m: m.link_latency["l_gw_edge0"])
    assert f > b * 2.0


def test_link_failure_drops_traffic():
    start, duration = 20, 30
    faulted = _collect(
        _sim(FaultSchedule([_event("link_failure", "l_gw_edge2", 1.0)])),
        start + duration,
    )
    f = _window_mean(faulted, start, duration, lambda m: m.service_drop_rate["svc2"])
    assert f > 0.9


def test_memory_leak_grows_footprint():
    start, duration = 20, 30
    sim = _sim(FaultSchedule([_event("service_memory_leak", "svc2", 5.0)]))
    footprints = []
    for _ in range(start + duration):
        sim.step()
        footprints.append(sim.state.services["svc2"].mem_footprint)
    assert footprints[start + duration - 1] > footprints[start - 1] + 50.0


def test_traffic_surge_raises_throughput():
    start, duration = 20, 30
    faulted = _collect(
        _sim(FaultSchedule([_event("traffic_surge", "svc2", 3.0)])),
        start + duration,
    )
    baseline = _collect(_sim(), start + duration)
    f = _window_mean(faulted, start, duration, lambda m: m.service_throughput["svc2"])
    b = _window_mean(baseline, start, duration, lambda m: m.service_throughput["svc2"])
    assert f > b + 10.0


def test_all_faults_revert_cleanly():
    start, duration = 20, 30
    cases = [
        (
            _event("node_cpu_saturation", "edge0", 4.0),
            lambda s: (s.nodes["edge0"].cpu_reserved, s.nodes["edge0"].status),
            (0.0, "healthy"),
        ),
        (
            _event("node_crash", "edge2", 1.0),
            lambda s: s.nodes["edge2"].status,
            "healthy",
        ),
        (
            _event("link_degradation", "l_gw_edge0", 3.0),
            lambda s: (s.links["l_gw_edge0"].latency_multiplier, s.links["l_gw_edge0"].loss_rate),
            (1.0, 0.0),
        ),
        (
            _event("link_failure", "l_gw_edge2", 1.0),
            lambda s: s.links["l_gw_edge2"].status,
            "up",
        ),
        (
            _event("service_memory_leak", "svc2", 5.0),
            lambda s: s.services["svc2"].mem_footprint,
            5.0,
        ),
        (
            _event("traffic_surge", "svc2", 3.0),
            lambda s: s.workloads["svc2"].rate,
            25.0,
        ),
    ]
    for event, selector, expected in cases:
        sim = _sim(FaultSchedule([event]))
        _collect(sim, start + duration + 1)
        assert selector(sim.state) == expected
        assert sim.state.active_faults == {}


def test_fault_in_fork_does_not_affect_parent():
    schedule = FaultSchedule([_event("node_crash", "edge0", 1.0, start=10, duration=20)])
    sim = _sim(schedule)
    _collect(sim, 5)
    pre = sim.snapshot()

    child = sim.fork()
    _collect(child, 20)

    after = sim.snapshot()
    assert after == pre
