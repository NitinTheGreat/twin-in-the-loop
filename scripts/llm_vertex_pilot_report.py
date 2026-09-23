from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm_study_statistics import OUTCOMES, outcome_distributions

ARMS = ("ungated", "always_reject", "twin@1.0", "twin@0.6")
ARM_LABELS = {"ungated": "ungated", "always_reject": "reject-all", "twin@1.0": "twin@1.0",
              "twin@0.6": "twin@0.6"}
PRICING = {
    "source": "https://cloud.google.com/vertex-ai/generative-ai/pricing",
    "checked": "2026-09-23",
    "model": "gemini-3.6-flash",
    "location": "global",
    "tier": "Standard (on-demand), <= 200K input tokens",
    "billing_rule": "Only requests returning HTTP 200 are charged",
    "schedules": {
        "introductory_through_2026_12_31": {"input": 0.75, "cached_input": 0.075, "output": 3.75,
                                            "thinking": 3.75},
        "standard_from_2027_01_01": {"input": 1.50, "cached_input": 0.15, "output": 7.50,
                                     "thinking": 7.50},
    },
    "thinking_note": "Vertex bills 'Text output (response and reasoning)' at one rate; thinking tokens are billed as output",
}
SERVER_DEADLINE = {(504, "DEADLINE_EXCEEDED"), (499, "CANCELLED")}
LABEL_KEYS = {"seed", "decision_index", "retry_index"}
FULL_RUN_SEEDS = 72
HEADROOM = 1.25


def journal_rows(path):
    target = Path(path)
    if not target.exists():
        return []
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]


def price(usage, rates):
    uncached = usage["input_tokens"] - usage["cached_input_tokens"]
    return (uncached * rates["input"] + usage["cached_input_tokens"] * rates["cached_input"]
            + usage["visible_output_tokens"] * rates["output"]
            + usage["thinking_tokens"] * rates["thinking"]) / 1e6


def episode_usage(rows):
    usage = Counter()
    traffic = Counter()
    versions = Counter()
    errors = Counter()
    retry_errors = Counter()
    for row in rows:
        event = row.get("event", "response")
        if event in ("response", "provider_error") and not row.get("cache_hit"):
            attempts = row.get("provider_attempts", 1)
            usage["http_attempts"] += attempts
            usage["retried_calls"] += attempts > 1
            usage["rescued_by_retry"] += attempts > 1 and event == "response"
            retry_errors.update(row.get("provider_retry_errors", []))
        if event == "provider_error":
            errors[f"{row.get('error_type', 'unknown')} HTTP {row.get('http_status')} {row.get('provider_status')}"] += 1
            usage["failed_calls"] += 1
            usage["failed_reserved_tokens"] += row.get("reserved_tokens", 0)
            if row.get("http_status") is None:
                usage["failed_client_side"] += 1
                usage["client_side_reserved_tokens"] += row.get("reserved_tokens", 0)
            else:
                usage["failed_http_non200"] += 1
            if (row.get("http_status"), row.get("provider_status")) in SERVER_DEADLINE:
                usage["failed_server_deadline"] += 1
            continue
        if event != "response":
            usage[f"{event}_events"] += 1
            continue
        if row["cache_hit"]:
            usage["cache_hits"] += 1
            continue
        meta = row.get("usage_metadata") or {}
        usage["paid_calls"] += 1
        usage["input_tokens"] += meta.get("promptTokenCount", 0)
        usage["cached_input_tokens"] += meta.get("cachedContentTokenCount", 0)
        usage["visible_output_tokens"] += meta.get("candidatesTokenCount", 0)
        usage["thinking_tokens"] += meta.get("thoughtsTokenCount", 0)
        usage["tool_use_prompt_tokens"] += meta.get("toolUsePromptTokenCount", 0)
        usage["total_tokens"] += meta.get("totalTokenCount", 0)
        usage["guard_tokens"] += row["tokens_in"] + row["tokens_out"]
        usage["missing_usage_metadata"] += not meta
        usage["incomplete_usage"] += not row.get("usage_complete", False)
        usage["thinking_mismatch"] += row.get("tokens_thinking", 0) != meta.get("thoughtsTokenCount", 0)
        traffic[meta.get("trafficType", "unreported")] += 1
        versions[row.get("model_version") or "unreported"] += 1
    keys = ("paid_calls", "cache_hits", "failed_calls", "input_tokens", "cached_input_tokens",
            "visible_output_tokens", "thinking_tokens", "tool_use_prompt_tokens", "total_tokens",
            "guard_tokens", "failed_reserved_tokens", "failed_client_side", "failed_http_non200",
            "failed_server_deadline", "client_side_reserved_tokens", "missing_usage_metadata",
            "incomplete_usage", "thinking_mismatch", "http_attempts", "retried_calls", "rescued_by_retry")
    result = {key: usage.get(key, 0) for key in keys}
    result["other_events"] = {k: v for k, v in usage.items() if k.endswith("_events")}
    result["provider_errors"] = dict(errors)
    result["retry_errors"] = dict(retry_errors)
    result["traffic_type"] = dict(traffic)
    result["model_versions"] = dict(versions)
    return result


