from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics as st
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from langgraph.checkpoint.memory import MemorySaver

from gateway_uncertainty_ablation import (
    REAL_TWIN, candidate_corpus, composition_detail, corpus_summary, episode_comparison,
    gate_class, holm, mean_with_ci, per_seed_candidate_arrays, ratio_with_ci, tau_theta_grid,
)
from twinloop.config import (
    EvaluationConfig, ExperimentConfig, FaultConfig, GraphConfig, LLMConfig, SimConfig, TwinConfig,
)
from twinloop.experiment import runner as runner_mod
from twinloop.experiment.arms import RunSpec
from twinloop.experiment.runner import run_single
from twinloop.llm.budget import BudgetExceeded, SharedBudgetGuard
from twinloop.llm.cache import ResponseCache
from twinloop.llm.providers import (
    GEMINI_MAX_ATTEMPTS, GEMINI_RETRYABLE_STATUS, MIN_ATTEMPT_SECONDS, SERVER_DEADLINE_MARGIN_SECONDS,
    GeminiProvider,
)

VERTEX_ENDPOINT = "https://aiplatform.googleapis.com"
PROMPT_PATH = ROOT / "src/twinloop/agent/prompts/llm_agent_v1.txt"
PARSER_PATH = ROOT / "src/twinloop/llm/structured.py"
PARSER_VERSION = "v2-strict-then-literal-control-character-tolerant"
VERIFIED_PARSER_SHA256 = "9fdb0bad43db2336b5f76c688dfc1fb30dec5717794324408137c57e92b49913"
MAX_OUTPUT_TOKENS = 8192
PROVIDER_MAX_ATTEMPTS = GEMINI_MAX_ATTEMPTS
THINKING_CONFIG = None
PILOT_MAX_SEEDS = 2
REGISTERED_SEEDS = 72
REGISTERED_MAX_CALLS = 16000
REGISTERED_MAX_TOKENS = 44000000
HISTORICAL_SEEDS = 30
ARMS = ("ungated", "always_reject", "twin@1.0", "twin@0.6")
RECORD_FIELDS = (
    "seed", "tick", "decision_index", "proposal_index", "retry_index", "proposed_action",
    "schema_valid", "semantically_valid", "twin_verdict", "twin_reason", "was_applied",
    "twin_predicted_violation_ticks_action", "twin_predicted_violation_ticks_noop",
    "counterfactual_violation_ticks_action", "counterfactual_violation_ticks_noop",
    "counterfactual_harm_delta", "ground_truth_harmful", "decision_outcome", "decision_fallback",
    "decision_trace", "exhausted", "llm_tokens_in", "llm_tokens_out", "llm_latency_ms",
    "llm_cache_hit", "prompt_version",
)
OUTCOMES = ("decision_produced", "deliberate_no_op", "tool_budget_exhausted", "final_parse_failure",
            "validation_exhausted", "timeout", "provider_budget_exhausted", "provider_error")


def load_env_file(path):
    loaded = []
    target = Path(path)
    if not target.exists():
        return loaded
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and not os.environ.get(key):
            os.environ[key] = value
            loaded.append(key)
    return loaded


def arm_spec(arm):
    if arm == "ungated":
        return False, None, None
    if arm == "always_reject":
        return True, 1.0, gate_class("always_reject")
    return True, float(arm.split("@")[1]), None


