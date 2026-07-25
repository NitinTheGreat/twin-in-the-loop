from twinloop.config import SimConfig, SLOConfig, TopologyConfig
from twinloop.faults.catalog import FAULT_TYPES
from twinloop.faults.injector import FaultInjector
from twinloop.faults.schedule import FaultEvent, FaultSchedule
from twinloop.sim.engine import NetworkSim, build_topology
from twinloop.sim.metrics import TickMetrics
from twinloop.telemetry.collector import Collector, Observation, summarize_topology
from twinloop.telemetry.slo import SLOEvaluator
from twinloop.telemetry.summarizer import Summarizer


def _all_faults_schedule():
    return FaultSchedule(
        [
            FaultEvent("node_cpu_saturation", "edge0", 20, 20, 4.0),
            FaultEvent("node_crash", "edge1", 50, 20, 1.0),
            FaultEvent("link_degradation", "l_gw_edge2", 80, 20, 3.0),
            FaultEvent("link_failure", "l_gw_edge3", 110, 20, 1.0),
            FaultEvent("service_memory_leak", "svc0", 140, 20, 8.0),
            FaultEvent("traffic_surge", "svc2", 170, 20, 3.0),
        ]
    )


def _pipeline(seed=9, schedule=None, config=None):
    config = config or SLOConfig()
    topology = build_topology(TopologyConfig(), SimConfig())
    sim = NetworkSim(topology, SimConfig(), seed=seed, schedule=schedule)
    collector = Collector(summarize_topology(topology), config)
    summarizer = Summarizer(config)
    return sim, collector, summarizer


def _reachable(obj, seen):
    if id(obj) in seen:
        return
    seen[id(obj)] = obj
    if isinstance(obj, (str, bytes, int, float, bool)) or obj is None:
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            _reachable(key, seen)
            _reachable(value, seen)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for item in obj:
            _reachable(item, seen)
    elif hasattr(obj, "__dict__"):
        for value in vars(obj).values():
            _reachable(value, seen)


def test_summarizer_never_leaks_fault_ground_truth():
    sim, collector, summarizer = _pipeline(schedule=_all_faults_schedule())
    for _ in range(200):
        metrics = sim.step()
        observation = collector.observe(metrics)
        text = summarizer.render(observation)

        for fault_type in FAULT_TYPES:
            assert fault_type not in text
        assert "fault" not in text.lower()

        seen: dict[int, object] = {}
        _reachable(observation, seen)
        for value in seen.values():
            assert not isinstance(value, (FaultEvent, FaultSchedule, FaultInjector))
        assert id(sim.state.active_faults) not in seen


def _metrics(tick, p95, drop, util=None, links=None):
    sids = list(p95)
    return TickMetrics(
        tick=tick,
        service_p50={s: p95[s] * 0.5 for s in sids},
        service_p95=dict(p95),
        service_throughput={s: 25.0 for s in sids},
        service_drop_rate=dict(drop),
        service_queue_len={s: 12 for s in sids},
        node_utilisation=util or {},
        link_latency=links or {},
    )


def test_summary_stays_within_budget():
    config = SLOConfig()
    topology = build_topology(TopologyConfig(), SimConfig())
    collector = Collector(summarize_topology(topology), config)
    sids = [f"svc{i}" for i in range(6)]
    p95 = {s: 1.2 for s in sids}
    drop = {s: 0.5 for s in sids}
    util = {f"edge{i}": 0.99 for i in range(4)}
    observation = None
    for t in range(config.history_window):
        observation = collector.observe(_metrics(t, p95, drop, util))
    summarizer = Summarizer(config)
    text = summarizer.render(observation)
    assert len(text) <= config.summary_char_budget
    assert summarizer.estimated_tokens(text) <= config.summary_char_budget / config.chars_per_token


def test_degradation_keeps_violation_detail():
    config = SLOConfig(summary_char_budget=340)
    topology = build_topology(TopologyConfig(), SimConfig())
    collector = Collector(summarize_topology(topology), config)
    sids = [f"svc{i}" for i in range(6)]
    p95 = {s: (1.2 if s == "svc0" else 0.1) for s in sids}
    drop = {s: (0.5 if s == "svc0" else 0.0) for s in sids}
    observation = collector.observe(_metrics(0, p95, drop))
    text = Summarizer(config).render(observation)

    assert "svc0 (host" in text
    assert "FAIL" in text
    assert "within SLO" in text
    assert "svc1 (host" not in text


def test_slo_detection_and_cumulative_counts():
    config = SLOConfig(p95_target_ms=600.0, availability_target=0.99)
    p95 = {"svcA": 0.5, "svcB": 0.7, "svcC": 0.4}
    drop = {"svcA": 0.0, "svcB": 0.0, "svcC": 0.05}

    single = SLOEvaluator(config).evaluate(_metrics(0, p95, drop))
    assert single["svcA"].compliant
    assert not single["svcB"].compliant and not single["svcB"].p95_ok
    assert single["svcB"].availability_ok
    assert not single["svcC"].compliant and single["svcC"].p95_ok
    assert not single["svcC"].availability_ok

    evaluator = SLOEvaluator(config)
    for t in range(3):
        evaluator.evaluate(_metrics(t, p95, drop))
    assert evaluator.violation_ticks == {"svcB": 3, "svcC": 3}
    assert evaluator.total_violation_ticks == 6


def test_observation_and_summary_deterministic():
    schedule_a = _all_faults_schedule()
    schedule_b = _all_faults_schedule()
    sim_a, col_a, sum_a = _pipeline(seed=13, schedule=schedule_a)
    sim_b, col_b, sum_b = _pipeline(seed=13, schedule=schedule_b)
    for _ in range(120):
        obs_a = col_a.observe(sim_a.step())
        obs_b = col_b.observe(sim_b.step())
        assert obs_a == obs_b
        assert sum_a.render(obs_a) == sum_b.render(obs_b)
