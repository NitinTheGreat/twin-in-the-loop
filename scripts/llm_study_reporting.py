from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from gateway_uncertainty_ablation import holm
from llm_study_statistics import exact_empirical_bootstrap_power, normal_approximation_required_n

ROOT = Path(__file__).resolve().parents[1]
ARM_NAMES = {"ungated": "LLM ungated", "always_reject": "LLM reject-all",
             "twin@1.0": "LLM twin@1.0", "twin@0.6": "LLM twin@0.6"}
ACTION_TYPES = ("scale_service", "restart_service", "migrate_service", "throttle_service", "reroute_traffic")
OUTCOMES = ("decision_produced", "deliberate_no_op", "tool_budget_exhausted", "final_parse_failure",
            "validation_exhausted", "timeout", "provider_budget_exhausted", "provider_error")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def enrich_analysis(output, data, statuses, args):
    seeds = output["provenance"]["seeds"]
    sweep = json.loads(Path(args.sweep_json).read_text(encoding="utf-8"))
    reference = sweep["conditions"]["discrete_switch"]
    a0 = [reference["a0_per_seed"][s] for s in seeds]
    if a0 != output["per_seed_violation_ticks"]["always_reject"]:
        raise ValueError("Reject-all does not reproduce the existing A0 reference")
    all_records = [r for arm in data.values() for shard in arm.values() for r in shard["records"]]
    for r in all_records:
        if r["counterfactual_harm_delta"] != (r["counterfactual_violation_ticks_action"]
                                                - r["counterfactual_violation_ticks_noop"]):
            raise ValueError("Counterfactual delta mismatch")
        if r["ground_truth_harmful"] != (r["counterfactual_harm_delta"] > 3):
            raise ValueError("Ground-truth threshold mismatch")
        if r["decision_outcome"] == "decision_produced":
            if not r["schema_valid"] or not r["semantically_valid"] or r["proposed_action"]["type"] == "no_op":
                raise ValueError("Produced decision is not a valid non-no-op action")
        elif r["proposed_action"]["type"] != "no_op":
            raise ValueError("Non-produced decision did not resolve to no_op")
        if bool(r["decision_fallback"]) != (r["decision_outcome"] not in OUTCOMES[:2]):
            raise ValueError("Fallback and taxonomy disagree")
    registered = bool(statuses) and all(s.get("budget_policy") == "shared_global_ledger" for s in statuses)
    live = bool(statuses) and (registered or all(s.get("provider_max_attempts") == 1 for s in statuses))
    prompt_path = ROOT / "src/twinloop/agent/prompts/llm_agent_v1.txt"
    archived_prompt = ROOT / "results/llm_descriptive_study/runtime_snapshot/src/twinloop/agent/prompts/llm_agent_v1.txt"
    if not live:
        if not archived_prompt.exists():
            raise ValueError("Historical analysis requires the archived runtime prompt")
        prompt_path = archived_prompt
    prov = output["provenance"]
    prov.update({"study_kind": "corrected_live_run" if live else "historical_live_run_audit",
                 "data_collection_date": "2026-09-22" if live else "2026-09-20",
                 "analysis_date": "2026-09-22", "prompt_sha256": sha256(prompt_path),
                 "model_identifier_kind": "requested model identifier; resolved modelVersion is recorded per successful new response",
                 "a0_reference": {"source": "docs/research/extended_fidelity_sweep.json",
                                  "sha256": sha256(args.sweep_json), "seeds": seeds,
                                  "violation_ticks": a0, "rerun": False, "mismatches": 0},
                 "runtime": {k: statuses[0].get(k) if statuses else None for k in
                             ("max_output_tokens", "provider_max_attempts", "parser_policy")},
                 "cache_paths": [s["cache_path"] for s in statuses],
                 "fidelity_rationale": "1.0 is an established useful rule-controller operating point; 0.6 is near the scripted crossover 0.549 [0.434,0.677]. Queue simplification is off at both levels.",
                 "uncertainty": "5000 seed-cluster percentile bootstrap resamples, common seed indices across arms; n/e for zero denominators. These condition on one cached model realization and do not measure between-call model variability.",
                 "cache_key_policy": "SHA256(UTF8(model + NUL + str(temperature) + NUL + json.dumps(messages, sort_keys=True, ensure_ascii=False))). Full messages include the prompt. Provider URL, parser, output cap and modelVersion are not in the legacy key; this study pins them and records inherited-cache provenance."})
    prov["protocol"].update({"faults_per_episode": 3, "fault_min_start_tick": 20, "fault_max_start_tick": 260,
                             "react_timeout_seconds": 20.0, "provider_timeout_seconds": 30.0,
                             "react_final_reserve_fraction": 0.25, "internal_validation_retries": 2,
                             "decision_ticks": list(range(0, 120, 10)), "simplify_queueing": False})
    output["checks"] = {"episodes": sum(len(v) for v in data.values()), "expected_episodes": len(ARM_NAMES) * len(seeds),
                        "records": len(all_records), "unclassified": 0, "a0_mismatches": 0,
                        "delta_mismatches": 0, "label_mismatches": 0, "invalid_produced_actions": 0,
                        "twin_verdict_mismatches": 0, "complete_seed_cohort": True}
    for arm, entry in output["arms"].items():
        if entry["outcomes"]["per_decision_point"]["total"] != 12 * len(seeds):
            raise ValueError(f"Expected {12 * len(seeds)} scheduled first attempts per arm")
        for seed in seeds:
            rows = data[arm][seed]["records"]
            first = [r for r in rows if r["retry_index"] == 0]
            if [r["tick"] for r in first] != list(range(0, 120, 10)):
                raise ValueError("Decision tick grid mismatch")
        if entry["candidates"] != entry["action_mix"]["produced_actions"]:
            raise ValueError("Candidate count differs from produced-action count")
        entry["action_mix"]["by_type"] = {a: entry["action_mix"]["by_type"].get(a, 0) for a in ACTION_TYPES}
        entry["action_mix"]["percent_of_produced"] = {
            a: 100 * v / entry["candidates"] if entry["candidates"] else None
            for a, v in entry["action_mix"]["by_type"].items()}
        entry["composition"]["neutral_magnitude"] = 0
        entry["composition"]["seed_fraction_with_benefit"] = entry["composition"]["seeds_with_beneficial_candidate"] / len(seeds)
        entry["per_seed"] = {str(s): {"violation_ticks": data[arm][s]["violation_ticks"],
                                       "candidates": sum(r["decision_outcome"] == "decision_produced" for r in data[arm][s]["records"]),
                                       "outcomes": dict(Counter(r["decision_outcome"] for r in data[arm][s]["records"]))}
                              for s in seeds}
        if arm.startswith("twin@"):
            for shard in data[arm].values():
                for r in shard["records"]:
                    if r["decision_outcome"] != "decision_produced":
                        continue
                    predicted = r["twin_predicted_violation_ticks_action"] - r["twin_predicted_violation_ticks_noop"]
                    if r["twin_verdict"] != (predicted <= 0):
                        raise ValueError("Twin verdict does not reconstruct at tau=0")
            if entry["tau_theta"]["corpus"]["candidates"] != entry["candidates"]:
                raise ValueError("Missing twin predictions in candidate corpus")
        entry["policy_confusion_theta3"] = policy_confusion(data[arm], arm != "ungated")
    pairs = output["episode_comparisons"]
    for index, comparison in enumerate(pairs.values()):
        comparison.pop("significant_unadjusted", None)
        comparison.pop("significant_holm", None)
        power = exact_empirical_bootstrap_power(comparison["diffs"], simulations=2000,
                                                seed=20260922 + index)
        comparison["observed_power_at_n"] = power["power"]
        comparison["power_details"] = power
        comparison["required_n_details"] = normal_approximation_required_n(comparison["diffs"])
        comparison["interpretation"] = "Descriptive pilot only; no episode-level effect or equivalence claim. Post-hoc empirical power is not prospective validation."
        leave_one_out = [(sum(comparison["diffs"]) - d) / 29 for d in comparison["diffs"]]
        comparison["leave_one_seed_out_mean_range"] = [min(leave_one_out), max(leave_one_out)]
    primary = [v for k, v in pairs.items() if k.endswith(" - always_reject")]
    for comp, adjusted in zip(primary, holm([v["wilcoxon"]["p_value"] for v in primary])):
        comp["wilcoxon_holm_primary_three_p"] = adjusted
    log_paths = sorted(Path(args.calls_log_root).glob("work*/*/llm_calls.jsonl")) if args.calls_log_root else []
    if not log_paths:
        raise ValueError("Per-call logs are required for metered usage analysis")
    buckets = {k: {"calls": 0, "tokens_in": 0, "tokens_out": 0} for k in ("provider", "cache")}
    errors = Counter()
    versions = Counter()
    thoughts = 0
    raw_usage_count = 0
    for path in log_paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("event") == "budget_error":
                continue
            if row.get("event") == "provider_error":
                errors[row.get("error_type", "unknown")] += 1
                continue
            bucket = buckets["cache" if row["cache_hit"] else "provider"]
            for key, value in (("calls", 1), ("tokens_in", row["tokens_in"]), ("tokens_out", row["tokens_out"])):
                bucket[key] += value
            if not row["cache_hit"]:
                metadata = row.get("usage_metadata") or {}
                raw_usage_count += bool(metadata)
                thoughts += metadata.get("thoughtsTokenCount", 0)
                if row.get("model_version"):
                    versions[row["model_version"]] += 1
    metered = buckets["provider"]
    cost = metered["tokens_in"] * args.price_in / 1e6 + metered["tokens_out"] * args.price_out / 1e6
    uncertain = sum(s.get("uncertain_tokens_reserved", 0) for s in statuses)
    known_attempts = sum(s["budget_calls_used"] for s in statuses)
    before = {"calls": sum(s.get("budget_before", {}).get("calls", 0) for s in statuses),
              "tokens": sum(s.get("budget_before", {}).get("tokens", 0) for s in statuses)}
    cost_upper = cost + uncertain * max(args.price_in, args.price_out) / 1e6 if live else None
    output["usage"]["total"].update({"billed": metered, "served_from_cache": buckets["cache"],
        "billed_cost_usd": None, "metered_token_cost_usd": cost,
        "cost_upper_bound_usd": cost_upper, "invoice_verified": False,
        "known_thinking_tokens": thoughts if live else None,
        "token_accounting_complete": live,
        "provider_attempts": known_attempts, "provider_error_events": dict(errors),
        "provider_attempts_unit": "charged LLMClient.complete invocations on cache misses",
        "http_attempts": known_attempts if live else None,
        "unknown_response_usage_attempts": known_attempts - metered["calls"],
        "raw_usage_responses": raw_usage_count, "model_versions": dict(versions),
        "uncertain_tokens_reserved": uncertain,
        "before": {**before, "metered_cost_usd": 0.0 if before["calls"] == 0 else None},
        "after": {"calls": known_attempts, "known_tokens": metered["tokens_in"] + metered["tokens_out"],
                  "guard_tokens_including_reservations": sum(s["budget_tokens_used"] for s in statuses),
                  "metered_cost_usd": cost},
        "cost_note": ("Metered token cost at published paid-tier rates, not a verified invoice. Failed-request usage is unknown; conservative reservations provide an upper bound. Inherited response-cache hits incur zero new provider cost."
                      if live else "Partial rate-card calculation from recorded input and visible-output tokens only. Historical thinking-token usage, failed-request usage and actual billing are unavailable; do not label this actual total cost."),
        "price_source": ("https://cloud.google.com/vertex-ai/generative-ai/pricing" if live
                         else "https://ai.google.dev/gemini-api/docs/pricing"),
        "price_checked_date": "2026-09-23" if live else "2026-09-22"})
    if live:
        if metered["calls"] + sum(errors.values()) != known_attempts:
            raise ValueError("Provider-attempt ledger does not reconcile with call journals")
        if metered["tokens_in"] + metered["tokens_out"] + uncertain != sum(s["budget_tokens_used"] for s in statuses):
            raise ValueError("Token ledger does not reconcile with response usage and reservations")
        ceiling = (16000, 44000000) if registered else (9000, 20000000)
        if (prov["budget"]["max_calls"], prov["budget"]["max_tokens"]) != ceiling:
            raise ValueError("Global budget ceiling differs from registered ceiling")
        if any(s["budget_calls_used"] > s["budget_max_calls"] or s["budget_tokens_used"] > s["budget_max_tokens"] for s in statuses):
            raise ValueError("Budget ceiling exceeded")
    prov["budget"]["enforcement"] = (
        "One shared file-locked ledger enforces 16000 calls/44M tokens across all workers; reserve before paid request, reconcile successful usage; retain uncertain liability for failures" if registered
        else "Persistent disjoint worker allocations sum to 9000 calls/20M tokens; reserve before paid request, reconcile successful usage; retain uncertain liability for failures" if live
        else "Historical per-process ceilings did not enforce a global cap")
    prov["budget"]["worker_statuses"] = statuses
    if not live:
        prov["budget"].update({"intended_max_calls": 9000, "intended_max_tokens": 20000000,
                               "global_enforcement_compliant": False,
                               "billable_token_ceiling_verifiable": False})
        prov["runtime"].update({"max_output_tokens": None, "provider_max_attempts": 4,
                                "parser_policy": "Strict JSON; literal control characters in strings rejected",
                                "changes_applied_to_historical_results": False})
        prov["model_identifier_kind"] = "Historical requested model identifier; resolved provider modelVersion was not retained"
        prov["cache_key_policy"] = "SHA256(UTF8(model + NUL + str(temperature) + NUL + json.dumps(messages, sort_keys=True, ensure_ascii=False))). Full messages include the prompt; provider URL, generation limits and resolved modelVersion are absent. Hits return stored replies without a provider call. Five separate cache files were preserved."
    if args.replay_audit:
        output["replay_audit"] = json.loads(Path(args.replay_audit).read_text(encoding="utf-8"))
    output["limitations"] = [
        "One model, one prompt, one response-cache realization; no generalization to all LLMs or sampling variability.",
        "Seed pairing controls simulator fault schedules, not independent model sampling; shared response cache induces reuse across arms.",
        "Candidate metrics use each arm's own trajectory and include retry attempts. Feedback explicitly identifies the reject-all ablation, so differences combine state, feedback and retries.",
        "Twenty-tick local counterfactuals use no-op continuations and overlap in time; summed D is neither episode causal effect nor a recoverable-benefit ceiling.",
        "Tau=3 is fixed-corpus relabelling of proposals collected at tau=0, not a tau=3 deployment.",
        "Zero denominators are not estimable; [1,1] empirical ratio intervals at a sample boundary do not establish population perfection.",
        "Post-hoc power and normal required-n figures are unstable pilot diagnostics, not evidence for a null, efficacy, or a guaranteed future sample size.",
        "The old run lost thinking-token usage and timing/error cache data. Its claimed exact $10.53 charge cannot be repaired retrospectively; it is excluded from the corrected study's incremental cost.",
    ]
    if not live:
        output["limitations"][-1] = "The historical run lost thinking-token usage and timing/error cache data. The $10.53 visible-token estimate is not a verified actual charge, and the intended single global budget was not enforced. These cannot be repaired retrospectively."
        attempt_root = ROOT / "results/llm_descriptive_study/billing_attempt"
        if not attempt_root.exists():
            attempt_root = ROOT / "results/llm_descriptive_study/live"
        ledgers = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((attempt_root / "shards").glob("_budget_w*.json"))]
        logs = list(attempt_root.glob("work*/*/llm_calls.jsonl")) + list(attempt_root.glob("diagnostic_calls.jsonl"))
        events = [json.loads(line) for p in logs for line in p.read_text(encoding="utf-8").splitlines()]
        failure_path = attempt_root / "diagnostic_error.json"
        failure = json.loads(failure_path.read_text(encoding="utf-8")) if failure_path.exists() else {}
        output["aborted_correction_attempt"] = {
            "included_in_primary_results": False,
            "provider_failure": failure,
            "before": {"calls": 0, "known_tokens": 0, "metered_cost_usd": 0.0},
            "after": {"calls": sum(r["calls"] for r in ledgers),
                      "successful_new_responses": sum(r.get("event") == "response" and not r.get("cache_hit") for r in events),
                      "known_tokens": sum(r.get("tokens_in", 0) + r.get("tokens_out", 0) for r in events if r.get("event") == "response" and not r.get("cache_hit")),
                      "metered_cost_usd": 0.0,
                      "actual_invoice_cost_usd": None,
                      "reserved_uncertain_tokens": sum(r.get("uncertain_tokens", 0) for r in ledgers),
                      "unresolved_calls": sum(r.get("pending_call", False) for r in ledgers),
                      "pending_token_reservations": sum(r.get("pending_tokens", 0) for r in ledgers)},
            "reason_stopped": "Completed new calls returned HTTP errors; one interrupted call remains unresolved. Diagnostic confirmed HTTP402 prepayment credits depleted. User chose to finish with existing live data; no further provider calls made.",
            "model_list_diagnostic_requests": 1,
            "note": "No successful new generation or usage returned. Failed request usage is unavailable. Four workers were stopped; interrupted in-flight calls remain marked unresolved rather than guessed free.",
        }


