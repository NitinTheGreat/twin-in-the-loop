from __future__ import annotations

from dataclasses import dataclass, field

from ..actions.executor import execute_action
from ..actions.schema import NoOp
from ..config import ActionsConfig, SLOConfig
from ..telemetry.slo import SLOEvaluator

FAULT_MODE_BLIND = "blind"
FAULT_MODE_SCHEDULED = "scheduled"


@dataclass
class RolloutResult:
    metrics: list = field(default_factory=list)
    violation_ticks: int = 0
    service_violation_ticks: dict = field(default_factory=dict)
    horizon: int = 0


def rollout(
    sim,
    action,
    horizon,
    fault_mode,
    actions_config=None,
    slo_config=None,
    prepare=None,
):
    actions_config = actions_config or ActionsConfig()
    slo_config = slo_config or SLOConfig()

    fork = sim.fork()
    if fault_mode == FAULT_MODE_BLIND:
        fork._injector = None
    elif fault_mode != FAULT_MODE_SCHEDULED:
        raise ValueError(f"unknown fault_mode {fault_mode!r}")

    if prepare is not None:
        prepare(fork)

    if action is not None:
        execute_action(fork, action, actions_config)

    evaluator = SLOEvaluator(slo_config)
    metrics = []
    for _ in range(horizon):
        tick_metrics = fork.step()
        metrics.append(tick_metrics)
        evaluator.evaluate(tick_metrics)

    return RolloutResult(
        metrics=metrics,
        violation_ticks=evaluator.total_violation_ticks,
        service_violation_ticks=dict(evaluator.violation_ticks),
        horizon=horizon,
    )
