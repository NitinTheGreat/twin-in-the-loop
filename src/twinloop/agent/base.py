from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from ..actions.schema import BaseAction
from ..sim.link import Link
from ..sim.node import Node
from ..sim.service import Service
from ..sim.state import PendingEffect, SimState
from ..telemetry.collector import Observation


@dataclass
class TwinFeedback:
    approved: bool
    reason: str
    predicted_metrics: dict = field(default_factory=dict)


@dataclass
class DecisionContext:
    tick: int
    pending: list[PendingEffect] = field(default_factory=list)
    state: Optional[SimState] = None


class Agent(Protocol):
    def decide(
        self, obs: Observation, feedback: Optional[TwinFeedback] = None
    ) -> BaseAction: ...


def _sanitized_state(state: SimState) -> SimState:
    nodes = {
        nid: Node(
            id=n.id,
            role=n.role,
            cpu_capacity=n.cpu_capacity,
            mem_capacity=n.mem_capacity,
            status=n.status,
        )
        for nid, n in state.nodes.items()
    }
    links = {
        lid: Link(
            id=link.id,
            endpoints=link.endpoints,
            bandwidth=link.bandwidth,
            base_latency_ms=link.base_latency_ms,
            current_latency_ms=link.base_latency_ms,
            status=link.status,
        )
        for lid, link in state.links.items()
    }
    services = {
        sid: Service(
            id=s.id,
            host_node_id=s.host_node_id,
            cpu_demand_per_req=s.cpu_demand_per_req,
            mem_footprint=s.mem_footprint,
            replicas=s.replicas,
            status=s.status,
            baseline_mem=s.baseline_mem,
            rate_limit=s.rate_limit,
        )
        for sid, s in state.services.items()
    }
    routes = {sid: list(path) for sid, path in state.routes.items()}
    pending = [
        PendingEffect(
            kind=e.kind,
            service_id=e.service_id,
            remaining=e.remaining,
            target_node_id=e.target_node_id,
        )
        for e in state.pending
    ]
    return SimState(
        tick=state.tick,
        nodes=nodes,
        links=links,
        services=services,
        routes=routes,
        workloads={},
        active_faults={},
        pending=pending,
        rng_states={},
    )


def context_from_sim(sim) -> DecisionContext:
    clean = _sanitized_state(sim.state)
    return DecisionContext(tick=clean.tick, pending=clean.pending, state=clean)
