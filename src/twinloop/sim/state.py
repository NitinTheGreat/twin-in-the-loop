from __future__ import annotations

from dataclasses import dataclass, field

from .link import Link
from .node import Node
from .service import Service
from .workload import Workload


@dataclass
class PendingEffect:
    kind: str
    service_id: str
    remaining: int
    target_node_id: str = ""


@dataclass
class SimState:
    tick: int = 0
    nodes: dict[str, Node] = field(default_factory=dict)
    links: dict[str, Link] = field(default_factory=dict)
    services: dict[str, Service] = field(default_factory=dict)
    routes: dict[str, list[str]] = field(default_factory=dict)
    workloads: dict[str, Workload] = field(default_factory=dict)
    active_faults: dict[int, dict] = field(default_factory=dict)
    pending: list[PendingEffect] = field(default_factory=list)
    rng_states: dict[str, dict] = field(default_factory=dict)
