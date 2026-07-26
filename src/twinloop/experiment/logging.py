from __future__ import annotations

import json
from pathlib import Path


PROPOSAL_FIELDS = [
    "episode_id",
    "arm",
    "seed",
    "fidelity",
    "tick",
    "decision_index",
    "proposal_index",
    "agent_type",
    "prompt_version",
    "decision_interval_ticks",
    "proposed_action",
    "schema_valid",
    "semantically_valid",
    "twin_verdict",
    "twin_reason",
    "twin_predicted_violation_ticks_action",
    "twin_predicted_violation_ticks_noop",
    "counterfactual_violation_ticks_action",
    "counterfactual_violation_ticks_noop",
    "counterfactual_harm_delta",
    "ground_truth_harmful",
    "was_applied",
    "retry_index",
    "exhausted",
    "slo_violations_cumulative_before",
    "slo_violations_cumulative_after",
    "llm_latency_ms",
    "llm_tokens_in",
    "llm_tokens_out",
    "llm_cache_hit",
    "twin_wallclock_ms",
    "counterfactual_wallclock_ms",
]


def proposal_record(**fields) -> dict:
    return {name: fields.get(name) for name in PROPOSAL_FIELDS}


def episode_summary(**fields) -> dict:
    return dict(fields)


def append_jsonl(path, record) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def read_jsonl(path) -> list[dict]:
    target = Path(path)
    if not target.exists():
        return []
    records = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def completed_keys(summaries) -> set:
    return {(s["arm"], s["seed"], s["fidelity"]) for s in summaries}
