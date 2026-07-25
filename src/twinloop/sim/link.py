from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Link:
    id: str
    endpoints: tuple[str, str]
    bandwidth: float
    base_latency_ms: float
    current_latency_ms: float
    loss_rate: float = 0.0
    status: str = "up"