def policy_confusion(shards, gated):
    counts = dict.fromkeys(("tp", "fn", "fp", "tn"), 0)
    for shard in shards.values():
        for r in shard["records"]:
            if r["decision_outcome"] != "decision_produced":
                continue
            rejected = r["twin_verdict"] is False if gated else not r["was_applied"]
            harmful = r["counterfactual_harm_delta"] > 3
            counts["tp" if rejected and harmful else "fp" if rejected else "fn" if harmful else "tn"] += 1
    counts["recall"] = counts["tp"] / (counts["tp"] + counts["fn"]) if counts["tp"] + counts["fn"] else None
    counts["fpr"] = counts["fp"] / (counts["fp"] + counts["tn"]) if counts["fp"] + counts["tn"] else None
    counts["precision"] = counts["tp"] / (counts["tp"] + counts["fp"]) if counts["tp"] + counts["fp"] else None
    return counts


def number(value, digits=3):
    return "n/e" if value is None else f"{value:.{digits}f}"


def interval(value, digits=3):
    return "n/e" if value is None else f"[{number(value[0], digits)}, {number(value[1], digits)}]"


def table(headers, rows):
    return "\n".join(["| " + " | ".join(map(str, headers)) + " |",
                       "|" + "|".join("---" for _ in headers) + "|"] +
                      ["| " + " | ".join(map(str, row)) + " |" for row in rows])


