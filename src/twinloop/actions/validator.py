from __future__ import annotations

from dataclasses import dataclass

from ..config import ActionsConfig
from ..sim.state import SimState
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


def _path_verdict(state: SimState, path: list[str], host_node_id: str) -> str:
    if not path:
        return "empty path is not connected"
    for link_id in path:
        if link_id not in state.links:
            return f"unknown link {link_id} in path"
    for link_id in path:
        if state.links[link_id].status != "up":
            return f"path traverses down link {link_id}"
    reachable = set(state.links[path[0]].endpoints)
    for link_id in path[1:]:
        endpoints = set(state.links[link_id].endpoints)
        if reachable.isdisjoint(endpoints):
            return "path links are not connected"
        reachable |= endpoints
    if host_node_id not in reachable:
        return "path does not reach the service host"
    return ""


def validate_action(action, state: SimState, config: ActionsConfig) -> ValidationResult:
    if isinstance(action, NoOp):
        return ValidationResult(True, "no_op is always valid")

    sid = action.service_id
    if sid not in state.services:
        return ValidationResult(False, f"service {sid} does not exist")
    service = state.services[sid]

    if isinstance(action, MigrateService):
        target = action.target_node_id
        if target not in state.nodes:
            return ValidationResult(False, f"target node {target} does not exist")
        node = state.nodes[target]
        if node.status == "down":
            return ValidationResult(False, f"target node {target} is down")
        footprint = service.mem_footprint * max(service.replicas, 1)
        if _node_mem_used(state, target) + footprint > node.mem_capacity:
            return ValidationResult(
                False, f"target node {target} lacks memory capacity for {sid}"
            )
        return ValidationResult(True, "migration accepted")

    if isinstance(action, RestartService):
        return ValidationResult(True, "restart accepted")

    if isinstance(action, ScaleService):
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
        verdict = _path_verdict(state, action.path_hint, service.host_node_id)
        if verdict:
            return ValidationResult(False, verdict)
        return ValidationResult(True, "reroute accepted")

    if isinstance(action, ThrottleService):
        if action.rate_limit <= 0.0:
            return ValidationResult(False, "rate_limit must be positive")
        return ValidationResult(True, "throttle accepted")

    return ValidationResult(False, "unknown action")
