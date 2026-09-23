from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

DECISION_DEADLINE = 20.0
RESERVE = DECISION_DEADLINE * 0.25
PROVIDER_CEILING = 30.0
MAX_STEPS = 4


def journal_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip() and json.loads(line).get("event", "response") in ("response", "provider_error")]


def planned_steps(trace):
    offsets = trace["clock_offsets"]
    steps = []
    for index in range(trace["steps"]):
        sent_at = offsets[1 + 2 * index]
        remaining = DECISION_DEADLINE - sent_at
        final = index == MAX_STEPS - 1 or remaining <= RESERVE
        budget = remaining if final else remaining - RESERVE
        timeout = min(PROVIDER_CEILING, budget)
        returned_at = offsets[2 + 2 * index] if 2 + 2 * index < len(offsets) else None
        steps.append({"step": index + 1, "final_call": final, "sent_at_s": sent_at,
                      "remaining_decision_s": remaining, "timeout_sent_s": timeout,
                      "server_header_s": math.ceil(timeout) if timeout > 0 else None,
                      "returned_at_s": returned_at, "reaches_provider": timeout > 0})
    return steps


def episode_calls(shard):
    rows = journal_rows(shard["llm_journal_path"])
    calls = []
    cursor = 0
    for record in shard["records"]:
        trace = record["decision_trace"]
        for step in planned_steps(trace):
            if not step["reaches_provider"]:
                calls.append({**step, "kind": "no_time_left", "outcome": trace["outcome"]})
                continue
            row = rows[cursor]
            cursor += 1
            kind = ("cache_hit" if row.get("cache_hit") else "success") if row.get("event", "response") == "response" \
                else row.get("error_type")
            calls.append({**step, "kind": kind, "http_status": row.get("http_status"),
                          "provider_status": row.get("provider_status"),
                          "elapsed_s": row.get("latency_ms", 0.0) / 1000,
                          "provider_attempts": row.get("provider_attempts", 1),
                          "arm": shard["arm"], "seed": shard["seed"], "decision_index": record["decision_index"],
                          "retry_index": record["retry_index"], "decision_outcome": trace["outcome"]})
    if cursor != len(rows):
        raise ValueError(f"{shard['arm']} seed {shard['seed']}: {len(rows) - cursor} journal rows unmatched")
    return calls


def describe(values):
    if not values:
        return None
    array = np.array(values)
    return {"n": len(values), "min": float(array.min()), "p10": float(np.percentile(array, 10)),
            "median": float(np.median(array)), "p90": float(np.percentile(array, 90)), "max": float(array.max())}


def diagnose(root):
    calls = []
    for path in sorted((Path(root) / "shards").glob("*__*.json")):
        calls.extend(episode_calls(json.loads(path.read_text(encoding="utf-8"))))
    failed = [c for c in calls if c["kind"] in ("provider_error", "timeout")]
    succeeded = [c for c in calls if c["kind"] == "success"]
    by_step = defaultdict(Counter)
    for c in calls:
        if c["kind"] in ("provider_error", "timeout", "success"):
            by_step[c["step"]]["failed" if c["kind"] != "success" else "success"] += 1
    for c in failed:
        c["elapsed_over_timeout"] = c["elapsed_s"] / c["timeout_sent_s"]
    return {
        "calls_matched": len([c for c in calls if c["kind"] != "no_time_left"]),
        "failed_calls": failed,
        "failures_by_step": {step: dict(counts) for step, counts in sorted(by_step.items())},
        "remaining_decision_budget_s": {"failed": describe([c["remaining_decision_s"] for c in failed]),
                                        "successful": describe([c["remaining_decision_s"] for c in succeeded])},
        "timeout_sent_s": {"failed": describe([c["timeout_sent_s"] for c in failed]),
                           "successful": describe([c["timeout_sent_s"] for c in succeeded])},
        "elapsed_s": {"failed": describe([c["elapsed_s"] for c in failed]),
                      "successful": describe([c["elapsed_s"] for c in succeeded])},
        "elapsed_over_timeout_failed": describe([c["elapsed_over_timeout"] for c in failed]),
        "failed_well_before_timeout": int(sum(c["elapsed_over_timeout"] < 0.9 for c in failed)),
        "successful_above_failed_timeout_median": int(sum(
            c["elapsed_s"] > np.median([f["timeout_sent_s"] for f in failed]) for c in succeeded)) if failed else 0,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = diagnose(args.root)
    Path(args.output).write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "failed_calls"}, indent=1))
    for c in result["failed_calls"]:
        print(f"{c['arm']:>13} s{c['seed']} d{c['decision_index']} r{c['retry_index']} step {c['step']} "
              f"final={c['final_call']!s:5} sent_at={c['sent_at_s']:5.2f} remaining={c['remaining_decision_s']:5.2f} "
              f"timeout={c['timeout_sent_s']:5.2f} header={c['server_header_s']} elapsed={c['elapsed_s']:5.2f} "
              f"{c['kind']} {c['http_status']} {c['provider_status']}")


if __name__ == "__main__":
    main()