def study_config(cache_dir, log_path, model, base_url, api_key_env, max_calls, max_tokens,
                 timeout_seconds, react_timeout_seconds):
    return ExperimentConfig(
        sim=SimConfig(episode_ticks=120),
        fault=FaultConfig(gateway_faultable=False),
        graph=GraphConfig(decision_interval_ticks=10, retry_cap=2),
        twin=TwinConfig(horizon_ticks=20, tolerance_margin=0.0),
        evaluation=EvaluationConfig(harm_threshold_ticks=3),
        llm=LLMConfig(provider="gemini", model=model, base_url=base_url, api_key_env=api_key_env,
                      temperature=0.0, cache_dir=str(cache_dir), cache_bypass=False,
                      cache_only=False, max_calls=max_calls, max_tokens=max_tokens,
                      timeout_seconds=timeout_seconds, react_timeout_seconds=react_timeout_seconds,
                      react_max_steps=4, prompt_version="v1", log_path=str(log_path)),
    )


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_identity(model, project, location):
    parser_sha256 = file_sha256(PARSER_PATH)
    return {
        "provider": "gemini", "provider_platform": "vertex_ai", "endpoint": VERTEX_ENDPOINT,
        "auth": "application_default_credentials", "vertex_project": project,
        "vertex_location": location, "model": model, "temperature": 0.0,
        "prompt_version": "v1", "prompt_sha256": file_sha256(PROMPT_PATH),
        "parser_version": PARSER_VERSION, "parser_sha256": parser_sha256,
        "parser_matches_verified": parser_sha256 == VERIFIED_PARSER_SHA256,
        "thinking_config": {"sent": THINKING_CONFIG,
                            "policy": "model default" if THINKING_CONFIG is None else "explicit",
                            "usage_field": "usageMetadata.thoughtsTokenCount"},
        "max_output_tokens": MAX_OUTPUT_TOKENS, "provider_max_attempts": PROVIDER_MAX_ATTEMPTS,
        "provider_retry_policy": {"retry_statuses": sorted(GEMINI_RETRYABLE_STATUS),
                                  "timeouts_retried": False, "shared_deadline": True,
                                  "min_attempt_seconds": MIN_ATTEMPT_SECONDS,
                                  "backoff_seconds": [2**i for i in range(PROVIDER_MAX_ATTEMPTS - 1)],
                                  "server_deadline_header": f"ceil(client budget) + {SERVER_DEADLINE_MARGIN_SECONDS} s"},
    }


