from __future__ import annotations

from dataclasses import dataclass

from ..config import SLOConfig
from ..sim.metrics import TickMetrics


@dataclass
class ServiceSLO:
    p95_ms: float
    availability: float
    p95_target_ms: float
    availability_target: float
    p95_ok: bool
    availability_ok: bool
    compliant: bool
    at_risk: bool


class SLOEvaluator:
    def __init__(self, config: SLOConfig) -> None:
        self.config = config
        self.violation_ticks: dict[str, int] = {}
        self.total_violation_ticks: int = 0

    def evaluate(self, metrics: TickMetrics) -> dict[str, ServiceSLO]:
        statuses: dict[str, ServiceSLO] = {}
        for sid in metrics.service_p95:
            p95_ms = metrics.service_p95[sid] * 1000.0
            availability = 1.0 - metrics.service_drop_rate[sid]
            p95_ok = p95_ms <= self.config.p95_target_ms
            availability_ok = availability >= self.config.availability_target
            compliant = p95_ok and availability_ok
            near_p95 = p95_ms >= self.config.at_risk_fraction * self.config.p95_target_ms
            near_availability = availability < self.config.availability_target + 0.01
            at_risk = compliant and (near_p95 or near_availability)
            if not compliant:
                self.violation_ticks[sid] = self.violation_ticks.get(sid, 0) + 1
                self.total_violation_ticks += 1
            statuses[sid] = ServiceSLO(
                p95_ms=p95_ms,
                availability=availability,
                p95_target_ms=self.config.p95_target_ms,
                availability_target=self.config.availability_target,
                p95_ok=p95_ok,
                availability_ok=availability_ok,
                compliant=compliant,
                at_risk=at_risk,
            )
        return statuses
