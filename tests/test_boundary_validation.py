"""Reproductions of inputs that previously passed validation and broke the plant."""
import pytest
from pydantic import ValidationError

from twinloop.actions.executor import execute_action
from twinloop.actions.schema import (
    MigrateService, NoOp, RestartService, ScaleService, ThrottleService, RerouteTraffic,
)
from twinloop.actions.validator import validate_action
from twinloop.config import ActionsConfig, FaultConfig, GraphConfig, SimConfig, TopologyConfig
from twinloop.sim.engine import NetworkSim, build_topology
from twinloop.sim.service import Request


def network():
    cfg = SimConfig()
    return NetworkSim(build_topology(TopologyConfig(), cfg, arrival_rate=0), cfg, seed=17)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), 0, -1])
def test_nonfinite_or_nonpositive_throttle_rejected_without_mutation(value):
    sim = network()
    action = ThrottleService(service_id="svc0", rate_limit=value)
    before = sim.snapshot()
    verdict = validate_action(action, sim.state, ActionsConfig())
    assert not verdict.valid
    assert "positive" in verdict.reason
    assert not execute_action(sim, action, ActionsConfig()).success
    assert sim.snapshot() == before
    sim.step()


@pytest.mark.parametrize("value", [.001, 1, 1e100])
def test_finite_positive_throttle_accepted(value):
    sim = network()
    assert execute_action(sim, ThrottleService(service_id="svc0", rate_limit=value), ActionsConfig()).success
    sim.step()


def test_zero_scale_rejected_without_mutation():
    sim = network()
    action = ScaleService(service_id="svc0", delta_replicas=0)
    before = sim.snapshot()
    verdict = validate_action(action, sim.state, ActionsConfig())
    assert not verdict.valid and "zero" in verdict.reason
    assert not execute_action(sim, action, ActionsConfig()).success
    assert sim.snapshot() == before


RESTART = RestartService(service_id="svc0")
MIGRATE = MigrateService(service_id="svc0", target_node_id="edge1")


@pytest.mark.parametrize("first", [RESTART, MIGRATE], ids=["restart", "migration"])
@pytest.mark.parametrize("second", [RESTART, MIGRATE,
    ScaleService(service_id="svc0", delta_replicas=1),
    ThrottleService(service_id="svc0", rate_limit=1),
    RerouteTraffic(service_id="svc0", path_hint=["l_gw_dev0", "l_gw_edge0"])],
    ids=["restart", "migration", "scale", "throttle", "reroute"])
def test_pending_action_blocks_every_state_change(first, second):
    sim = network()
    svc = sim.state.services["svc0"]
    svc.queue = [Request(0), Request(0)]
    svc.in_service = Request(0, work=2)
    cfg = ActionsConfig()
    initial = execute_action(sim, first, cfg)
    assert initial.success
    before = sim.snapshot()
    verdict = validate_action(second, sim.state, cfg)
    assert not verdict.valid and "pending" in verdict.reason
    assert not execute_action(sim, second, cfg).success
    assert sim.snapshot() == before
    assert execute_action(sim, NoOp(), cfg).success
    losses = 0
    for index in range(initial.details["downtime"]):
        assert svc.status == "down"
        metrics = sim.step()
        losses += metrics.service_dropped["svc0"]
        assert metrics.service_completed["svc0"] == 0
        if index < initial.details["downtime"] - 1:
            assert svc.status == "down"
    assert not sim.state.pending and svc.status == "healthy"
    assert losses == svc.total_request_losses == 3
    assert sim.step().service_dropped["svc0"] == 0


@pytest.mark.parametrize("model,kwargs", [
    (SimConfig, {"tick_seconds": 0}), (SimConfig, {"tick_seconds": float("nan")}),
    (SimConfig, {"default_cpu_capacity": float("inf")}),
    (SimConfig, {"queue_cap": -1}), (GraphConfig, {"decision_interval_ticks": 0}),
    (TopologyConfig, {"n_edge_servers": 0}), (TopologyConfig, {"n_devices": 0}),
    (TopologyConfig, {"n_gateways": 0}), (TopologyConfig, {"n_gateways": 2}),
    (FaultConfig, {"min_start_tick": 20, "max_start_tick": 10}),
    (FaultConfig, {"min_duration": 4, "max_duration": 3}),
    (FaultConfig, {"min_magnitude": 4, "max_magnitude": 3}),
    (ActionsConfig, {"migration_downtime_per_mem": -1}),
    (ActionsConfig, {"migration_min_downtime": 0}),
    (ActionsConfig, {"migration_transfer_cost": -1}),
    (ActionsConfig, {"migration_downtime_per_mem": float("inf")}),
])
def test_invalid_config_rejected_at_construction(model, kwargs):
    with pytest.raises(ValidationError):
        model(**kwargs)


def test_smallest_supported_configuration():
    cfg = SimConfig(tick_seconds=.001, episode_ticks=1, queue_cap=0)
    top = TopologyConfig(n_gateways=1, n_edge_servers=1, n_devices=1, n_services=1)
    sim = NetworkSim(build_topology(top, cfg), cfg, seed=4)
    assert len(sim.state.nodes) == 3
    assert GraphConfig(decision_interval_ticks=1, retry_cap=0)
    assert FaultConfig(faults_per_episode=0, min_start_tick=0, max_start_tick=0,
                       min_duration=1, max_duration=1, min_magnitude=1, max_magnitude=1)
    assert ActionsConfig(migration_downtime_per_mem=0, migration_min_downtime=1, restart_downtime=1)
    metrics = sim.step()
    assert 0 <= metrics.service_drop_rate["svc0"] <= 1