def command_run(args):
    shards = Path(args.shards)
    shards.mkdir(parents=True, exist_ok=True)
    lock = shards / f"_worker_{args.worker}.lock"
    with lock.open("x", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    try:
        _command_run(args)
    finally:
        lock.unlink()


def _command_run(args):
    if args.workers < 1 or not 0 <= args.worker < args.workers:
        raise SystemExit("Invalid worker partition")
    if args.pilot:
        if args.seed_start != 0 or not 1 <= args.seed_count <= PILOT_MAX_SEEDS or args.arms:
            raise SystemExit(f"The pilot is limited to seeds 0..{PILOT_MAX_SEEDS - 1} and all four arms")
    elif args.seed_start != 0 or args.seed_count != REGISTERED_SEEDS or args.arms:
        raise SystemExit(f"The registered study requires seeds 0..{REGISTERED_SEEDS - 1} and all four arms")
    elif (args.max_calls, args.max_tokens) != (REGISTERED_MAX_CALLS, REGISTERED_MAX_TOKENS):
        raise SystemExit(f"The registered study requires the global ceiling of {REGISTERED_MAX_CALLS} calls "
                         f"and {REGISTERED_MAX_TOKENS} tokens")
    loaded = load_env_file(args.env_file) if args.env_file else []
    shards = Path(args.shards)
    shards.mkdir(parents=True, exist_ok=True)
    work = Path(args.workdir)
    work.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    project = args.project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = args.location or os.environ.get("GOOGLE_CLOUD_LOCATION") or "global"
    if not project and not args.cache_only:
        raise SystemExit("GOOGLE_CLOUD_PROJECT is not set")
    seeds = [s for i, s in enumerate(range(args.seed_start, args.seed_start + args.seed_count))
             if i % args.workers == args.worker]
    arms = [a for a in (args.arms.split(",") if args.arms else list(ARMS))]
    source_paths = sorted((ROOT / "src/twinloop").rglob("*.py"))
    source_paths += sorted((ROOT / "src/twinloop/agent/prompts").glob("*.txt"))
    source_paths += [Path(__file__), ROOT / "scripts/gateway_uncertainty_ablation.py"]
    identity = {
        "config": study_config("", "", args.model, VERTEX_ENDPOINT, "",
                               args.max_calls, args.max_tokens, args.timeout_seconds,
                               args.react_timeout_seconds).model_dump(mode="json"),
        "run": run_identity(args.model, project, location),
        "pilot": args.pilot,
        "seeds": list(range(args.seed_start, args.seed_start + args.seed_count)),
        "source_sha256": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in source_paths},
    }
    experiment_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    allocation_path = shards / "_allocation.json"
    allocation = {"workers": args.workers, "global_max_calls": args.max_calls,
                  "global_max_tokens": args.max_tokens, "policy": SharedBudgetGuard.policy,
                  "experiment_hash": experiment_hash, "experiment_identity": identity}
    try:
        with allocation_path.open("x", encoding="utf-8") as handle:
            json.dump(allocation, handle)
    except FileExistsError:
        for attempt in range(10):
            try:
                existing = json.loads(allocation_path.read_text(encoding="utf-8"))
                break
            except json.JSONDecodeError:
                time.sleep(0.1)
        else:
            raise SystemExit("Incomplete budget allocation manifest")
        if existing != allocation:
            raise SystemExit("Study budget allocation cannot change on resume")
    config = study_config(cache_dir, work / "llm_calls.jsonl", args.model, VERTEX_ENDPOINT,
                          "", args.max_calls, args.max_tokens,
                          args.timeout_seconds, args.react_timeout_seconds)
    ledger = shards / "_budget.json"
    if any((shards / f"{arm.replace('@', '_')}__{seed:04d}.json").exists()
           for seed in seeds for arm in arms) and not ledger.exists() and not args.cache_only:
        raise SystemExit("Existing shards have no persistent budget ledger; use offline replay")
    budget = SharedBudgetGuard(args.max_calls, args.max_tokens, ledger, owner=f"w{args.worker}")
    cache = ResponseCache(cache_dir / "cache.json", cache_only=args.cache_only)
    provider_factory = lambda: GeminiProvider(project, location, max_attempts=PROVIDER_MAX_ATTEMPTS,
                                               max_output_tokens=MAX_OUTPUT_TOKENS,
                                               thinking_config=THINKING_CONFIG)
    before = budget.owner_usage
    config.llm.cache_only = args.cache_only
    config_path = shards / f"_config_w{args.worker}.json"
    frozen = config.model_dump(mode="json")
    frozen["llm"].pop("cache_only")
    if config_path.exists() and json.loads(config_path.read_text(encoding="utf-8")) != frozen:
        raise SystemExit("Study configuration differs from the recorded configuration")
    config_path.write_text(json.dumps(frozen, indent=1), encoding="utf-8")
    print(f"env vars loaded from file: {loaded}", flush=True)
    print(f"model={args.model} vertex_project={project} vertex_location={location} "
          f"temperature=0.0 prompt_version=v1 "
          f"cache={cache_dir / 'cache.json'} budget_policy={SharedBudgetGuard.policy} "
          f"global_calls={args.max_calls} global_tokens={args.max_tokens} "
          f"ledger_calls={budget.calls} ledger_tokens={budget.tokens}", flush=True)
    started = time.perf_counter()
    stopped = None
    for seed in seeds:
        for arm in arms:
            target = shards / f"{arm.replace('@', '_')}__{seed:04d}.json"
            if target.exists():
                continue
            gated, fidelity, patch = arm_spec(arm)
            runner_mod.TwinValidator = patch if patch is not None else REAL_TWIN
            episode_dir = work / f"{arm.replace('@', '_')}_{seed:04d}_{time.time_ns()}"
            usage_before = budget.owner_usage
            episode_start = time.perf_counter()
            try:
                summary, _, recs = run_single(
                    config, RunSpec("LLM", "llm", gated, seed, fidelity), episode_dir,
                    provider_factory, budget, cache, True, MemorySaver())
            except BudgetExceeded as error:
                stopped = {"reason": str(error), "seed": seed, "arm": arm,
                           "calls": budget.calls, "tokens": budget.tokens}
                print(f"BUDGET STOP at seed {seed} arm {arm}: {error}", flush=True)
                break
            finally:
                runner_mod.TwinValidator = REAL_TWIN
            payload = {
                "arm": arm, "seed": seed, "fidelity": fidelity, "gated": gated,
                "llm_journal_path": str(episode_dir / "llm_calls.jsonl"),
                "violation_ticks": summary["slo_violation_ticks"],
                "wall_seconds": time.perf_counter() - episode_start,
                "llm_calls": summary["llm_calls"], "llm_tokens_in": summary["llm_tokens_in"],
                "llm_tokens_out": summary["llm_tokens_out"],
                "budget_calls_before": usage_before["calls"],
                "budget_calls_after": budget.owner_usage["calls"],
                "budget_tokens_before": usage_before["tokens"],
                "budget_tokens_after": budget.owner_usage["tokens"],
                "global_calls_after": budget.calls, "global_tokens_after": budget.tokens,
                "proposals": summary["proposals"], "rejections": summary["rejections"],
                "retries": summary["retries"],
                "records": [{k: r.get(k) for k in RECORD_FIELDS} for r in recs],
            }
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            temporary.replace(target)
            print(f"seed {seed} {arm}: ticks={payload['violation_ticks']} "
                  f"calls={payload['llm_calls']} tokens_in={payload['llm_tokens_in']} "
                  f"tokens_out={payload['llm_tokens_out']} "
                  f"wall={payload['wall_seconds']:.1f}s global_budget={budget.calls}/{args.max_calls}",
                  flush=True)
            if getattr(budget, "blocked_reason", None):
                stopped = {"reason": budget.blocked_reason, "seed": seed, "arm": arm,
                           "calls": budget.calls, "tokens": budget.tokens}
                break
            if any(r.get("decision_outcome") == "provider_budget_exhausted" for r in recs):
                stopped = {"reason": "provider budget exhausted during recorded episode", "seed": seed,
                           "arm": arm, "calls": budget.calls, "tokens": budget.tokens}
                break
        if stopped:
            break
    elapsed = time.perf_counter() - started
    budget.refresh()
    usage = budget.owner_usage
    status = {"model": args.model, "base_url": VERTEX_ENDPOINT, "temperature": 0.0,
              "prompt_version": "v1", "vertex_project": project, "vertex_location": location,
              "run_identity": identity["run"], "experiment_hash": experiment_hash,
              "budget_policy": SharedBudgetGuard.policy,
              "budget_max_calls": args.max_calls, "budget_max_tokens": args.max_tokens,
              "budget_calls_used": usage["calls"], "budget_tokens_used": usage["tokens"],
              "global_calls_used": budget.calls, "global_tokens_used": budget.tokens,
              "wall_seconds": elapsed, "stopped_early": stopped,
              "cache_path": str(cache_dir / "cache.json")}
    status["worker"] = args.worker
    status["workers"] = args.workers
    status["seeds"] = seeds
    status["budget_before"] = before
    status["cache_only"] = args.cache_only
    status["max_output_tokens"] = MAX_OUTPUT_TOKENS
    status["provider_max_attempts"] = PROVIDER_MAX_ATTEMPTS
    status["parser_policy"] = "strict JSON, then literal-control-character tolerance; schema and semantic validation unchanged"
    status["uncertain_tokens_reserved"] = usage["uncertain_tokens"]
    status["prompt_sha256"] = file_sha256(PROMPT_PATH)
    name = "_status.json" if args.workers == 1 else f"_status_w{args.worker}.json"
    (shards / name).write_text(json.dumps(status, indent=1), encoding="utf-8")
    print(json.dumps(status, indent=1), flush=True)


