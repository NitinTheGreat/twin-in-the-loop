from twinloop.actions.schema import (
    MigrateService,
    NoOp,
    RerouteTraffic,
    RestartService,
    ScaleService,
    ThrottleService,
)
from twinloop.config import SimConfig, TopologyConfig, TwinConfig
from twinloop.faults.schedule import FaultEvent, FaultSchedule
from twinloop.sim.engine import NetworkSim, Topology, build_topology
from twinloop.sim.link import Link
from twinloop.sim.node import Node
from twinloop.sim.service import Service
from twinloop.sim.workload import PoissonWorkload
from twinloop.twin.fidelity import fidelity_to_config
from twinloop.twin.rollout import FAULT_MODE_BLIND, FAULT_MODE_SCHEDULED, rollout
from twinloop.twin.validator import TwinValidator, feedback_from_verdict


def _mean(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def _mean_p95(metrics):
    return [_mean(m.service_p95.values()) for m in metrics]


def _mae(left, right):
    return _mean(abs(a - b) for a, b in zip(left, right))


def _busy_spare_topology(rate=25.0):
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


def _default_sim(seed=1, schedule=None, advance=25):
    sim = NetworkSim(
        build_topology(TopologyConfig(), SimConfig()), SimConfig(), seed=seed, schedule=schedule
    )
    for _ in range(advance):
        sim.step()
    return sim


def _saturation(target="edge2", start=10, duration=300, magnitude=4.5):
    return FaultSchedule([FaultEvent("node_cpu_saturation", target, start, duration, magnitude)])


def test_validation_never_mutates_sim(tmp_path):
    actions = [
        MigrateService(service_id="svc2", target_node_id="edge3"),
        RestartService(service_id="svc0"),
        ScaleService(service_id="svc0", delta_replicas=1),
        RerouteTraffic(service_id="svc2", path_hint=["l_gw_dev0", "l_gw_edge2"]),
        ThrottleService(service_id="svc0", rate_limit=10.0),
        NoOp(),
    ]
    for action in actions:
        sim = _default_sim(seed=2, schedule=_saturation(), advance=30)
        before = sim.snapshot()
        validator = TwinValidator(
            TwinConfig(horizon_ticks=20), seed=0, log_path=str(tmp_path / "twin.jsonl")
        )
        validator.validate(sim, action, None)
        assert sim.snapshot() == before


def test_perfect_twin_matches_real_rollout(tmp_path):
    sim = _default_sim(seed=4, schedule=_saturation(start=5, duration=300), advance=30)
    validator = TwinValidator(
        TwinConfig(fidelity=1.0, horizon_ticks=30), seed=0, log_path=str(tmp_path / "t.jsonl")
    )
    verdict = validator.validate(sim, NoOp(), None)
    real = rollout(sim, NoOp(), 30, FAULT_MODE_SCHEDULED)
    assert verdict.noop_metrics == real.metrics


def test_twin_is_not_clairvoyant(tmp_path):
    start, duration = 20, 15
    sim = _default_sim(
        seed=6,
        schedule=FaultSchedule([FaultEvent("node_cpu_saturation", "edge2", start, duration, 4.5)]),
        advance=25,
    )
    expiry_index = (start + duration) - sim.state.tick
    blind = rollout(sim, NoOp(), 30, FAULT_MODE_BLIND)
    real = rollout(sim, NoOp(), 30, FAULT_MODE_SCHEDULED)

    assert blind.metrics != real.metrics
    for k in range(expiry_index + 1, 30):
        assert (
            blind.metrics[k].node_utilisation["edge2"]
            > real.metrics[k].node_utilisation["edge2"]
        )


def test_approves_helpful_migration(tmp_path):
    schedule = FaultSchedule([FaultEvent("node_cpu_saturation", "busy", 10, 300, 4.5)])
    sim = NetworkSim(_busy_spare_topology(), SimConfig(), seed=3, schedule=schedule)
    for _ in range(25):
        sim.step()
    validator = TwinValidator(
        TwinConfig(fidelity=1.0, horizon_ticks=30), seed=0, log_path=str(tmp_path / "t.jsonl")
    )
    verdict = validator.validate(sim, MigrateService(service_id="svcX", target_node_id="spare"), None)
    assert verdict.approved


def test_rejects_harmful_migration_with_availability_reason(tmp_path):
    sim = NetworkSim(_busy_spare_topology(), SimConfig(), seed=3)
    for _ in range(25):
        sim.step()
    validator = TwinValidator(
        TwinConfig(fidelity=1.0, horizon_ticks=30), seed=0, log_path=str(tmp_path / "t.jsonl")
    )
    verdict = validator.validate(sim, MigrateService(service_id="svcX", target_node_id="spare"), None)
    assert not verdict.approved
    assert "availability" in verdict.reason
    assert not feedback_from_verdict(verdict).approved


def test_rejects_harmful_restart(tmp_path):
    sim = NetworkSim(_busy_spare_topology(), SimConfig(), seed=3)
    for _ in range(25):
        sim.step()
    validator = TwinValidator(
        TwinConfig(fidelity=1.0, horizon_ticks=30), seed=0, log_path=str(tmp_path / "t.jsonl")
    )
    verdict = validator.validate(sim, RestartService(service_id="svcX"), None)
    assert not verdict.approved


def _axis_error(config, seed, tmp_path):
    sim = _default_sim(seed=seed, schedule=_saturation(), advance=30)
    validator = TwinValidator(config, seed=seed, log_path=str(tmp_path / "e.jsonl"))
    verdict = validator.validate(sim, NoOp(), None)
    real = rollout(sim, NoOp(), config.horizon_ticks, FAULT_MODE_SCHEDULED)
    return _mae(_mean_p95(verdict.noop_metrics), _mean_p95(real.metrics))


def test_each_fidelity_axis_degrades_accuracy(tmp_path):
    seeds = range(4)
    base = TwinConfig(horizon_ticks=30)
    axes = [
        TwinConfig(sigma_obs=0.3, horizon_ticks=30),
        TwinConfig(lag_ticks=10, horizon_ticks=30),
        TwinConfig(drift_pct=0.3, horizon_ticks=30),
        TwinConfig(simplify_queueing=True, horizon_ticks=30),
        TwinConfig(forecast_err=0.4, horizon_ticks=30),
    ]
    base_error = _mean(_axis_error(base, s, tmp_path) for s in seeds)
    for axis in axes:
        axis_error = _mean(_axis_error(axis, s, tmp_path) for s in seeds)
        assert axis_error > base_error


def test_scalar_fidelity_sweep_is_monotonic(tmp_path):
    seeds = range(6)

    def _error(fidelity):
        config = fidelity_to_config(fidelity, horizon_ticks=30)
        return _mean(_axis_error(config, s, tmp_path) for s in seeds)

    high = _error(1.0)
    mid = _error(0.6)
    low = _error(0.2)
    assert low > mid > high


def test_fidelity_randomness_does_not_perturb_real_sim(tmp_path):
    schedule = _saturation()

    def _run(config):
        sim = _default_sim(seed=42, schedule=schedule, advance=0)
        validator = TwinValidator(config, seed=7, log_path=str(tmp_path / "r.jsonl"))
        metrics = []
        for _ in range(40):
            metrics.append(sim.step())
            validator.validate(sim, MigrateService(service_id="svc2", target_node_id="edge3"), None)
        return metrics

    assert _run(TwinConfig(horizon_ticks=20)) == _run(
        TwinConfig(sigma_obs=0.3, drift_pct=0.3, forecast_err=0.4, horizon_ticks=20)
    )


def test_determinism_same_inputs_same_verdict(tmp_path):
    def _once():
        sim = _default_sim(seed=3, schedule=_saturation(), advance=25)
        validator = TwinValidator(
            TwinConfig(sigma_obs=0.2, horizon_ticks=30), seed=9, log_path=str(tmp_path / "d.jsonl")
        )
        return validator.validate(sim, MigrateService(service_id="svc2", target_node_id="edge3"), None)

    first = _once()
    second = _once()
    assert first.approved == second.approved
    assert first.reason == second.reason
    assert first.action_violation_ticks == second.action_violation_ticks
    assert first.noop_violation_ticks == second.noop_violation_ticks


def test_tolerance_flips_rejection(tmp_path):
    sim = NetworkSim(_busy_spare_topology(), SimConfig(), seed=3)
    for _ in range(25):
        sim.step()
    action = MigrateService(service_id="svcX", target_node_id="spare")

    strict = TwinValidator(
        TwinConfig(fidelity=1.0, horizon_ticks=30, tolerance_margin=0.0),
        seed=0,
        log_path=str(tmp_path / "s.jsonl"),
    ).validate(sim, action, None)
    lenient = TwinValidator(
        TwinConfig(fidelity=1.0, horizon_ticks=30, tolerance_margin=10000.0),
        seed=0,
        log_path=str(tmp_path / "l.jsonl"),
    ).validate(sim, action, None)

    assert not strict.approved
    assert lenient.approved
