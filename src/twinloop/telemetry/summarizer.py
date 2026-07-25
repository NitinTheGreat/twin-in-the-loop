from __future__ import annotations

from ..config import SLOConfig
from .collector import Observation


class Summarizer:
    def __init__(self, config: SLOConfig) -> None:
        self.config = config

    def estimated_tokens(self, text: str) -> float:
        return len(text) / self.config.chars_per_token

    def _service_line(self, sid: str, observation: Observation) -> str:
        status = observation.slo_status[sid]
        metrics = observation.metrics
        p95_flag = "OK" if status.p95_ok else "FAIL"
        avail_flag = "OK" if status.availability_ok else "FAIL"
        return (
            f"  {sid} (host {observation.topology.service_hosts.get(sid, '?')}): "
            f"p95={status.p95_ms:.0f}ms/{status.p95_target_ms:.0f} {p95_flag}, "
            f"avail={status.availability * 100:.1f}%/{status.availability_target * 100:.0f} {avail_flag}, "
            f"qlen={metrics.service_queue_len.get(sid, 0)}, "
            f"thr={metrics.service_throughput.get(sid, 0.0):.0f}rps"
        )

    def render(self, observation: Observation) -> str:
        slo = observation.slo_status
        metrics = observation.metrics
        budget = self.config.summary_char_budget

        violations = [sid for sid, s in slo.items() if not s.compliant]
        at_risk = [sid for sid, s in slo.items() if s.compliant and s.at_risk]
        healthy = [sid for sid, s in slo.items() if s.compliant and not s.at_risk]

        violations.sort(key=lambda sid: (slo[sid].availability, -slo[sid].p95_ms))
        at_risk.sort(key=lambda sid: -slo[sid].p95_ms)
        healthy.sort()

        sections: list[str] = []
        sections.append(f"OBSERVATION tick={observation.tick}")
        sections.append(
            f"SLO: {len(violations)} in violation, {len(at_risk)} at risk, "
            f"{len(healthy)} healthy (of {len(slo)} services)"
        )

        if violations:
            block = ["VIOLATIONS:"]
            block += [self._service_line(sid, observation) for sid in violations]
            sections.append("\n".join(block))

        if at_risk:
            block = ["AT RISK:"]
            block += [self._service_line(sid, observation) for sid in at_risk]
            sections.append("\n".join(block))

        hot_nodes = sorted(
            ((nid, u) for nid, u in metrics.node_utilisation.items() if u >= 0.85),
            key=lambda item: -item[1],
        )
        if hot_nodes:
            block = ["HOT NODES:"]
            block += [f"  {nid}: util={u * 100:.0f}%" for nid, u in hot_nodes]
            sections.append("\n".join(block))

        latencies = metrics.link_latency
        if latencies:
            nominal = min(latencies.values())
            elevated = sorted(
                (
                    (lid, ms)
                    for lid, ms in latencies.items()
                    if ms > nominal * 1.5
                ),
                key=lambda item: -item[1],
            )
            if elevated:
                block = ["ELEVATED LINKS:"]
                block += [f"  {lid}: latency={ms:.0f}ms" for lid, ms in elevated]
                sections.append("\n".join(block))

        text = "\n".join(sections)

        if healthy:
            avg_p95 = sum(slo[sid].p95_ms for sid in healthy) / len(healthy)
            avg_avail = sum(slo[sid].availability for sid in healthy) / len(healthy)
            aggregate = (
                f"HEALTHY: {len(healthy)} services within SLO "
                f"(mean p95 {avg_p95:.0f}ms, mean availability {avg_avail * 100:.1f}%)"
            )
            full = "\n".join(
                ["HEALTHY:"] + [self._service_line(sid, observation) for sid in healthy]
            )
            if len(text) + 1 + len(full) <= budget:
                text = text + "\n" + full
            else:
                text = text + "\n" + aggregate

        context = (
            f"CONTEXT: topology {observation.topology.n_gateways}gw/"
            f"{observation.topology.n_edges}edge/{observation.topology.n_devices}dev/"
            f"{observation.topology.n_services}svc; history window "
            f"{len(observation.history)} ticks"
        )
        if len(text) + 1 + len(context) <= budget:
            text = text + "\n" + context

        return text