def load_study(shards):
    data = defaultdict(dict)
    for path in sorted(Path(shards).glob("*.json")):
        if path.name.startswith("_"):
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload["seed"] in data[payload["arm"]]:
            raise ValueError(f"Duplicate episode: {payload['arm']} seed {payload['seed']}")
        data[payload["arm"]][payload["seed"]] = payload
    return data


def outcome_distribution(records):
    attempts = Counter()
    first_attempt = Counter()
    for r in records:
        outcome = r.get("decision_outcome")
        attempts[outcome] += 1
        if not r.get("retry_index"):
            first_attempt[outcome] += 1
    def table(counter):
        total = sum(counter.values())
        return {"total": total,
                "counts": {k: counter.get(k, 0) for k in OUTCOMES if counter.get(k, 0)},
                "percent": {k: round(100 * counter.get(k, 0) / total, 2)
                            for k in OUTCOMES if counter.get(k, 0)},
                "unclassified": sum(v for k, v in counter.items() if k not in OUTCOMES)}
    return {"per_attempt": table(attempts), "per_decision_point": table(first_attempt)}


def action_mix(records):
    produced = Counter()
    fallback_noop = 0
    deliberate_noop = 0
    for r in records:
        action = (r.get("proposed_action") or {}).get("type")
        outcome = r.get("decision_outcome")
        if outcome == "decision_produced":
            produced[action] += 1
        elif outcome == "deliberate_no_op":
            deliberate_noop += 1
        elif r.get("decision_fallback"):
            fallback_noop += 1
    total = sum(produced.values())
    return {"produced_actions": total, "by_type": dict(produced.most_common()),
            "percent_of_produced": {k: round(100 * v / total, 2) for k, v in produced.items()} if total else {},
            "deliberate_no_op": deliberate_noop, "fallback_no_op": fallback_noop}