def add_costs(usage):
    for name, rates in PRICING["schedules"].items():
        usage[f"cost_usd_{name}"] = price(usage, rates)
        usage[f"failed_call_exposure_usd_{name}"] = usage["client_side_reserved_tokens"] * rates["output"] / 1e6
    output = usage["visible_output_tokens"] + usage["thinking_tokens"]
    usage["thinking_share_of_output"] = usage["thinking_tokens"] / output if output else None
    return usage


def summed(items):
    keys = [k for k, v in items[0].items() if isinstance(v, (int, float)) and not isinstance(v, bool)
            and not k.startswith("thinking_share") and k not in LABEL_KEYS]
    total = {k: sum(item[k] for item in items) for k in keys}
    total["seeds"] = sorted({item["seed"] for item in items if "seed" in item})
    total["provider_errors"] = dict(sum((Counter(i["provider_errors"]) for i in items), Counter()))
    total["retry_errors"] = dict(sum((Counter(i["retry_errors"]) for i in items), Counter()))
    total["traffic_type"] = dict(sum((Counter(i["traffic_type"]) for i in items), Counter()))
    total["model_versions"] = dict(sum((Counter(i["model_versions"]) for i in items), Counter()))
    total["other_events"] = dict(sum((Counter(i["other_events"]) for i in items), Counter()))
    output = total["visible_output_tokens"] + total["thinking_tokens"]
    total["thinking_share_of_output"] = total["thinking_tokens"] / output if output else None
    return total


def first_attempt(records):
    table = outcome_distributions(records)["per_decision_point"]
    return {"total": table["total"], "counts": {k: table["counts"].get(k, 0) for k in OUTCOMES},
            "percent": {k: 100 * table["counts"].get(k, 0) / table["total"] for k in OUTCOMES}}


def historical_paid_calls_per_seed(shards):
    per_seed = Counter()
    for path in Path(shards).glob("*__*.json"):
        shard = json.loads(path.read_text(encoding="utf-8"))
        per_seed[shard["seed"]] += shard["budget_calls_after"] - shard["budget_calls_before"]
    return dict(sorted(per_seed.items()))


def ceil_to(value, step):
    return int(math.ceil(value / step) * step)


