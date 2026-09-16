from __future__ import annotations

from dataclasses import dataclass
import math

from ..config import ActionsConfig
from ..sim.state import SimState
from ..sim.routing import path_error, route_to
from .schema import (
    MigrateService,
    NoOp,
    RerouteTraffic,
    RestartService,
    ScaleService,
    ThrottleService,
)


@dataclass
class ValidationResult:
    valid: bool
    reason: str


def _node_mem_used(state: SimState, node_id: str) -> float:
    return sum(
        s.mem_footprint * max(s.replicas, 1)
        for s in state.services.values()
        if s.host_node_id == node_id
    )



def validate_action(action, state: SimState, config: ActionsConfig) -> ValidationResult:
    if isinstance(action, NoOp):
        return ValidationResult(True, "no_op is always valid")

    sid = action.service_id
    if sid not in state.services:
        return ValidationResult(False, f"service {sid} does not exist")
    service = state.services[sid]
    if any(effect.service_id == sid for effect in state.pending):
        return ValidationResult(False, f"service {sid} has a pending action")

    if isinstance(action, MigrateService):
        target = action.target_node_id
        if target not in state.nodes:
            return ValidationResult(False, f"target node {target} does not exist")
        node = state.nodes[target]
        if node.status == "down":
            return ValidationResult(False, f"target node {target} is down")
        if target == service.host_node_id:
            return ValidationResult(False, "service is already on target")
        if route_to(state, sid, target) is None:
            return ValidationResult(False, "target has no operational client path")
        footprint = service.mem_footprint * max(service.replicas, 1)
        if _node_mem_used(state, target) + footprint > node.mem_capacity:
            return ValidationResult(
                False, f"target node {target} lacks memory capacity for {sid}"
            )
        return ValidationResult(True, "migration accepted")

    if isinstance(action, RestartService):
        return ValidationResult(True, "restart accepted")

    if isinstance(action, ScaleService):
        if action.delta_replicas == 0:
            return ValidationResult(False, "scale delta cannot be zero")
        new_replicas = service.replicas + action.delta_replicas
        if new_replicas < 1:
            return ValidationResult(False, "scale would drop replicas below one")
        if new_replicas > config.replica_cap:
            return ValidationResult(
                False, f"scale would exceed replica cap of {config.replica_cap}"
            )
        if action.delta_replicas > 0:
            node = state.nodes[service.host_node_id]
            added = service.mem_footprint * action.delta_replicas
            if _node_mem_used(state, service.host_node_id) + added > node.mem_capacity:
                return ValidationResult(
                    False,
                    f"host node {service.host_node_id} cannot accommodate scale up",
                )
        return ValidationResult(True, "scale accepted")

    if isinstance(action, RerouteTraffic):
        verdict = path_error(state, action.path_hint, service.source_node_id, service.host_node_id)
        if verdict:
            return ValidationResult(False, verdict)
        return ValidationResult(True, "reroute accepted")

    if isinstance(action, ThrottleService):
        if not math.isfinite(action.rate_limit) or action.rate_limit <= 0.0:
            return ValidationResult(False, "rate_limit must be finite and positive")
        return ValidationResult(True, "throttle accepted")

    return ValidationResult(False, "unknown action")
