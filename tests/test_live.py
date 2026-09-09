from __future__ import annotations

import json

import pytest

from twinloop.dashboard.live import provider_status, run_stream

SHORT = dict(seed=15, ticks=30, interval=10, horizon=12)


def collect(**kwargs):
    return list(run_stream(**{**SHORT, **kwargs}))


def by_type(events):
    counts: dict[str, int] = {}
    for event in events:
        counts[event["type"]] = counts.get(event["type"], 0) + 1
    return counts


def test_stream_opens_with_meta_and_closes_with_done():
    events = collect(agent_kind="null", gate=False)
    assert events[0]["type"] == "meta"
    assert events[-1]["type"] == "done"
    assert events[0]["topology"]["nodes"]
    assert events[0]["config"]["seed"] == SHORT["seed"]


def test_every_event_is_json_serialisable():
    for event in collect(agent_kind="llm", provider_name="scripted", gate=True, fidelity=1.0):
        json.dumps(event)


def test_one_tick_event_per_tick():
    events = collect(agent_kind="null", gate=False)
    assert by_type(events)["tick"] == SHORT["ticks"]
    assert [e["tick"] for e in events if e["type"] == "tick"] == list(range(SHORT["ticks"]))


def test_gate_off_emits_no_verdicts():
    events = collect(agent_kind="llm", provider_name="scripted", gate=False)
    assert "verdict" not in by_type(events)
    assert by_type(events)["proposal"] >= 1


def test_gate_on_emits_a_verdict_for_every_proposal():
    events = collect(agent_kind="llm", provider_name="scripted", gate=True, fidelity=1.0)
    counts = by_type(events)
    assert counts["verdict"] == counts["proposal"]


def test_rejection_is_followed_by_a_retry_and_a_new_proposal():
    events = collect(agent_kind="llm", provider_name="scripted", gate=True, fidelity=1.0)
    rejected = [i for i, e in enumerate(events) if e["type"] == "verdict" and not e["approved"]]
    assert rejected, "this seed is expected to produce at least one rejection"
    for index in rejected:
        tail = [e["type"] for e in events[index + 1 : index + 4]]
        assert "retry" in tail or "exhausted" in tail


def test_llm_calls_carry_the_full_prompt_and_reply():
    events = collect(agent_kind="llm", provider_name="scripted", gate=False)
    calls = [e for e in events if e["type"] == "llm_call"]
    assert calls
    for call in calls:
        assert call["messages"][0]["role"] == "system"
        assert "autonomous remediation agent" in call["messages"][0]["content"]
        assert call["messages"][-1]["content"]
        assert call["response"]
        assert call["tokens_in"] > 0


def test_ground_truth_covers_every_proposal_including_rejected_ones():
    events = collect(agent_kind="llm", provider_name="scripted", gate=True, fidelity=1.0)
    proposals: dict[int, int] = {}
    truths: dict[int, int] = {}
    for event in events:
        if event["type"] == "proposal":
            proposals[event["tick"]] = proposals.get(event["tick"], 0) + 1
        if event["type"] == "truth":
            truths[event["tick"]] = len(event["rows"])
    assert proposals == truths


def test_counterfactual_can_be_switched_off():
    events = collect(agent_kind="rule", gate=False, counterfactual=False)
    assert "truth" not in by_type(events)


WALLCLOCK = {"cost_ms", "latency_ms", "cf_ms", "twin_ms"}


def _without_timings(event):
    if isinstance(event, dict):
        return {k: _without_timings(v) for k, v in event.items() if k not in WALLCLOCK}
    if isinstance(event, list):
        return [_without_timings(v) for v in event]
    return event


def test_identical_options_produce_identical_streams():
    a = collect(agent_kind="llm", provider_name="scripted", gate=True, fidelity=0.5)
    b = collect(agent_kind="llm", provider_name="scripted", gate=True, fidelity=0.5)
    strip = lambda events: [
        json.dumps(_without_timings(e), sort_keys=True) for e in events if e["type"] != "llm_call"
    ]
    assert strip(a) == strip(b)


def test_cache_only_provider_reports_a_miss_instead_of_crashing():
    events = collect(agent_kind="llm", provider_name="cached", gate=False)
    errors = [e for e in events if e["type"] == "error"]
    assert errors, "seed 15 is not in the recorded transcript, so a miss is expected"
    assert errors[0]["hint"]
    assert events[-1]["type"] == "error"


def test_provider_status_lists_every_brain():
    status = provider_status(".")
    assert set(status) == {"scripted", "cached", "gemini", "local"}
    assert status["scripted"]["available"] is True
    for info in status.values():
        assert info["label"] and info["note"]


def test_done_totals_agree_with_the_streamed_events():
    events = collect(agent_kind="llm", provider_name="scripted", gate=True, fidelity=1.0)
    counts = by_type(events)
    done = events[-1]
    assert done["totals"]["proposals"] == counts["proposal"]
    assert done["totals"]["llm_calls"] == counts.get("llm_call", 0)
    assert done["totals"]["retries"] == counts.get("retry", 0)
    harmful = sum(1 for e in events if e["type"] == "truth" for r in e["rows"] if r["harmful"])
    assert done["totals"]["harmful"] == harmful
