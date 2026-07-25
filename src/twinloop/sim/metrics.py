from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TickMetrics:
    tick: int
    service_p50: dict[str, float] = field(default_factory=dict)
    service_p95: dict[str, float] = field(default_factory=dict)
    service_throughput: dict[str, float] = field(default_factory=dict)
    service_drop_rate: dict[str, float] = field(default_factory=dict)
    service_queue_len: dict[str, int] = field(default_factory=dict)
    node_utilisation: dict[str, float] = field(default_factory=dict)
    link_latency: dict[str, float] = field(default_factory=dict)
