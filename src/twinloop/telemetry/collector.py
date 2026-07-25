from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from ..config import SLOConfig
from ..sim.metrics import TickMetrics
from .slo import ServiceSLO, SLOEvaluator


@dataclass
class TopologySummary:
    n_gateways: int
    n_edges: int
    n_devices: int
    n_services: int
    node_roles: dict[str, str] = field(default_factory=dict)
    service_hosts: dict[str, str] = field(default_factory=dict)


def summarize_topology(topology) -> TopologySummary:
    roles = {node.id: node.role for node in topology.nodes}
    hosts = {service.id: service.host_node_id for service in topology.services}
    counts = {"gateway": 0, "edge": 0, "device": 0}
    for role in roles.values():
        if role in counts:
            counts[role] += 1
    return TopologySummary(
        n_gateways=counts["gateway"],
        n_edges=counts["edge"],
        n_devices=counts["device"],
        n_services=len(hosts),
        node_roles=roles,
        service_hosts=hosts,
    )


@dataclass
class Observation:
    tick: int
    metrics: TickMetrics
    history: list[TickMetrics]
    topology: TopologySummary
    slo_status: dict[str, ServiceSLO]


class Collector:
    def __init__(self, topology_summary: TopologySummary, config: SLOConfig) -> None:
        self.topology = topology_summary
        self.config = config
        self.evaluator = SLOEvaluator(config)
        self._history: deque = deque(maxlen=config.history_window)

    def observe(self, metrics: TickMetrics) -> Observation:
        self._history.append(metrics)
        slo_status = self.evaluator.evaluate(metrics)
        return Observation(
            tick=metrics.tick,
            metrics=metrics,
            history=list(self._history),
            topology=self.topology,
            slo_status=slo_status,
        )
