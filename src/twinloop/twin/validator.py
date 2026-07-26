from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..actions.schema import NoOp
from ..agent.base import TwinFeedback
from ..config import ActionsConfig, SLOConfig, TwinConfig
from ..seeding import SeedManager
from .fidelity import apply_fidelity, deterministic_service_prepare
from .rollout import FAULT_MODE_BLIND, rollout


@dataclass
class TwinVerdict:
    approved: bool
    reason: str
    action_violation_ticks: int
    noop_violation_ticks: int
    action_metrics: list = field(default_factory=list)
    noop_metrics: list = field(default_factory=list)
    fidelity: dict = field(default_factory=dict)
    cost_ms: float = 0.0


def feedback_from_verdict(verdict: TwinVerdict) -> TwinFeedback:
    return TwinFeedback(
        approved=verdict.approved,
        reason=verdict.reason,
        predicted_metrics={
            "action_violation_ticks": verdict.action_violation_ticks,
            "noop_violation_ticks": verdict.noop_violation_ticks,
        },
    )


def _mean(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


class TwinValidator:
    def __init__(
        self,
        config: TwinConfig,
        seed: int = 0,
        actions_config=None,
        slo_config=None,
        log_path=None,
    ) -> None:
        self.config = config
        self.seed_manager = SeedManager(seed)
        self.actions_config = actions_config or ActionsConfig()
        self.slo_config = slo_config or SLOConfig()
        self.log_path = (
            Path(log_path)
            if log_path is not None
            else Path("results/logs/twin_validations.jsonl")
        )
        self.records: list[dict] = []

    def _fidelity_dict(self) -> dict:
        return {
            "fidelity": self.config.fidelity,
            "sigma_obs": self.config.sigma_obs,
            "lag_ticks": self.config.lag_ticks,
            "drift_pct": self.config.drift_pct,
            "forecast_err": self.config.forecast_err,
            "simplify_queueing": self.config.simplify_queueing,
        }

    def _build_twin(self, sim):
        twin = sim.fork()
        twin._injector = None
        generator = self.seed_manager.stream(f"fidelity::{sim.state.tick}")
        apply_fidelity(twin.state, self.config, generator)
        return twin

    def _dominant_harm(self, action_metrics, noop_metrics) -> str:
        action_drop = _mean(
            _mean(m.service_drop_rate.values()) for m in action_metrics
        )
        noop_drop = _mean(_mean(m.service_drop_rate.values()) for m in noop_metrics)
        if action_drop > noop_drop + 0.05:
            return (
                "predicted loss of availability from dropped requests "
                "such as migration or restart downtime"
            )
        return "predicted higher p95 response latency"

    def _reason(self, approved, action_res, noop_res, diff, tolerance) -> str:
        action_vt = action_res.violation_ticks
        noop_vt = noop_res.violation_ticks
        if approved:
            return (
                f"approved: predicted {action_vt} SLO violation-ticks versus "
                f"{noop_vt} for no-op (difference {diff} within tolerance {tolerance})"
            )
        harm = self._dominant_harm(action_res.metrics, noop_res.metrics)
        return (
            f"rejected: predicted {action_vt} SLO violation-ticks versus "
            f"{noop_vt} for no-op (+{diff} beyond tolerance {tolerance}); {harm}"
        )

    def validate(self, sim, action, obs=None) -> TwinVerdict:
        start = time.perf_counter()
        twin = self._build_twin(sim)
        prepare = (
            deterministic_service_prepare if self.config.simplify_queueing else None
        )
        horizon = self.config.horizon_ticks

        action_res = rollout(
            twin,
            action,
            horizon,
            FAULT_MODE_BLIND,
            self.actions_config,
            self.slo_config,
            prepare=prepare,
        )
        noop_res = rollout(
            twin,
            NoOp(),
            horizon,
            FAULT_MODE_BLIND,
            self.actions_config,
            self.slo_config,
            prepare=prepare,
        )

        diff = action_res.violation_ticks - noop_res.violation_ticks
        tolerance = self.config.tolerance_margin
        approved = diff <= tolerance
        reason = self._reason(approved, action_res, noop_res, diff, tolerance)
        cost_ms = (time.perf_counter() - start) * 1000.0

        verdict = TwinVerdict(
            approved=approved,
            reason=reason,
            action_violation_ticks=action_res.violation_ticks,
            noop_violation_ticks=noop_res.violation_ticks,
            action_metrics=action_res.metrics,
            noop_metrics=noop_res.metrics,
            fidelity=self._fidelity_dict(),
            cost_ms=cost_ms,
        )
        self._log(sim.state.tick, action, verdict)
        return verdict

    def _log(self, tick, action, verdict: TwinVerdict) -> None:
        record = {
            "tick": tick,
            "action": action.model_dump() if hasattr(action, "model_dump") else str(action),
            "approved": verdict.approved,
            "reason": verdict.reason,
            "action_violation_ticks": verdict.action_violation_ticks,
            "noop_violation_ticks": verdict.noop_violation_ticks,
            "fidelity": verdict.fidelity,
            "cost_ms": verdict.cost_ms,
        }
        self.records.append(record)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
