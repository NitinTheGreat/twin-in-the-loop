from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..config import ActionsConfig
from ..sim.state import PendingEffect
from .schema import (
    MigrateService,
    NoOp,
    RerouteTraffic,
    RestartService,
    ScaleService,
    ThrottleService,
)


@dataclass
class ActionResult:
    action_type: str
    success: bool
    reason: str
    cost: float = 0.0
    details: dict = field(default_factory=dict)


def _migration_downtime(mem_footprint: float, config: ActionsConfig) -> int:
    computed = math.ceil(mem_footprint * config.migration_downtime_per_mem)
    return max(config.migration_min_downtime, computed)


def execute_action(sim, action, config: ActionsConfig) -> ActionResult:
    state = sim.state

    if isinstance(action, NoOp):
        return ActionResult("no_op", True, "did nothing", 0.0, {})

    service = state.services[action.service_id]

    if isinstance(action, MigrateService):
        downtime = _migration_downtime(service.mem_footprint, config)
        cost = service.mem_footprint * config.migration_transfer_cost
        service.status = "down"
        service.queue.clear()
        service.in_service = None
        state.pending.append(
            PendingEffect(
                kind="migrate",
                service_id=service.id,
                remaining=downtime,
                target_node_id=action.target_node_id,
            )
        )
        return ActionResult(
            "migrate_service",
            True,
            f"migrating {service.id} to {action.target_node_id}",
            cost,
            {"downtime": downtime, "target_node_id": action.target_node_id},
        )

    if isinstance(action, RestartService):
        downtime = config.restart_downtime
        service.status = "down"
        service.queue.clear()
        service.in_service = None
        state.pending.append(
            PendingEffect(
                kind="restart",
                service_id=service.id,
                remaining=downtime,
            )
        )
        return ActionResult(
            "restart_service",
            True,
            f"restarting {service.id}",
            config.restart_cost,
            {"downtime": downtime},
        )

    if isinstance(action, ScaleService):
        before = service.replicas
        service.replicas = service.replicas + action.delta_replicas
        return ActionResult(
            "scale_service",
            True,
            f"scaled {service.id} from {before} to {service.replicas} replicas",
            float(abs(action.delta_replicas)),
            {"replicas": service.replicas},
        )

    if isinstance(action, RerouteTraffic):
        state.routes[service.id] = list(action.path_hint)
        return ActionResult(
            "reroute_traffic",
            True,
            f"rerouted {service.id}",
            float(len(action.path_hint)),
            {"path_hint": list(action.path_hint)},
        )

    if isinstance(action, ThrottleService):
        service.rate_limit = action.rate_limit
        return ActionResult(
            "throttle_service",
            True,
            f"throttled {service.id} to {action.rate_limit} rps",
            0.0,
            {"rate_limit": action.rate_limit},
        )

    return ActionResult("unknown", False, "unknown action", 0.0, {})