def render_report(output):
    arms = output["arms"]
    usage = output["usage"]["total"]
    prov = output["provenance"]
    live = prov["study_kind"] == "corrected_live_run"
    if live:
        raise ValueError("This Markdown renderer describes the historical audited study; review a new study before rendering its narrative")
    parts = ["# Live-model descriptive study: proposals, outcomes and gate classification",
        "Data collected 2026-09-20; audited 2026-09-22. Thirty seeds (0–29), four arms, 120 episodes. "
        "This is a descriptive study of a single hosted model. **No episode-level effect, equivalence or null is claimed at 30 seeds.** "
        "The existing live data are retained without relabelling or replacing failures. A corrected rerun was stopped because the configured Gemini key returned HTTP 402 (prepayment credits depleted); "
        "the user chose to finish with these existing data. **Historical global-budget enforcement and complete cost accounting remain unresolved limitations.** `paper/main.tex` is unchanged.",
        "The model did not collapse to inactivity: 51.94–63.06% of first attempts produced a valid non-no-op action. "
        "Formatting failures are substantial and explicitly classified. On the recorded twin@1.0 corpus, 24 of 25 false positives at tau=0/theta=3 "
        "come from the threshold-definition mismatch; diagonal recall is 0.989 and FPR is 0.048. "
        "These describe the observed proposer and classifier, with the limitations below.",
        "## Protocol and provenance",
        table(["Item", "Recorded setting"], [
            ["Model / provider", f"{prov['model']} / Google Generative Language API"],
            ["Endpoint", prov["base_url"]], ["Temperature / prompt", "0.0 / v1; prompt SHA256 " + prov["prompt_sha256"]],
            ["Resolved model versions", json.dumps(usage["model_versions"], sort_keys=True) if usage["model_versions"] else "Not retained in the historical logs; model identifier above is the requested identifier"],
            ["Episode protocol", "120 ticks; decisions at 0,10,…,110; retry cap 2; horizon 20; tau=0; theta=3; gateway immune"],
            ["Faults", "Default FaultConfig: 3 faults; starts 20–260 (some fall beyond the episode)"],
            ["LLM loop", "4 response steps; 20 s decision deadline; 25% reserved for finalization; 30 s provider ceiling"],
            ["Provider request settings", "Historical maximum 4 HTTP attempts per client call; default provider output limit"],
            ["Parsing used for these data", "Strict JSON; schema and semantic validation; original parser preserved in the artifact"],
            ["A0", "Existing sweep seeds 0–29 reused; zero reject-all mismatches; no A0 episodes run"],
        ]),
        "Fidelity 1.0 is an established useful operating point for the rule controller (0.8 is also useful in the sweep). "
        "Fidelity 0.6 lies near the scripted controller's crossover, 0.549 [0.434, 0.677]. "
        "Queue simplification is off at both selected levels. These are preselected operating points, not an LLM crossover estimate.",
        "## Calls, tokens, cost and cache",
        prov["cache_key_policy"],
        "All five historical response caches are preserved. A cache hit incurred no new provider call. "
        "The original logs retain input and visible-output token counts but omit thinking-token usage and resolved modelVersion. "
        "They also count client calls rather than hidden HTTP retry attempts. Thus exact total tokens, HTTP request count and invoiced cost cannot be reconstructed.",
        table(["Measurement", "Calls", "Input tokens", "Output tokens", "USD"], [
            ["Before historical run (counters initialized)", usage["before"]["calls"], 0, 0, "0 recorded"],
            ["Successful historical provider responses", usage["billed"]["calls"], usage["billed"]["tokens_in"], usage["billed"]["tokens_out"], number(usage["metered_token_cost_usd"], 6) + " partial estimate"],
            ["Response-cache hits", usage["served_from_cache"]["calls"], usage["served_from_cache"]["tokens_in"], usage["served_from_cache"]["tokens_out"], "0 incremental"],
            ["After historical run: charged client calls", usage["provider_attempts"], "see successful responses", "thinking and failed-request usage unknown", "actual total unavailable"],
        ]),
        f"Charged client calls without a successful usage record: {usage['unknown_response_usage_attempts']}. "
        f"The historical guard recorded {usage['after']['guard_tokens_including_reservations']:,} tokens; this excludes thinking. "
        f"The input-plus-visible-output rate-card calculation is ${usage['metered_token_cost_usd']:.6f}. "
        "**Actual total cost and total billable tokens are not available.** The prior draft's claim that $10.53 was the actual billed total is withdrawn. "
        "The rates checked on 2026-09-22 are $0.75/M input and $3.75/M output including thinking. "
        "[Google pricing](https://ai.google.dev/gemini-api/docs/pricing).",
        "The intended ceiling was 9,000 calls / 20M tokens, but the original two-seed pilot and four workers had separate guards totalling "
        "17,000 calls / 38M tokens. The 5,655 recorded client calls and 12,008,001 recorded tokens are below the intended limits, "
        "but this does not establish compliance: one global guard was not enforced and thinking-token usage was omitted. "
        "No episodes stopped for budget exhaustion. The pilot's eight episodes are included once in the 120-episode cohort; no additional A0 was run. "
        "Future execution now uses persistent disjoint allocations whose sum is the global ceiling, complete returned token usage and conservative failed-request reservations. "
        "Those repairs were tested offline and do not retroactively repair historical compliance.",
        "## 1. Decision outcome distribution (primary result)",
        "An attempt means one call to the agent's decision routine, which may itself use up to four model responses. "
        "A gate rejection may trigger up to two more attempts at the same scheduled decision. "
        "The first-attempt table has exactly 360 observations per arm; the all-attempt table includes gate retries. "
        "The terminal-attempt table describes how each scheduled decision ends, including a produced action that the gate may still reject. "
        "These are distinct denominators; `decision_produced` means a valid non-no-op proposal, not necessarily an applied action.",
    ]
    for key, title in (("per_decision_point", "First attempt at each scheduled decision"),
                       ("per_attempt", "All attempts, including gate retries"),
                       ("per_terminal_decision", "Terminal attempt at each scheduled decision")):
        parts += ["### " + title, table(["Outcome"] + [f"{ARM_NAMES[a]} (N={arms[a]['outcomes'][key]['total']})" for a in arms],
            [[outcome] + [f"{arms[a]['outcomes'][key]['counts'][outcome]} ({arms[a]['outcomes'][key]['percent'][outcome]:.2f}%)" for a in arms]
             for outcome in OUTCOMES])]
    first_rates = [v["outcomes"]["per_decision_point"]["percent"]["decision_produced"] for v in arms.values()]
    failures = {a: v["action_mix"]["fallback_no_op"] for a, v in arms.items()}
    parts += [f"Valid non-no-op first proposals occur at {min(first_rates):.2f}–{max(first_rates):.2f}% of scheduled decisions. "
              "The complete taxonomy above distinguishes intentional inactivity from failures. "
              "All failure categories use an explicit no-op fallback; none is silently counted as deliberate inactivity. "
              f"Fallback counts over all attempts: {json.dumps(failures)}.",
              "## 2. Action-type mix",
              "Counts and percentages below condition on valid non-no-op proposals and include gate retries. All five available non-no-op action types are shown, including zeros.",
              table(["Arm", "N"] + list(ACTION_TYPES),
                    [[ARM_NAMES[a], e["candidates"]] + [f"{e['action_mix']['by_type'][t]} ({number(e['action_mix']['percent_of_produced'][t], 2)}%)" for t in ACTION_TYPES]
                     for a, e in arms.items()]),
              "The sweep references below use 150 seeds (0–149), so they are descriptive reference mixes, not paired controller comparisons. "
              "They exclude no-ops using the same candidate definition. `llm` in the sweep JSON denotes the deterministic scripted stub and made no model calls.",
              table(["Sweep controller", "Arm", "N"] + list(ACTION_TYPES),
                    [["Rule" if controller == "rule" else "Scripted", ARM_NAMES[a].replace("LLM ", ""), e["candidates"]] +
                     [f"{e['counts'].get(t, 0)} ({e['percent'].get(t, 0):.2f}%)" for t in ACTION_TYPES]
                     for controller, aa in output["sweep_reference_action_mix"].items() for a, e in aa.items()]),
              "Mix differences across arms combine trajectory changes, rejection feedback, retry weighting and response reuse. "
              "Ungated, the LLM proposes restarts or migrations in 60.35% of valid actions, compared with 18.13% for the rule controller and 0% for the scripted stub in the sweep. "
              "The reject-all feedback explicitly names that ablation. They do not isolate an effect of predictor fidelity on an otherwise fixed proposal population.",
              "## 3. Candidate composition and magnitude",
              "D = V20(action, then no-op) − V20(no-op), in service-SLO violation ticks. "
              "Violations are summed across services, so an episode total can exceed its 120 elapsed ticks. "
              "Beneficial means D<0, neutral D=0, harmful D>0. The classifier's positive ground-truth label is narrower: D>3. "
              "No-ops are excluded. Quantiles use the sweep's nearest-order-statistic convention. Available benefit is Σmax(−D,0); available harm is Σmax(D,0).",
              table(["Arm", "Candidates", "Beneficial", "Neutral", "Harmful D>0 (D>3)", "Available benefit", "Available harm", "Beneficial magnitude min/median/p90/max", "Harmful magnitude min/median/p90/max"],
                    [[ARM_NAMES[a], e["candidates"], e["composition"]["beneficial"], e["composition"]["neutral"],
                      f"{e['composition']['harmful']} ({e['composition']['harmful_above_3']})", e["composition"]["available_benefit"], e["composition"]["available_harm"],
                      quantiles(e["composition"]["benefit_quantiles"]), quantiles(e["composition"]["harm_quantiles"])] for a, e in arms.items()]),
              "Neutral candidates have zero magnitude. Action-specific composition and beneficial-seed identities are retained in the JSON. "
              "Candidate sums include retries and overlapping 20-tick windows, so they cannot be added up as episode causal effects.",
              "## 4. Preserved benefit, prevented harm and net ΣD",
              "benefit_preserved = approved benefit / available benefit; harm_prevented = rejected harm / available harm. "
              "Net ΣD is the mean per seed of the sum of D over approved candidates. All intervals below are 95% seed-cluster percentile bootstrap intervals (5,000 resamples, shared seed indices across arms). "
              "A zero denominator is **not estimable (n/e)** and stored as null; zero-denominator bootstrap resamples are excluded and their estimable fraction is recorded. "
              "A boundary interval such as [1,1] describes this empirical sample, not perfect population performance.",
              table(["Arm", "benefit_preserved [CI] (approved/available)", "harm_prevented [CI] (rejected/available)", "Net ΣD / seed [CI]", "Net total"],
                    [[ARM_NAMES[a], ratio(e["benefit_preserved"]), ratio(e["harm_prevented"]),
                      f"{number(e['net_sum_d_approved']['mean_per_seed'])} {interval(e['net_sum_d_approved']['bootstrap_ci95'])}",
                      e["net_sum_d_approved"]["total"]] for a, e in arms.items()]),
              "Each arm is evaluated on its own candidate set. These ratios describe its observed filtering, and are not a paired experiment on common proposals.",
              "## 5. Classification at tau=0/theta=3 and tau=theta=3",
              "Positive means reject; ground truth is D>theta. Definitional false positives are rejected candidates with tau<D≤theta. "
              "Predictive false positives are the remaining false positives. The definitional band is empty on the diagonal. "
              "The tau=3 rows relabel the fixed corpus collected at tau=0; they do not simulate trajectories under a tau=3 gate.",
              table(["Arm", "tau / theta", "TP / FN / FP / TN", "Recall [CI]", "FPR [CI]", "Precision [CI]", "FP definitional / predictive", "FP on D<0"],
                    [[ARM_NAMES[a], f"{c['tau']} / {c['theta']}", " / ".join(str(c["counts"][k]) for k in ("tp", "fn", "fp", "tn")),
                      f"{number(c['recall']['point'])} {interval(c['recall']['ci95'])}",
                      f"{number(c['fpr']['point'])} {interval(c['fpr']['ci95'])}",
                      f"{number(c['precision']['point'])} {interval(c['precision']['ci95'])}",
                      f"{c['false_positives']['definitional']} / {c['false_positives']['predictive']}", c["false_positives"]["predictive_and_beneficial"]]
                     for a, e in arms.items() if "tau_theta" in e for c in e["tau_theta"]["cells"] if (c["tau"], c["theta"]) in ((0, 3), (3, 3))]),
              "Ungated and reject-all have no predictive twin scores, so tau-dependent predictor metrics are not applicable. "
              "For completeness their fixed decision policies give the following theta=3 matrices; they are unchanged by tau and have no predictive-error interpretation.",
              table(["Control", "TP / FN / FP / TN", "Recall", "FPR", "Precision"],
                    [[ARM_NAMES[a], " / ".join(str(arms[a]["policy_confusion_theta3"][k]) for k in ("tp", "fn", "fp", "tn"))] +
                     [number(arms[a]["policy_confusion_theta3"][k]) for k in ("recall", "fpr", "precision")]
                     for a in ("ungated", "always_reject")]),
              "## 6. Paired episode differences and power limitations",
              "Difference = arm minus reject-all, paired by simulator seed. Negative values mean fewer observed violation ticks. "
              "Exact two-sided Wilcoxon signed-rank p-values enumerate the conditional sign distribution by dynamic programming, omit zero differences and use average ranks for ties. "
              "The signed-rank interpretation assumes symmetry/exchangeable signs under the null. Bootstrap intervals target the mean, whereas signed ranks test a different statistic; disagreement is not resolved by choosing the favorable result. "
              "Holm adjustment for the three requested comparisons is shown below; the four-comparison family including the exploratory fidelity contrast is retained in the JSON.",
              table(["Comparison", "Mean", "95% t-CI", "95% bootstrap CI", "Nonzero (lower/higher)", "Exact Wilcoxon p", "Holm p (3 primary)", "Observed power (MCSE)", "Normal heuristic n for 80%"],
                    [[name.replace("always_reject", "reject-all"), number(c["mean"]), interval(c["t_ci95"]), interval(c["bootstrap_ci95"]),
                      f"{c['n_nonzero']} ({c['better']}/{c['worse']})", f"{c['wilcoxon']['p_value']:.6g}",
                      number(c.get("wilcoxon_holm_primary_three_p"), 6),
                      f"{c['observed_power_at_n']:.3f} ({c['power_details']['mcse']:.3f})", c["seeds_for_80pct_power_normal"]]
                     for name, c in output["episode_comparisons"].items()]),
              "Observed power is a post-hoc empirical resampling diagnostic: 2,000 samples of 30 paired differences are tested using the exact signed-rank calculation at unadjusted alpha=0.05. "
              "MCSE describes Monte Carlo error only. Required n uses ceil(((z0.975+z0.8)×SD/abs(mean))²), a paired-mean normal approximation; it is not the required size for the exact Wilcoxon test. "
              "Both quantities condition on this small observed sample and are unstable when differences are sparse. "
              "The gate-versus-reject-all comparisons at 30 seeds are underpowered; failure to reject is not evidence of no effect. "
              "Even a small p-value elsewhere in this table is retained only as a descriptive pilot statistic. **No episode-level effect is claimed.**",
              "The twin@1.0 paired mean is sensitive to sparse opportunities: seed 6 contributes −63 of the total −129 ticks. "
              "Leaving that seed out changes the mean from −4.300 to −2.276; the primary estimate retains it. "
              "This sensitivity reinforces the limits of using 30 seeds for an episode-level conclusion.",
              "## 7. Seeds containing a beneficial candidate",
              table(["Arm", "Seeds with D<0", "Percentage", "Seed identities"],
                    [[ARM_NAMES[a], f"{e['composition']['seeds_with_beneficial_candidate']} / {output['provenance']['n_seeds']}",
                      f"{100 * e['composition']['seed_fraction_with_benefit']:.2f}%",
                      ", ".join(map(str, e["composition"]["seeds_with_beneficial_candidate_list"])) or "none"] for a, e in arms.items()]),
              "These are opportunities on each arm's recorded proposal trajectory. In particular, reject-all's available local benefit is not an upper bound on a different policy's episode benefit.",
              "## Engineering audit, corrections and remaining limitations",
              "The original 120 episodes were reconstructed offline with zero simulator, counterfactual-label or gate-verdict mismatches. "
              "Its response-only cache could not faithfully replay 13 historical timeouts. "
              "The repaired runner records timing/error information for future runs; the old run remains unchanged. "
              "The original cache contained 273 unique responses that failed strict JSON parsing; 192 parsed when literal control characters were allowed. "
              "That is a response-level diagnostic, not 192 failed decisions recovered. Changing parsing can change subsequent actions and states, so historical outcomes were not relabelled as if those actions had happened.",
              "Engineering fixes validated offline include preserved thinking-token metadata, accounting for failed-request uncertainty, persistent study-wide budget allocation, "
              "atomic cache writes, explicit first/terminal/all-attempt denominators, all eight outcome categories including zeros, "
              "strict cohort/label checks, exact-test empirical power, and an HTTP billing/configuration circuit breaker. "
              "The report removes unsupported episode claims and incorrect sweep comparisons. Parser and accounting repairs were not used to manufacture replacement outcomes for the historical data.",
              "\n".join("- " + item for item in output["limitations"]),
              "The model/prompt/temperature are fixed, but the results characterize the original cached controller configuration, not independent fresh sampling at every arm or decision. "
              "Parser failures are a property of the model-plus-parser system and should not be described as the model lacking useful actions. "
              "Literal-newline tolerance can recover some formatting failures, but evaluating the changed controller requires a new live run. "
              "The aborted 2026-09-22 attempt is excluded from every primary table because it encountered a systemic provider billing failure.",
              "## Reproduction and artifacts",
              "The [JSON alongside this report](llm_descriptive_study.json) contains exact counts, per-seed totals, all metric denominators, estimable bootstrap fractions, the full threshold grid, and audit metadata. "
              "The [primary archive](llm_descriptive_study_artifacts.zip) and [artifact manifest](llm_descriptive_study_artifact_manifest.json) provide portable evidence and SHA256 hashes. "
              "The artifact archive preserves shards, response caches, chronological call journals, prompt/source snapshots and hashes. Offline replay makes no provider calls and reuses A0 from the sweep.",
              "```powershell\nExpand-Archive -LiteralPath docs/research/llm_descriptive_study_artifacts.zip -DestinationPath results/llm_descriptive_study -Force\nExpand-Archive -LiteralPath docs/research/llm_descriptive_study_billing_attempt.zip -DestinationPath results/llm_descriptive_study -Force\n.\\.venv\\Scripts\\python.exe scripts\\llm_descriptive_study.py analyze --shards results\\llm_descriptive_study\\source\\shards --calls-log-root results\\llm_descriptive_study\\source --price-in 0.75 --price-out 3.75 --replay-audit results\\llm_descriptive_study\\historical_replay_audit.json --output docs\\research\\llm_descriptive_study.json --markdown docs\\research\\llm_descriptive_study.md\n.\\.venv\\Scripts\\python.exe scripts\\llm_study_replay.py --historical\n```",
              "A future paid run uses a new output directory and `run --workers 4 --worker <0..3> --max-calls 9000 --max-tokens 20000000`; "
              "the ceilings are global totals divided across workers, not ceilings to multiply by four. "
              "Existing completed shards are immutable and resumes retain the same budget allocation.",
    ]
    if "aborted_correction_attempt" in output:
        attempt = output["aborted_correction_attempt"]
        after = attempt["after"]
        parts.insert(-3, "### Aborted correction attempt: separate usage disclosure\n\n"
                     f"Before: 0 new calls, 0 returned tokens, $0 recorded. After: {after['calls']} guarded client calls, "
                     f"{after['successful_new_responses']} successful new responses, {after['known_tokens']} returned provider tokens, $0 in metered response charges. "
                     f"{after['reserved_uncertain_tokens']:,} tokens were conservatively reserved for failures. "
                     f"Unresolved client calls when workers stopped: {after['unresolved_calls']} "
                     f"({after['pending_token_reservations']:,} pending reserved tokens). "
                     "A separate read-only model-list request verified model availability. "
                     "The guarded diagnostic generation returned HTTP 402 RESOURCE_EXHAUSTED, explicitly stating prepayment credits were depleted. "
                     "No actual invoice total was available. These attempts produced no new usable live-model responses and do not enter the primary study. "
                     "After this diagnosis the user selected the existing-data analysis; no further generation calls were made.")
    if "replay_audit" in output:
        parts.append("Replay audit is embedded in the JSON and distributed with the artifact archive.")
    return "\n\n".join(parts) + "\n"


def quantiles(values):
    return "n/e" if values is None else " / ".join(str(values[k]) for k in ("min", "median", "p90", "max"))


def ratio(entry):
    return f"{number(entry['point'])} {interval(entry['ci95'])} ({entry['numerator']:g}/{entry['denominator']:g})"