def build(args):
    root = Path(args.root)
    shards = root / "shards"
    allocation = json.loads((shards / "_allocation.json").read_text(encoding="utf-8"))
    ledger = json.loads((shards / "_budget.json").read_text(encoding="utf-8"))
    episodes = []
    records_by_arm = {arm: [] for arm in ARMS}
    for path in sorted(shards.glob("*__*.json")):
        shard = json.loads(path.read_text(encoding="utf-8"))
        rows = journal_rows(shard["llm_journal_path"])
        usage = add_costs(episode_usage(rows))
        records_by_arm[shard["arm"]].extend(shard["records"])
        episodes.append({"arm": shard["arm"], "seed": shard["seed"],
                         "violation_ticks": shard["violation_ticks"], "wall_seconds": shard["wall_seconds"],
                         "ledger_calls": shard["budget_calls_after"] - shard["budget_calls_before"],
                         "ledger_tokens": shard["budget_tokens_after"] - shard["budget_tokens_before"],
                         **usage})
    episodes.sort(key=lambda e: (e["seed"], ARMS.index(e["arm"])))
    total = summed(episodes)
    seeds = sorted({e["seed"] for e in episodes})
    complete_seeds = [s for s in seeds if {e["arm"] for e in episodes if e["seed"] == s} == set(ARMS)]
    per_seed = {s: {**summed([e for e in episodes if e["seed"] == s]), "seed": s} for s in seeds}
    reconciliation = {
        "ledger_calls": ledger["calls"], "journal_paid_plus_failed": total["paid_calls"] + total["failed_calls"],
        "ledger_tokens": ledger["tokens"],
        "journal_guard_plus_reserved": total["guard_tokens"] + total["failed_reserved_tokens"],
        "ledger_in_flight": ledger["in_flight"], "ledger_blocked_reason": ledger["blocked_reason"],
        "ceiling_calls": ledger["max_calls"], "ceiling_tokens": ledger["max_tokens"],
    }
    reconciliation["calls_reconcile"] = reconciliation["ledger_calls"] == reconciliation["journal_paid_plus_failed"]
    reconciliation["tokens_reconcile"] = reconciliation["ledger_tokens"] == reconciliation["journal_guard_plus_reserved"]
    historical = json.loads(Path(args.historical_json).read_text(encoding="utf-8"))
    outcomes = {arm: first_attempt(records_by_arm[arm]) for arm in ARMS if records_by_arm[arm]}
    pooled_counts = {k: sum(outcomes[a]["counts"][k] for a in ARMS if a in outcomes) for k in OUTCOMES}
    pooled_total = sum(pooled_counts.values())
    outcomes["pooled"] = {"total": pooled_total, "counts": pooled_counts,
                          "percent": {k: 100 * v / pooled_total for k, v in pooled_counts.items()}}
    historical_outcomes = {arm: historical["arms"][arm]["outcomes"]["per_decision_point"]["percent"]
                           for arm in ARMS}
    projection = None
    if complete_seeds:
        historical_calls = historical_paid_calls_per_seed(args.historical_shards)
        pilot_calls = sum(per_seed[s]["paid_calls"] + per_seed[s]["failed_calls"] for s in complete_seeds)
        historical_same = sum(historical_calls[s] for s in complete_seeds)
        scale = pilot_calls / historical_same
        values = np.array(list(historical_calls.values()), dtype=float) * scale
        rng = np.random.default_rng(20260923)
        boot = rng.choice(values, size=(20000, FULL_RUN_SEEDS), replace=True).sum(axis=1)
        calls_per_paid_seed = pilot_calls / len(complete_seeds)
        projection = {"full_run_seeds": FULL_RUN_SEEDS, "complete_pilot_seeds": complete_seeds,
                      "pilot_calls_per_seed": calls_per_paid_seed,
                      "historical_calls_same_seeds": historical_same,
                      "historical_calls_per_seed_all": historical_calls,
                      "pilot_to_historical_call_ratio": scale,
                      "historical_mean_calls_per_seed": float(np.mean(list(historical_calls.values())))}
        seed_totals = [per_seed[s] for s in complete_seeds]
        per_call = {k: sum(t[k] for t in seed_totals) / pilot_calls
                    for k in ("guard_tokens", "failed_reserved_tokens", "input_tokens", "thinking_tokens",
                              "visible_output_tokens")}
        per_call["guard_tokens"] += per_call.pop("failed_reserved_tokens")
        projection["per_call"] = per_call
        calls_point = float(values.mean() * FULL_RUN_SEEDS)
        calls_low, calls_high = (float(x) for x in np.percentile(boot, [2.5, 97.5]))
        pilot_seed_calls = [per_seed[s]["paid_calls"] + per_seed[s]["failed_calls"] for s in complete_seeds]
        projection["calls"] = {"point": calls_point, "bootstrap_95": [calls_low, calls_high],
                               "pilot_seed_extremes": [min(pilot_seed_calls) * FULL_RUN_SEEDS,
                                                       max(pilot_seed_calls) * FULL_RUN_SEEDS],
                               "naive_pilot_mean": calls_per_paid_seed * FULL_RUN_SEEDS}
        costs = {}
        for name in PRICING["schedules"]:
            cost_per_call = sum(t[f"cost_usd_{name}"] for t in seed_totals) / pilot_calls
            per_seed_costs = [t[f"cost_usd_{name}"] for t in seed_totals]
            low = min(calls_low, min(pilot_seed_calls) * FULL_RUN_SEEDS) * cost_per_call
            high = max(calls_high, max(pilot_seed_calls) * FULL_RUN_SEEDS) * cost_per_call
            costs[name] = {"cost_per_call": cost_per_call, "point": calls_point * cost_per_call,
                           "range": [low, high], "naive_pilot_mean": np.mean(per_seed_costs) * FULL_RUN_SEEDS,
                           "failed_call_exposure_per_seed": sum(t[f"failed_call_exposure_usd_{name}"]
                                                                for t in seed_totals) / len(complete_seeds)}
        projection["cost_usd"] = costs
        tokens_point = calls_point * per_call["guard_tokens"]
        projection["guard_tokens_point"] = tokens_point
        projection["recommended_ceiling"] = {
            "headroom": HEADROOM,
            "max_calls": ceil_to(calls_point * HEADROOM, 100),
            "max_tokens": ceil_to(tokens_point * HEADROOM, 1_000_000),
            "covers_bootstrap_upper_calls": ceil_to(calls_point * HEADROOM, 100) >= calls_high,
            "cost_at_call_ceiling_usd": {name: ceil_to(calls_point * HEADROOM, 100) * c["cost_per_call"]
                                         for name, c in costs.items()},
        }
    return {"pricing": PRICING, "run_identity": allocation["experiment_identity"]["run"],
            "experiment_hash": allocation["experiment_hash"], "budget_policy": allocation["policy"],
            "workers": allocation["workers"], "seeds": seeds, "episodes": episodes, "per_seed": per_seed,
            "total": total, "reconciliation": reconciliation, "first_attempt_outcomes": outcomes,
            "historical_first_attempt_percent": historical_outcomes, "projection": projection}


