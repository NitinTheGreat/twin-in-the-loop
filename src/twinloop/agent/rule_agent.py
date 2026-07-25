from __future__ import annotations

from typing import Optional

from ..actions.schema import (
    MigrateService,
    NoOp,
    RerouteTraffic,
    RestartService,
    ScaleService,
    ThrottleService,
)
from ..actions.validator import validate_action
from ..config import ActionsConfig, RuleAgentConfig
from .base import DecisionContext, TwinFeedback


class RuleAgent:
    name = "rule"

    def __init__(
        self,
        config: Optional[RuleAgentConfig] = None,
        actions_config: Optional[ActionsConfig] = None,
    ) -> None:
        self.config = config or RuleAgentConfig()
        self.actions_config = actions_config or ActionsConfig()
        self._context: Optional[DecisionContext] = None
        self._last_action_tick: dict[str, int] = {}
        self._mem_history: dict[str, list[float]] = {}
        self.last_reason: str = "no decision yet"

    def set_context(self, context: DecisionContext) -> None:
        self._context = context

    def decide(self, obs, feedback: Optional[TwinFeedback] = None):
        ctx = self._context
        if ctx is None or ctx.state is None:
            self.last_reason = "no context available"
            return NoOp()

        self._record_mem(ctx)

        for rule in (
            self._try_migrate,
            self._try_restart,
            self._try_reroute,
            self._try_scale,
            self._try_throttle,
        ):
            action = rule(obs, ctx)
            if action is not None:
                self._last_action_tick[action.service_id] = ctx.tick
                return action

        self.last_reason = "no sustained actionable condition; holding"
        return NoOp()

    def _record_mem(self, ctx: DecisionContext) -> None:
        window = self.config.mem_growth_window + 1
        for sid, service in ctx.state.services.items():
            history = self._mem_history.setdefault(sid, [])
            history.append(service.mem_footprint)
            if len(history) > window:
                del history[: len(history) - window]

    def _available(self, sid: str, ctx: DecisionContext) -> bool:
        if any(effect.service_id == sid for effect in ctx.pending):
            return False
        last = self._last_action_tick.get(sid)
        if last is not None and ctx.tick - last < self.config.cooldown_ticks:
            return False
        return True

    def _valid(self, action, ctx: DecisionContext) -> bool:
        return validate_action(action, ctx.state, self.actions_config).valid

    def _sustained_node(self, node_id: str, obs) -> bool:
        k = self.config.sustain_ticks
        if len(obs.history) < k:
            return False
        return all(
            m.node_utilisation.get(node_id, 0.0) > self.config.node_util_threshold
            for m in obs.history[-k:]
        )

    def _sustained_link(self, link_id: str, obs) -> bool:
        k = self.config.sustain_ticks
        if len(obs.history) < k:
            return False
        return all(
            m.link_latency.get(link_id, 0.0) > self.config.link_latency_threshold_ms
            for m in obs.history[-k:]
        )

    def _sustained_p95_violation(self, sid: str, obs) -> bool:
        k = self.config.sustain_ticks
        if len(obs.history) < k:
            return False
        target = obs.slo_status[sid].p95_target_ms
        return all(
            m.service_p95.get(sid, 0.0) * 1000.0 > target for m in obs.history[-k:]
        )

    def _node_mem_used(self, ctx: DecisionContext, node_id: str) -> float:
        return sum(
            s.mem_footprint * max(s.replicas, 1)
            for s in ctx.state.services.values()
            if s.host_node_id == node_id
        )

    def _pick_target(self, sid: str, obs, ctx: DecisionContext) -> Optional[str]:
        service = ctx.state.services[sid]
        footprint = service.mem_footprint * max(service.replicas, 1)
        best: Optional[str] = None
        best_util = float("inf")
        for nid, node in ctx.state.nodes.items():
            if node.role != "edge" or nid == service.host_node_id or node.status == "down":
                continue
            if self._node_mem_used(ctx, nid) + footprint > node.mem_capacity:
                continue
            util = obs.metrics.node_utilisation.get(nid, 0.0)
            if util < best_util:
                best_util = util
                best = nid
        return best

    def _try_migrate(self, obs, ctx: DecisionContext):
        ordered = sorted(
            ctx.state.nodes,
            key=lambda nid: obs.metrics.node_utilisation.get(nid, 0.0),
            reverse=True,
        )
        for nid in ordered:
            node = ctx.state.nodes[nid]
            if node.role != "edge":
                continue
            if not self._sustained_node(nid, obs):
                continue
            hosted = [
                sid
                for sid, s in ctx.state.services.items()
                if s.host_node_id == nid
            ]
            violating = [
                sid
                for sid in hosted
                if not obs.slo_status[sid].compliant and self._available(sid, ctx)
            ]
            if not violating:
                continue
            candidate = max(
                violating,
                key=lambda sid: (obs.metrics.service_throughput.get(sid, 0.0), sid),
            )
            target = self._pick_target(candidate, obs, ctx)
            if target is None:
                continue
            action = MigrateService(service_id=candidate, target_node_id=target)
            if self._valid(action, ctx):
                self.last_reason = (
                    f"node {nid} util sustained above "
                    f"{self.config.node_util_threshold:.0%} with {candidate} in "
                    f"violation; migrate {candidate} to least-loaded {target}"
                )
                return action
        return None

    def _try_restart(self, obs, ctx: DecisionContext):
        window = self.config.mem_growth_window
        for sid in sorted(ctx.state.services):
            if not self._available(sid, ctx):
                continue
            history = self._mem_history.get(sid, [])
            if len(history) < window:
                continue
            recent = history[-window:]
            increasing = all(recent[i + 1] > recent[i] for i in range(len(recent) - 1))
            if increasing and recent[-1] - recent[0] > 1e-6:
                action = RestartService(service_id=sid)
                if self._valid(action, ctx):
                    self.last_reason = (
                        f"{sid} memory footprint rose monotonically over "
                        f"{window} ticks; restart to reclaim it"
                    )
                    return action
        return None

    def _alternative_path(self, sid: str, avoid: set, ctx: DecisionContext):
        state = ctx.state
        service = state.services[sid]
        host = service.host_node_id
        current = state.routes.get(sid, [])
        source_links = [
            lid
            for lid in current
            if any(state.nodes[e].role == "device" for e in state.links[lid].endpoints)
        ]
        host_links = [
            lid
            for lid, link in state.links.items()
            if host in link.endpoints and link.status == "up" and lid not in avoid
        ]
        for host_link in host_links:
            for source_link in source_links:
                path = [source_link, host_link]
                if path != current:
                    return path
        return None

    def _try_reroute(self, obs, ctx: DecisionContext):
        for sid in sorted(ctx.state.services):
            if not self._available(sid, ctx):
                continue
            if obs.slo_status[sid].compliant:
                continue
            if not self._sustained_p95_violation(sid, obs):
                continue
            route = ctx.state.routes.get(sid, [])
            slow = {lid for lid in route if self._sustained_link(lid, obs)}
            if not slow:
                continue
            path = self._alternative_path(sid, slow, ctx)
            if path is None:
                continue
            action = RerouteTraffic(service_id=sid, path_hint=path)
            if self._valid(action, ctx):
                self.last_reason = (
                    f"{sid} route latency sustained high; reroute around {sorted(slow)}"
                )
                return action
        return None

    def _try_scale(self, obs, ctx: DecisionContext):
        for sid in sorted(ctx.state.services):
            if not self._available(sid, ctx):
                continue
            status = obs.slo_status[sid]
            if status.compliant or status.p95_ok:
                continue
            if not self._sustained_p95_violation(sid, obs):
                continue
            action = ScaleService(
                service_id=sid, delta_replicas=self.config.scale_delta
            )
            if self._valid(action, ctx):
                self.last_reason = (
                    f"{sid} in p95 violation with host capacity; scale up by "
                    f"{self.config.scale_delta}"
                )
                return action
        return None

    def _try_throttle(self, obs, ctx: DecisionContext):
        for sid in sorted(ctx.state.services):
            if not self._available(sid, ctx):
                continue
            status = obs.slo_status[sid]
            if status.compliant or status.p95_ok:
                continue
            if not self._sustained_p95_violation(sid, obs):
                continue
            throughput = obs.metrics.service_throughput.get(sid, 0.0)
            limit = max(1.0, self.config.throttle_fraction * throughput)
            action = ThrottleService(service_id=sid, rate_limit=limit)
            if self._valid(action, ctx):
                self.last_reason = (
                    f"{sid} still in p95 violation with no structural remedy; "
                    f"throttle to {limit:.0f} rps as a last resort"
                )
                return action
        return None
