from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict

import numpy as np

try:
    from .gateway_uncertainty_ablation import wilcoxon_exact_dp
except ImportError:
    from gateway_uncertainty_ablation import wilcoxon_exact_dp


OUTCOMES = (
    "decision_produced",
    "deliberate_no_op",
    "tool_budget_exhausted",
    "final_parse_failure",
    "validation_exhausted",
    "timeout",
    "provider_budget_exhausted",
    "provider_error",
)


def outcome_distributions(records, retry_cap=2):
    grouped = defaultdict(dict)
    for record in records:
        outcome = record.get("decision_outcome")
        if outcome not in OUTCOMES:
            raise ValueError(f"Unknown or missing decision outcome: {outcome!r}")
        seed = record.get("seed")
        decision = record.get("decision_index")
        retry = record.get("retry_index")
        if any(type(value) is not int or value < 0 for value in (seed, decision, retry)):
            raise ValueError("Every record needs nonnegative integer seed, decision_index, retry_index")
        if retry > retry_cap:
            raise ValueError(f"Retry index {retry} exceeds cap {retry_cap}")
        group = grouped[(seed, decision)]
        if retry in group:
            raise ValueError(f"Duplicate attempt at seed {seed}, decision {decision}, retry {retry}")
        action = record.get("proposed_action") or {}
        action_type = action.get("type")
        if not action_type:
            raise ValueError("Missing proposed action")
        if outcome == "decision_produced" and action_type == "no_op":
            raise ValueError("decision_produced must carry a non-no-op action")
        if outcome != "decision_produced" and action_type != "no_op":
            raise ValueError(f"{outcome} must carry no_op")
        fallback = record.get("decision_fallback")
        if fallback is not (outcome not in ("decision_produced", "deliberate_no_op")):
            raise ValueError(f"Fallback flag inconsistent with {outcome}")
        group[retry] = outcome
    for (seed, decision), group in grouped.items():
        if sorted(group) != list(range(len(group))):
            raise ValueError(f"Missing attempt at seed {seed}, decision {decision}")

    def table(values, unit):
        counts = Counter(values)
        total = sum(counts.values())
        return {
            "total": total,
            "counts": {outcome: counts[outcome] for outcome in OUTCOMES},
            "percent": {
                outcome: 100 * counts[outcome] / total if total else None
                for outcome in OUTCOMES
            },
            "unclassified": 0,
            "unit": unit,
        }

    return {
        "per_attempt": table(
            (value for group in grouped.values() for value in group.values()),
            "one decide() attempt, including gate rejection retries",
        ),
        "per_decision_point": table(
            (group[0] for group in grouped.values()),
            "first decide() attempt at each scheduled decision point",
        ),
        "per_terminal_decision": table(
            (group[max(group)] for group in grouped.values()),
            "last decide() attempt at each scheduled decision point; action may still be rejected",
        ),
    }


def _finite_differences(diffs):
    values = np.asarray(list(diffs), dtype=float)
    if values.ndim != 1 or not len(values) or not np.isfinite(values).all():
        raise ValueError("Differences must be a nonempty finite one-dimensional sequence")
    return values


def exact_empirical_bootstrap_power(diffs, n=None, simulations=2000, seed=20260922, alpha=0.05):
    values = _finite_differences(diffs)
    n = len(values) if n is None else n
    if type(n) is not int or n < 1 or type(simulations) is not int or simulations < 1:
        raise ValueError("n and simulations must be positive integers")
    if not 0 < alpha < 1:
        raise ValueError("alpha must lie in (0, 1)")
    rng = np.random.default_rng(seed)
    hits = 0
    for indices in rng.integers(0, len(values), size=(simulations, n)):
        hits += wilcoxon_exact_dp(values[indices].tolist())["p_value"] < alpha
    point = hits / simulations
    z = statistics.NormalDist().inv_cdf(0.975)
    denominator = 1 + z * z / simulations
    center = (point + z * z / (2 * simulations)) / denominator
    half_width = z * math.sqrt(point * (1 - point) / simulations + z * z / (4 * simulations ** 2)) / denominator
    return {
        "power": point,
        "mcse": math.sqrt(point * (1 - point) / simulations),
        "monte_carlo_ci95": [max(0.0, center - half_width), min(1.0, center + half_width)],
        "rejections": hits,
        "simulations": simulations,
        "n": n,
        "alpha": alpha,
        "rng_seed": seed,
        "method": "empirical paired-seed bootstrap; exact two-sided signed-rank sign enumeration",
        "zero_method": "drop zero differences; average tied absolute ranks",
        "multiplicity": "unadjusted per-comparison alpha",
        "caveat": "Post-hoc sensitivity under the observed empirical differences, not prospective power. "
                  "Monte Carlo error excludes uncertainty in the effect distribution. Exact test "
                  "calibration assumes independent symmetric differences under its null.",
    }


def normal_approximation_required_n(diffs, power=0.8, alpha=0.05):
    values = _finite_differences(diffs)
    if len(values) < 2 or not 0 < power < 1 or not 0 < alpha < 1:
        raise ValueError("Need at least two differences and power and alpha in (0, 1)")
    mean = float(values.mean())
    sd = float(values.std(ddof=1))
    normal = statistics.NormalDist()
    z = normal.inv_cdf(1 - alpha / 2) + normal.inv_cdf(power)
    required = math.ceil((z * sd / abs(mean)) ** 2) if mean and sd else None
    return {
        "n": required,
        "target_power": power,
        "alpha": alpha,
        "observed_mean": mean,
        "observed_sd": sd,
        "method": "ceil(((z_(1-alpha/2) + z_power) * observed_sd / abs(observed_mean)) ** 2)",
        "caveat": "Unadjusted normal approximation for a paired mean under the observed effect and "
                  "variance; not an exact Wilcoxon sample-size requirement or a guarantee. Sparse "
                  "nonzero differences and estimated effect uncertainty can invalidate this planning heuristic.",
        "not_estimable_reason": (
            "Observed mean is zero" if mean == 0 else "Observed variance is zero" if sd == 0 else None
        ),
    }
