from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Node:
    id: str
    role: str
    cpu_capacity: float
    mem_capacity: float
    cpu_used: float = 0.0
    mem_used: float = 0.0
    status: str = "healthy"
