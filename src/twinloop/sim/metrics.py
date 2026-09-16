from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TickMetrics:
    """Per-processing-tick measurements.

    Latencies are None without completions. Drop rate is losses divided by
    (opening outstanding + newly generated + action losses since the last tick).
    Arrivals + opening outstanding = completed + dropped + closing outstanding;
    pre-tick action losses remain in the opening population until reported.
    service_hosts and node_memory_utilisation record closing state after action completion.
    """
    tick: int
    service_p50: dict[str, float | None] = field(default_factory=dict)
    service_p95: dict[str, float | None] = field(default_factory=dict)
    service_throughput: dict[str, float] = field(default_factory=dict)
    service_drop_rate: dict[str, float] = field(default_factory=dict)
    service_queue_len: dict[str, int] = field(default_factory=dict)
    node_utilisation: dict[str, float] = field(default_factory=dict)
    link_latency: dict[str, float] = field(default_factory=dict)

    node_memory_utilisation: dict[str, float] = field(default_factory=dict)
    service_hosts: dict[str, str] = field(default_factory=dict)
    service_arrivals: dict[str, int] = field(default_factory=dict)
    service_dropped: dict[str, int] = field(default_factory=dict)
    service_completed: dict[str, int] = field(default_factory=dict)
    service_outstanding: dict[str, int] = field(default_factory=dict)


def latency_ms(value):
    return None if value is None else value * 1000.0


def mean_latency(values):
    """Mean of observed latencies only; an entirely missing series stays missing."""
    observed = [v for v in values if v is not None]
    return sum(observed) / len(observed) if observed else None


def display_latency(value):
    return "missing" if value is None else f"{value:.0f} ms"


def rounded_latency(value):
    return None if value is None else round(value * 1000.0, 1)
