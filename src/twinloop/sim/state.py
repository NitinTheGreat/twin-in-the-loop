from __future__ import annotations

from dataclasses import dataclass, field

from .link import Link
from .node import Node
from .service import Service


@dataclass
class SimState:
    tick: int = 0
    nodes: dict[str, Node] = field(default_factory=dict)
    links: dict[str, Link] = field(default_factory=dict)
    services: dict[str, Service] = field(default_factory=dict)