def sweep_action_mix(path):
    if not Path(path).exists():
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    condition = data.get("conditions", {}).get("discrete_switch") or data
    out = {}
    agents = condition.get("agents", data.get("agents", {}))
    for agent_kind, agent in agents.items():
        arms = {}
        for arm in ("ungated", "always_reject", "twin@1.0", "twin@0.6"):
            entry = agent.get("arms", {}).get(arm)
            if not entry:
                continue
            by_type = entry["composition"]["by_action_type"]
            counts = {k: v["beneficial"] + v["neutral"] + v["harmful"] for k, v in by_type.items()}
            total = sum(counts.values())
            arms[arm] = {"candidates": total,
                         "percent": {k: round(100 * v / total, 2) for k, v in counts.items()} if total else {},
                         "counts": counts}
        out[agent_kind] = arms
    return out


def command_analyze(args):
    from llm_study_reporting import enrich_analysis, render_report
    from llm_study_statistics import outcome_distributions

    data = load_study(args.shards)
    statuses = [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(Path(args.shards).glob("_status*.json"))]
    registered = bool(statuses) and all(s.get("budget_policy") == SharedBudgetGuard.policy for s in statuses)
    expected_seeds = REGISTERED_SEEDS if registered else HISTORICAL_SEEDS
    if set(data) != set(ARMS) or any(set(data[a]) != set(range(expected_seeds)) for a in ARMS):
        raise ValueError(f"Analysis requires exactly four arms and seeds 0..{expected_seeds - 1} in every arm")
    if registered and any((s["budget_max_calls"], s["budget_max_tokens"]) != (REGISTERED_MAX_CALLS, REGISTERED_MAX_TOKENS)
                          for s in statuses):
        raise ValueError("Registered analysis requires the 16000-call / 44000000-token global ceiling")
    status = dict(statuses[0]) if statuses else {}
    if statuses:
        shared = statuses[0].get("budget_policy") == SharedBudgetGuard.policy
        status["budget_max_calls"] = (statuses[0]["budget_max_calls"] if shared
                                      else sum(s.get("budget_max_calls") or 0 for s in statuses))
        status["budget_max_tokens"] = (statuses[0]["budget_max_tokens"] if shared
                                       else sum(s.get("budget_max_tokens") or 0 for s in statuses))
        status["budget_calls_used"] = sum(s.get("budget_calls_used") or 0 for s in statuses)
        status["budget_tokens_used"] = sum(s.get("budget_tokens_used") or 0 for s in statuses)
        status["stopped_early"] = [s["stopped_early"] for s in statuses if s.get("stopped_early")] or None
        status["runs"] = [{"worker": s.get("worker"), "seeds": s.get("seeds"),
                           "calls": s.get("budget_calls_used"), "tokens": s.get("budget_tokens_used"),
                           "wall_seconds": s.get("wall_seconds")} for s in statuses]
    arms = [a for a in ARMS if a in data]
    seeds = sorted(set.intersection(*[set(data[a]) for a in arms])) if arms else []
    rng = np.random.default_rng(20260920)
    idx = rng.integers(0, len(seeds), (5000, len(seeds)))
    nested = {"llm": {arm: {s: data[arm][s] for s in seeds} for arm in arms}}
    output = {
        "provenance": {
            "model": status.get("model"), "provider": "gemini",
            "base_url": status.get("base_url"), "temperature": status.get("temperature"),
            "prompt_version": status.get("prompt_version"),
            "api_key_env": status.get("api_key_env"),
            **{key: status[key] for key in ("vertex_project", "vertex_location") if key in status},
            "cache_key_policy": "sha256(model, temperature, canonical JSON of the full message list); "
                                "hit returns the stored reply and makes no provider call",
            "cache_path": status.get("cache_path"),
            "budget": {"max_calls": status.get("budget_max_calls"),
                       "max_tokens": status.get("budget_max_tokens"),
                       "calls_used": status.get("budget_calls_used"),
                       "tokens_used": status.get("budget_tokens_used"),
                       "stopped_early": status.get("stopped_early")},
            "protocol": {"episode_ticks": 120, "decision_interval_ticks": 10, "retry_cap": 2,
                         "horizon_ticks": 20, "tolerance_margin": 0.0, "harm_threshold_ticks": 3,
                         "gateway_faultable": False, "react_max_steps": 4},
            "seeds": seeds, "n_seeds": len(seeds), "arms": arms,
            "bootstrap_resamples": 5000, "bootstrap_unit": "seed",
        },
        "usage": {}, "arms": {}, "episode_comparisons": {},
        "sweep_reference_action_mix": sweep_action_mix(args.sweep_json),
    }
    totals = {}
    usage_calls = usage_in = usage_out = usage_wall = 0
    for arm in arms:
        shards = [data[arm][s] for s in seeds]
        records = [r for shard in shards for r in shard["records"]]
        by_seed = {s: data[arm][s]["records"] for s in seeds}
        gated = data[arm][seeds[0]]["gated"]
        arrays = per_seed_candidate_arrays(by_seed, seeds, gated)
        totals[arm] = [data[arm][s]["violation_ticks"] for s in seeds]
        usage_calls += sum(s["llm_calls"] for s in shards)
        usage_in += sum(s["llm_tokens_in"] for s in shards)
        usage_out += sum(s["llm_tokens_out"] for s in shards)
        usage_wall += sum(s["wall_seconds"] for s in shards)
        entry = {
            "fidelity": data[arm][seeds[0]]["fidelity"], "gated": gated,
            "episode_mean_violation_ticks": st.mean(totals[arm]),
            "usage": {"llm_calls": sum(s["llm_calls"] for s in shards),
                      "tokens_in": sum(s["llm_tokens_in"] for s in shards),
                      "tokens_out": sum(s["llm_tokens_out"] for s in shards),
                      "wall_seconds": sum(s["wall_seconds"] for s in shards),
                      "cache_hit_records": sum(1 for r in records if r.get("llm_cache_hit"))},
            "proposals": sum(s["proposals"] for s in shards),
            "rejections": sum(s["rejections"] for s in shards),
            "retries": sum(s["retries"] for s in shards),
            "outcomes": outcome_distributions(records),
            "action_mix": action_mix(records),
            "composition": composition_detail(by_seed, seeds),
            "benefit_preserved": ratio_with_ci(arrays["benefit_approved"], arrays["benefit_total"], idx),
            "harm_prevented": ratio_with_ci(arrays["harm_rejected"], arrays["harm_total"], idx),
            "net_sum_d_approved": mean_with_ci(arrays["net_approved"], idx),
            "candidates": int(arrays["n_candidates"].sum()),
            "approved": int(arrays["n_approved"].sum()),
        }
        if arm.startswith("twin@"):
            rows = candidate_corpus(nested, "llm", arm, seeds)
            matches = sum(1 for r in rows
                          if (r["predicted_delta"] > 0) == (r["recorded_verdict"] is False))
            entry["tau_theta"] = {"corpus": corpus_summary(rows, f"{matches}/{len(rows)}"),
                                  "cells": tau_theta_grid(rows, seeds, idx)}
        output["arms"][arm] = entry
    billed = {"calls": 0, "tokens_in": 0, "tokens_out": 0}
    cached = {"calls": 0, "tokens_in": 0, "tokens_out": 0}
    for path in sorted(Path(args.calls_log_root).glob("*/*/llm_calls.jsonl")) if args.calls_log_root else []:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            bucket = cached if record.get("cache_hit") else billed
            bucket["calls"] += 1
            bucket["tokens_in"] += record.get("tokens_in", 0)
            bucket["tokens_out"] += record.get("tokens_out", 0)
    cost = (None if args.price_in is None or args.price_out is None
            else round(billed["tokens_in"] / 1e6 * args.price_in
                       + billed["tokens_out"] / 1e6 * args.price_out, 4))
    output["usage"]["total"] = {"llm_calls_recorded": usage_calls, "tokens_in_recorded": usage_in,
                                "tokens_out_recorded": usage_out, "wall_seconds": usage_wall,
                                "billed": billed, "served_from_cache": cached,
                                "price_per_million_in": args.price_in,
                                "price_per_million_out": args.price_out,
                                "billed_cost_usd": cost,
                                "cost_note": "billed excludes cache hits; recorded totals include them"}
    if "always_reject" in arms:
        pairs = [(a, "always_reject") for a in arms if a != "always_reject"]
        pairs += [("twin@1.0", "twin@0.6")] if "twin@1.0" in arms and "twin@0.6" in arms else []
        results = {f"{a} - {b}": episode_comparison(totals[a], totals[b], rng) for a, b in pairs}
        for comp, adj in zip(results.values(), holm([c["wilcoxon"]["p_value"] for c in results.values()])):
            comp["wilcoxon_holm_p"] = adj
            comp["significant_holm"] = adj < 0.05
        output["episode_comparisons"] = results
    output["per_seed_violation_ticks"] = totals
    enrich_analysis(output, data, statuses, args)
    Path(args.output).write_text(json.dumps(output, indent=1, allow_nan=False), encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(render_report(output), encoding="utf-8")
    print_summary(output)


def print_summary(output):
    prov = output["provenance"]
    usage = output["usage"]["total"]
    print(f"model={prov['model']} seeds={prov['n_seeds']} arms={prov['arms']}")
    print(f"recorded calls={usage['llm_calls_recorded']} tokens_in={usage['tokens_in_recorded']} "
          f"tokens_out={usage['tokens_out_recorded']} wall={usage['wall_seconds']:.0f}s")
    print(f"billed {usage['billed']} cached {usage['served_from_cache']} "
          f"cost_usd={usage['billed_cost_usd']}")
    print(f"budget {prov['budget']['calls_used']}/{prov['budget']['max_calls']} calls, "
          f"{prov['budget']['tokens_used']}/{prov['budget']['max_tokens']} tokens, "
          f"stopped_early={prov['budget']['stopped_early']}")
    fmt = lambda v: "n/e" if v is None else f"{v:.3f}"
    fci = lambda c: "n/e" if c is None else f"[{c[0]:.3f}, {c[1]:.3f}]"
    for arm, entry in output["arms"].items():
        comp = entry["composition"]
        bp, hp, net = entry["benefit_preserved"], entry["harm_prevented"], entry["net_sum_d_approved"]
        print(f"\n== {arm} ep={entry['episode_mean_violation_ticks']:.2f} "
              f"calls={entry['usage']['llm_calls']} proposals={entry['proposals']} "
              f"rejections={entry['rejections']} retries={entry['retries']}")
        print(f"   outcomes/attempt {entry['outcomes']['per_attempt']['counts']} "
              f"pct {entry['outcomes']['per_attempt']['percent']}")
        print(f"   outcomes/decision {entry['outcomes']['per_decision_point']['counts']} "
              f"pct {entry['outcomes']['per_decision_point']['percent']}")
        print(f"   action_mix {entry['action_mix']}")
        print(f"   cand={entry['candidates']} appr={entry['approved']} "
              f"b/n/h={comp['beneficial']}/{comp['neutral']}/{comp['harmful']} (>3 {comp['harmful_above_3']}) "
              f"B={comp['available_benefit']} H={comp['available_harm']} "
              f"Bq={comp['benefit_quantiles']} Hq={comp['harm_quantiles']} "
              f"seedsB={comp['seeds_with_beneficial_candidate']} types={comp['by_action_type']}")
        print(f"   BP={fmt(bp['point'])} {fci(bp['ci95'])} ({bp['numerator']:.0f}/{bp['denominator']:.0f}) "
              f"HP={fmt(hp['point'])} {fci(hp['ci95'])} ({hp['numerator']:.0f}/{hp['denominator']:.0f}) "
              f"net={net['mean_per_seed']:.3f} {fci(net['bootstrap_ci95'])} total={net['total']:.0f} "
              f"nz={net['nonzero_seeds']}")
        if "tau_theta" in entry:
            print(f"   corpus {entry['tau_theta']['corpus']}")
            for cell in entry["tau_theta"]["cells"]:
                if (cell["tau"], cell["theta"]) in ((0, 3), (3, 3)):
                    c = cell["counts"]
                    fp = cell["false_positives"]
                    print(f"   tau={cell['tau']} theta={cell['theta']} "
                          f"TP/FN/FP/TN={c['tp']}/{c['fn']}/{c['fp']}/{c['tn']} "
                          f"recall={fmt(cell['recall']['point'])} {fci(cell['recall']['ci95'])} "
                          f"FPR={fmt(cell['fpr']['point'])} {fci(cell['fpr']['ci95'])} "
                          f"prec={fmt(cell['precision']['point'])} "
                          f"FP def/pred={fp['definitional']}/{fp['predictive']} "
                          f"share={fmt(fp['definitional_share'])}")
    for name, c in output.get("episode_comparisons", {}).items():
        w = c["wilcoxon"]
        print(f"EP {name:26s} mean={c['mean']:+.3f} sd={c['sd']:.2f} t={fci(c['t_ci95'])} "
              f"boot={fci(c['bootstrap_ci95'])} nz={c['n_nonzero']} B/W={c['better']}/{c['worse']} "
              f"p={w['p_value']:.4g} holm={c['wilcoxon_holm_p']:.4g} "
              f"power={c['observed_power_at_n']:.3f} need={c['seeds_for_80pct_power_normal']}")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--shards", required=True)
    run.add_argument("--workdir", required=True)
    run.add_argument("--cache-dir", required=True)
    run.add_argument("--env-file", default=str(ROOT / ".env"))
    run.add_argument("--project", default=None, help="Vertex AI project (default: GOOGLE_CLOUD_PROJECT)")
    run.add_argument("--location", default=None, help="Vertex AI location (default: GOOGLE_CLOUD_LOCATION or global)")
    run.add_argument("--model", default="gemini-3.6-flash")
    run.add_argument("--seed-start", type=int, default=0)
    run.add_argument("--seed-count", type=int, default=REGISTERED_SEEDS)
    run.add_argument("--worker", type=int, default=0)
    run.add_argument("--workers", type=int, default=1)
    run.add_argument("--arms", default=None)
    run.add_argument("--max-calls", type=int, default=REGISTERED_MAX_CALLS)
    run.add_argument("--max-tokens", type=int, default=REGISTERED_MAX_TOKENS)
    run.add_argument("--timeout-seconds", type=float, default=30.0)
    run.add_argument("--react-timeout-seconds", type=float, default=20.0)
    run.add_argument("--cache-only", action="store_true")
    run.add_argument("--pilot", action="store_true")
    analyze = sub.add_parser("analyze")
    analyze.add_argument("--shards", required=True)
    analyze.add_argument("--output", required=True)
    analyze.add_argument("--sweep-json", default=str(ROOT / "docs/research/extended_fidelity_sweep.json"))
    analyze.add_argument("--calls-log-root", default=None)
    analyze.add_argument("--price-in", type=float, required=True)
    analyze.add_argument("--price-out", type=float, required=True)
    analyze.add_argument("--markdown", default=None)
    analyze.add_argument("--replay-audit", default=None)
    args = parser.parse_args()
    if args.command == "run":
        command_run(args)
    else:
        command_analyze(args)


if __name__ == "__main__":
    main()
