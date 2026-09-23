import itertools
import math

import pytest

from scripts.llm_study_statistics import (
    OUTCOMES,
    exact_empirical_bootstrap_power,
    normal_approximation_required_n,
    outcome_distributions,
    wilcoxon_exact_dp,
)


@pytest.mark.parametrize("diffs", [
    [0, 0, 0],
    [0, 1, -1, 2],
    [1, 1, 1, -3, 0, -3],
    [0, -2, -2, 2, 4, -5],
    [1, 2, 3, 4, 5, 6],
])
def test_exact_signed_rank_matches_enumerated_sign_null(diffs):
    nonzero = [d for d in diffs if d]
    ranks = [
        sum(abs(other) < abs(d) for other in nonzero)
        + (sum(abs(other) == abs(d) for other in nonzero) + 1) / 2
        for d in nonzero
    ]
    observed = abs(sum(math.copysign(rank, d) for rank, d in zip(ranks, nonzero)))
    signs = list(itertools.product((-1, 1), repeat=len(nonzero)))
    expected = sum(
        abs(sum(sign * rank for sign, rank in zip(pattern, ranks))) >= observed
        for pattern in signs
    ) / len(signs)
    result = wilcoxon_exact_dp(diffs)
    assert result["p_value"] == expected
    assert result["n_nonzero"] == len(nonzero)


def record(seed, decision, retry, outcome):
    return {
        "seed": seed,
        "decision_index": decision,
        "retry_index": retry,
        "decision_outcome": outcome,
        "proposed_action": {"type": "restart_service" if outcome == "decision_produced" else "no_op"},
        "decision_fallback": outcome not in ("decision_produced", "deliberate_no_op"),
    }


def test_units_preserve_rejected_actions_and_terminal_fallbacks():
    records = [
        record(0, 0, 0, "decision_produced"),
        record(0, 0, 1, "decision_produced"),
        record(0, 0, 2, "final_parse_failure"),
        record(0, 1, 0, "deliberate_no_op"),
        record(1, 0, 0, "decision_produced"),
    ]
    output = outcome_distributions(list(reversed(records)))
    assert output["per_attempt"]["total"] == 5
    assert output["per_attempt"]["counts"]["decision_produced"] == 3
    assert output["per_decision_point"]["total"] == 3
    assert output["per_decision_point"]["counts"]["decision_produced"] == 2
    assert output["per_terminal_decision"]["counts"]["decision_produced"] == 1
    assert output["per_terminal_decision"]["counts"]["final_parse_failure"] == 1
    for table in output.values():
        assert set(table["counts"]) == set(OUTCOMES)
        assert sum(table["percent"].values()) == pytest.approx(100)
        assert table["counts"]["provider_error"] == 0


@pytest.mark.parametrize("records", [
    [record(0, 0, 0, "unclassified")],
    [record(0, 0, 1, "decision_produced")],
    [record(0, 0, 0, "decision_produced"), record(0, 0, 0, "decision_produced")],
    [record(0, 0, 0, "decision_produced"), record(0, 0, 2, "timeout")],
    [record(0, 0, 3, "timeout")],
    [{**record(0, 0, 0, "timeout"), "decision_fallback": False}],
    [{**record(0, 0, 0, "deliberate_no_op"), "proposed_action": {"type": "restart_service"}}],
    [{**record(0, 0, 0, "decision_produced"), "proposed_action": {"type": "no_op"}}],
    [{**record(0, 0, 0, "timeout"), "decision_index": None}],
])
def test_malformed_decision_records_fail_closed(records):
    with pytest.raises(ValueError):
        outcome_distributions(records)


def test_empty_distribution_percentages_are_not_estimable():
    for table in outcome_distributions([]).values():
        assert table["total"] == 0
        assert all(value == 0 for value in table["counts"].values())
        assert all(value is None for value in table["percent"].values())


def test_bootstrap_uses_exact_small_sample_rejection_boundary():
    below = exact_empirical_bootstrap_power([1] * 5, simulations=50)
    above = exact_empirical_bootstrap_power([1] * 6, simulations=50)
    assert below["power"] == 0
    assert above["power"] == 1
    assert above["mcse"] == 0


def test_zero_effect_is_uninformative_for_required_sample_size():
    assert exact_empirical_bootstrap_power([0] * 30, simulations=20)["power"] == 0
    result = normal_approximation_required_n([-1, 0, 1])
    assert result["n"] is None
    assert result["not_estimable_reason"] == "Observed mean is zero"


@pytest.mark.parametrize("diffs", [[], [float("nan")], [float("inf")]])
def test_nonfinite_differences_are_rejected(diffs):
    with pytest.raises(ValueError):
        exact_empirical_bootstrap_power(diffs)


def test_normal_heuristic_matches_known_planning_example():
    result = normal_approximation_required_n([-2, 0, 8, 0, -10, 0, -63, 3, 0, 0, 0, 0, 0, 0, 0,
                                               3, -10, 0, 0, -26, 0, 0, -8, 0, -18, 5, -11, 0, 0, 0])
    assert result["n"] == 72
    assert result["observed_mean"] == -4.3