def money(value):
    return f"${value:,.4f}"


def render(report):
    run = report["run_identity"]
    total = report["total"]
    intro, later = PRICING["schedules"]
    lines = ["# Vertex AI live-model pilot: cost and outcome measurement", "",
             f"Seeds {report['seeds']} x four LLM arms on Vertex AI. Pilot only; the 72-seed run was not started.", "",
             "## Run identity", "", "| Item | Value |", "|---|---|"]
    for key in ("provider", "provider_platform", "auth", "vertex_project", "vertex_location", "model",
                "temperature", "prompt_version", "prompt_sha256", "parser_version", "parser_sha256",
                "parser_matches_verified", "max_output_tokens", "provider_max_attempts"):
        lines.append(f"| {key} | {run[key]} |")
    lines.append(f"| thinking_config | {json.dumps(run['thinking_config'])} |")
    lines.append(f"| budget | {report['budget_policy']}, {report['reconciliation']['ceiling_calls']} calls / "
                 f"{report['reconciliation']['ceiling_tokens']:,} tokens, {report['workers']} workers |")
    lines.append(f"| experiment_hash | {report['experiment_hash']} |")
    lines += ["", "## Pricing", "",
              f"Source: {PRICING['source']} (checked {PRICING['checked']}). {PRICING['tier']}, location "
              f"{PRICING['location']}. {PRICING['thinking_note']}. {PRICING['billing_rule']}.", "",
              "| Schedule | Input /M | Cached input /M | Output /M | Thinking /M |", "|---|---|---|---|---|"]
    for name, rates in PRICING["schedules"].items():
        lines.append(f"| {name} | ${rates['input']} | ${rates['cached_input']} | ${rates['output']} | ${rates['thinking']} |")
    lines += ["", "## Usage and cost per episode", "",
              "| Seed | Arm | Paid calls | Cache hits | Failed | Input | Cached input | Visible output | Thinking | Thinking share | Cost (2026 rate) | Cost (2027 rate) |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    rows = report["episodes"] + [dict(report["total"], seed="all", arm="total")]
    identity_attempts = report["run_identity"].get("provider_max_attempts")
    for e in rows:
        share = "n/a" if e["thinking_share_of_output"] is None else f"{100 * e['thinking_share_of_output']:.1f}%"
        lines.append(f"| {e['seed']} | {ARM_LABELS.get(e['arm'], e['arm'])} | {e['paid_calls']} | {e['cache_hits']} | "
                     f"{e['failed_calls']} | {e['input_tokens']:,} | {e['cached_input_tokens']:,} | "
                     f"{e['visible_output_tokens']:,} | {e['thinking_tokens']:,} | {share} | "
                     f"{money(e['cost_usd_' + intro])} | {money(e['cost_usd_' + later])} |")
    rec = report["reconciliation"]
    lines += ["", f"Provider errors: {total['provider_errors'] or 'none'}. Traffic type: {total['traffic_type']}. "
              f"Resolved model versions: {total['model_versions']}. Responses without usage metadata: "
              f"{total['missing_usage_metadata']}; thinking-field mismatches: {total['thinking_mismatch']}.",
              f"Failed calls: {total['failed_calls']} ({total['failed_http_non200']} non-200 HTTP responses, not billed "
              f"under the Vertex rule; {total['failed_client_side']} client-side timeouts with unknown billing; "
              f"{total['failed_server_deadline']} + {total['failed_client_side']} = "
              f"{total['failed_server_deadline'] + total['failed_client_side']} deadline-related). "
              f"HTTP attempts: {total['http_attempts']} for {total['paid_calls'] + total['failed_calls']} provider calls "
              f"(max {identity_attempts} per call); calls that retried: {total['retried_calls']}, of which "
              f"{total['rescued_by_retry']} then succeeded; retried errors: {total['retry_errors'] or 'none'}. Upper-bound exposure "
              f"if every client-side timeout were billed at its full reservation as output: "
              f"{money(total['failed_call_exposure_usd_' + intro])} (2026 rate).",
              f"Ledger reconciliation: calls {rec['ledger_calls']} vs journal {rec['journal_paid_plus_failed']} "
              f"({'ok' if rec['calls_reconcile'] else 'MISMATCH'}); tokens {rec['ledger_tokens']:,} vs journal "
              f"{rec['journal_guard_plus_reserved']:,} ({'ok' if rec['tokens_reconcile'] else 'MISMATCH'}); "
              f"in flight {rec['ledger_in_flight']}; blocked {rec['ledger_blocked_reason']}.", "",
              "## First-attempt outcomes", "",
              "| Outcome | " + " | ".join(ARM_LABELS[a] for a in ARMS if a in report["first_attempt_outcomes"]) + " | Pooled | Historical range |",
              "|---" * (sum(a in report["first_attempt_outcomes"] for a in ARMS) + 3) + "|"]
    outcomes = report["first_attempt_outcomes"]
    for outcome in OUTCOMES:
        cells = [f"{outcomes[a]['counts'][outcome]} ({outcomes[a]['percent'][outcome]:.1f}%)"
                 for a in ARMS if a in outcomes]
        hist = [report["historical_first_attempt_percent"][a].get(outcome, 0.0) for a in ARMS]
        pooled = outcomes["pooled"]
        lines.append(f"| {outcome} | " + " | ".join(cells) +
                     f" | {pooled['counts'][outcome]} ({pooled['percent'][outcome]:.1f}%) | "
                     f"{min(hist):.1f}-{max(hist):.1f}% |")
    sizes = ", ".join(f"{ARM_LABELS[a]} N={outcomes[a]['total']}" for a in ARMS if a in outcomes)
    lines.append(f"\nFirst attempts per arm: {sizes}.")
    history = report["historical_first_attempt_percent"].values()
    lines += ["", "Deadline-related first attempts (timeout + provider_error):", "",
              "| Arm | Count | Share | Historical timeout + provider_error |", "|---|---|---|---|"]
    for arm in [a for a in ARMS if a in outcomes] + ["pooled"]:
        counts = outcomes[arm]["counts"]
        deadline = counts["timeout"] + counts["provider_error"]
        hist = (report["historical_first_attempt_percent"][arm].get("timeout", 0.0)
                + report["historical_first_attempt_percent"][arm].get("provider_error", 0.0)) if arm in ARMS else None
        lines.append(f"| {ARM_LABELS.get(arm, arm)} | {deadline}/{outcomes[arm]['total']} | "
                     f"{100 * deadline / outcomes[arm]['total']:.1f}% | "
                     f"{'-' if hist is None else f'{hist:.1f}%'} |")
    lines.append(f"\nHistorical timeout range across arms: {min(h.get('timeout', 0.0) for h in history):.1f}-"
                 f"{max(h.get('timeout', 0.0) for h in report['historical_first_attempt_percent'].values()):.1f}%.")
    projection = report["projection"]
    if projection:
        calls = projection["calls"]
        ceiling = projection["recommended_ceiling"]
        lines += ["", "## Projection for 72 seeds", "",
                  f"Pilot used {projection['pilot_calls_per_seed']:.1f} provider calls per seed; the historical run used "
                  f"{projection['historical_calls_same_seeds']} on the same seeds (ratio "
                  f"{projection['pilot_to_historical_call_ratio']:.3f}) and {projection['historical_mean_calls_per_seed']:.1f} "
                  "per seed on average over 30 seeds. The projection rescales the 30 historical per-seed call counts by "
                  "that ratio, bootstraps 72-seed totals, and prices calls at the pilot's measured cost per call. "
                  "The range is the wider of the bootstrap 95% interval and 72 x the cheaper/dearer pilot seed.", "",
                  f"Projected provider calls: {calls['point']:,.0f} (bootstrap 95% {calls['bootstrap_95'][0]:,.0f}-"
                  f"{calls['bootstrap_95'][1]:,.0f}; pilot-seed extremes {calls['pilot_seed_extremes'][0]:,.0f}-"
                  f"{calls['pilot_seed_extremes'][1]:,.0f}; naive pilot mean {calls['naive_pilot_mean']:,.0f}).", "",
                  "| Price schedule | Cost per call | Projected cost | Range | Naive pilot mean x 72 |", "|---|---|---|---|---|"]
        for name, c in projection["cost_usd"].items():
            lines.append(f"| {name} | {money(c['cost_per_call'])} | {money(c['point'])} | "
                         f"{money(c['range'][0])}-{money(c['range'][1])} | {money(c['naive_pilot_mean'])} |")
        lines += ["", f"Guard tokens per call (input + output + thinking, plus failed-call reservations): "
                      f"{projection['per_call']['guard_tokens']:,.0f}; projected guard tokens "
                      f"{projection['guard_tokens_point']:,.0f}.", "",
                  f"Recommended global ceiling ({int((HEADROOM - 1) * 100)}% above the point projection): "
                  f"**{ceiling['max_calls']:,} calls / {ceiling['max_tokens']:,} tokens**, one shared ledger for all "
                  f"workers. Covers the bootstrap upper bound on calls: {ceiling['covers_bootstrap_upper_calls']}. "
                  f"Spend at the call ceiling: " + ", ".join(f"{money(v)} ({k})" for k, v in ceiling['cost_at_call_ceiling_usd'].items()) + "."]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--historical-json", default=str(ROOT / "docs/research/llm_descriptive_study.json"))
    parser.add_argument("--historical-shards", default=str(ROOT / "results/llm_descriptive_study/source/shards"))
    parser.add_argument("--output", required=True)
    parser.add_argument("--markdown", required=True)
    args = parser.parse_args()
    report = build(args)
    Path(args.output).write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    Path(args.markdown).write_text(render(report), encoding="utf-8")
    print(render(report))


if __name__ == "__main__":
    main()
