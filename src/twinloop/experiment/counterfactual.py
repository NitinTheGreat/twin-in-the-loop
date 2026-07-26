from __future__ import annotations

import time
from dataclasses import dataclass

from ..actions.schema import NoOp
from ..twin.rollout import FAULT_MODE_SCHEDULED, rollout


@dataclass
class ProposalGroundTruth:
    action: object
    retry_index: int
    was_applied: bool
    verdict: object
    cf_action_violation_ticks: int
    cf_noop_violation_ticks: int
    harm_delta: int
    harmful: bool
    cf_wallclock_ms: float


def evaluate_decision(pre_action_sim, proposals, config):
    horizon = config.twin.horizon_ticks
    threshold = config.evaluation.harm_threshold_ticks

    noop_res = rollout(
        pre_action_sim,
        NoOp(),
        horizon,
        FAULT_MODE_SCHEDULED,
        config.actions,
        config.slo,
    )
    baseline = noop_res.violation_ticks

    results = []
    for proposal in proposals:
        start = time.perf_counter()
        action_res = rollout(
            pre_action_sim,
            proposal["action"],
            horizon,
            FAULT_MODE_SCHEDULED,
            config.actions,
            config.slo,
        )
        cf_ms = (time.perf_counter() - start) * 1000.0
        delta = action_res.violation_ticks - baseline
        results.append(
            ProposalGroundTruth(
                action=proposal["action"],
                retry_index=proposal["retry_index"],
                was_applied=proposal["was_applied"],
                verdict=proposal["verdict"],
                cf_action_violation_ticks=action_res.violation_ticks,
                cf_noop_violation_ticks=baseline,
                harm_delta=delta,
                harmful=delta > threshold,
                cf_wallclock_ms=cf_ms,
            )
        )
    return results


def make_counterfactual_hook(config, on_decision):
    def hook(decision_index, record, proposals, pre_action_sim):
        results = evaluate_decision(pre_action_sim, proposals, config)
        on_decision(decision_index, record, results, pre_action_sim)

    return hook
