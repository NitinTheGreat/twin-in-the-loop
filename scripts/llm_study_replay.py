from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import URLError
import zipfile

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_RUNTIME = ROOT / "results/llm_descriptive_study/runtime_snapshot"
LIVE_RUNTIME = ROOT / "results/llm_descriptive_study/live_runtime_snapshot"
if "--historical" in sys.argv and not HISTORICAL_RUNTIME.exists():
    raise SystemExit("Extract the archived runtime_snapshot into results/llm_descriptive_study before historical replay")
RUNTIME_ROOT = (LIVE_RUNTIME if LIVE_RUNTIME.exists() else ROOT) if "--live" in sys.argv else (
    HISTORICAL_RUNTIME if HISTORICAL_RUNTIME.exists() else ROOT)
sys.path.insert(0, str(RUNTIME_ROOT / "src"))
sys.path.insert(0, str(RUNTIME_ROOT / "scripts"))

from langgraph.checkpoint.memory import MemorySaver

from gateway_uncertainty_ablation import gate_class
from twinloop.actions.schema import ParseError, parse_action
from twinloop.agent.llm_agent import LLMAgent
from twinloop.config import EvaluationConfig, ExperimentConfig, FaultConfig, GraphConfig, LLMConfig, SimConfig, TwinConfig
from twinloop.experiment import runner
from twinloop.experiment.arms import RunSpec
from twinloop.llm.budget import BudgetExceeded, BudgetGuard
from twinloop.llm.cache import CacheMiss, ResponseCache, cache_key
from twinloop.llm.client import LLMClient, canonical_prompt
from twinloop.twin.validator import TwinValidator

FIELDS = (
    "seed", "tick", "decision_index", "proposal_index", "retry_index", "proposed_action",
    "schema_valid", "semantically_valid", "twin_verdict", "twin_reason", "was_applied",
    "twin_predicted_violation_ticks_action", "twin_predicted_violation_ticks_noop",
    "counterfactual_violation_ticks_action", "counterfactual_violation_ticks_noop",
    "counterfactual_harm_delta", "ground_truth_harmful", "decision_outcome", "decision_fallback",
    "decision_trace", "exhausted", "prompt_version",
)
ARMS = ("ungated", "always_reject", "twin@1.0", "twin@0.6")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class NoNetworkProvider:
    def __init__(self):
        self.calls = 0

    def complete(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("Network provider is disabled for this audit")


class AuditedCache(ResponseCache):
    def __init__(self, path):
        super().__init__(path, cache_only=True)
        self.lookups = []

    def get(self, key):
        value = super().get(key)
        self.lookups.append({"key": key, "hit": value is not None})
        return value

    def put(self, key, value):
        raise AssertionError("Source response cache is read-only during audit")


class RecordedAgent:
    name = "llm"
    prompt_version = "v1"

    def __init__(self, records, cached_agent, cache):
        self.records = records
        self.cached_agent = cached_agent
        self.cache = cache
        self.index = 0
        self.last_trace = {}
        self.cache_audit = []

    def set_context(self, context):
        self.cached_agent.set_context(context)

    def decide(self, obs, feedback=None):
        if self.index >= len(self.records):
            raise AssertionError("Replay requested an extra proposal")
        recorded = self.records[self.index]
        if obs.tick != recorded["tick"]:
            raise AssertionError(f"Replay tick differs: {obs.tick} != {recorded['tick']}")
        if (feedback is not None) != recorded["decision_trace"]["feedback_present"]:
            raise AssertionError("Replay feedback availability differs")
        action = parse_action(recorded["proposed_action"])
        if isinstance(action, ParseError):
            raise AssertionError(action.message)
        start = len(self.cache.lookups)
        audit = {"tick": obs.tick, "retry_index": recorded["retry_index"],
                 "historical_outcome": recorded["decision_outcome"]}
        try:
            cached_action = self.cached_agent.decide(obs, feedback)
            audit["status"] = "matched" if (
                cached_action.model_dump() == recorded["proposed_action"]
                and self.cached_agent.last_trace == recorded["decision_trace"]
            ) else "different"
            if audit["status"] == "different":
                audit["cached_action"] = cached_action.model_dump()
                audit["cached_outcome"] = self.cached_agent.last_trace["outcome"]
                audit["trace_fields_differing"] = [
                    key for key in set(self.cached_agent.last_trace) | set(recorded["decision_trace"])
                    if self.cached_agent.last_trace.get(key) != recorded["decision_trace"].get(key)
                ]
        except CacheMiss as error:
            audit["status"] = "cache_miss"
            audit["error"] = str(error)
        self.cached_agent._last_proposed = action
        audit["lookups"] = self.cache.lookups[start:]
        self.cache_audit.append(audit)
        self.last_trace = deepcopy(recorded["decision_trace"])
        self.index += 1
        return action


class JournalClient:
    def __init__(self, events, cache, config):
        self.events = events
        self.cache = cache
        self.config = config
        self.index = 0
        self.records = []
        self.verified_keys = []

    def complete(self, messages, *, timeout_seconds=None):
        if self.index >= len(self.events):
            raise AssertionError("Replay requested an unrecorded LLM event")
        event = self.events[self.index]
        self.index += 1
        prompt = canonical_prompt(messages)
        key = cache_key(self.config.model, self.config.temperature, prompt)
        if key != event["cache_key"]:
            raise AssertionError(f"Replayed prompt key differs at event {self.index - 1}: {key} != {event['cache_key']}")
        self.verified_keys.append(key)
        if event["event"] == "provider_error":
            if event["error_type"] == "timeout":
                raise TimeoutError("Recorded provider timeout")
            raise URLError("Recorded provider error")
        if event["event"] == "budget_error":
            raise BudgetExceeded("Recorded budget guard exhaustion")
        cached = self.cache.get(key)
        if cached is None:
            raise AssertionError(f"Recorded response is absent from cache: {key}")
        defaults = {"tokens_thinking": 0, "model_version": None, "usage_complete": False, "usage_metadata": {}}
        for name in ("tokens_in", "tokens_out", "tokens_thinking", "model_version", "usage_complete", "usage_metadata"):
            if event.get(name, defaults.get(name)) != cached.get(name, defaults.get(name)):
                raise AssertionError(f"Cached {name} differs from journal at {key}")
        if len(cached["text"]) != event["response_chars"] or len(prompt) != event["prompt_chars"]:
            raise AssertionError("Cached response or regenerated prompt length differs from journal")
        record = SimpleNamespace(**event)
        self.records.append(record)
        if self.index < len(self.events):
            following = self.events[self.index]
            if following.get("event") == "budget_error" and following.get("stage") == "after_response":
                if following["cache_key"] != key:
                    raise AssertionError("Post-response budget event has a different prompt key")
                self.index += 1
                self.verified_keys.append(key)
                raise BudgetExceeded("Recorded post-response budget guard exhaustion")
        if event.get("budget_error"):
            raise BudgetExceeded(event["budget_error"])
        return cached["text"], record


class JournalAgent(LLMAgent):
    def __init__(self, client, config, slo_config, actions_config, records):
        super().__init__(client, config, slo_config, actions_config)
        self.expected_records = records
        self.index = 0

    def decide(self, obs, feedback=None):
        if self.index >= len(self.expected_records):
            raise AssertionError("Model replay requested an extra proposal")
        expected = self.expected_records[self.index]
        if expected["tick"] != obs.tick:
            raise AssertionError("Model replay tick differs")
        offsets = iter(expected["decision_trace"]["clock_offsets"])
        self.clock = offsets.__next__
        action = super().decide(obs, feedback)
        if next(offsets, None) is not None:
            raise AssertionError("Recorded decision timing was not fully consumed")
        if action.model_dump() != expected["proposed_action"]:
            raise AssertionError(f"Model replay action differs at proposal {self.index}")
        if self.last_trace != expected["decision_trace"]:
            fields = [key for key in set(self.last_trace) | set(expected["decision_trace"])
                      if self.last_trace.get(key) != expected["decision_trace"].get(key)]
            raise AssertionError(f"Model replay trace differs at proposal {self.index}: {fields}")
        self.index += 1
        return action


def portable_path(recorded, source):
    path = Path(recorded)
    if path.exists() and path.is_relative_to(source):
        return path
    parts = path.parts
    for index, part in enumerate(parts):
        if part in ("live", source.name):
            candidate = source.joinpath(*parts[index + 1:])
            if candidate.exists():
                return candidate
    raise FileNotFoundError(f"Recorded artifact is unavailable under source root: {recorded}")


def live_replay_one(arguments):
    source_text, output_text, shard_name = arguments
    source, output = Path(source_text), Path(output_text)
    original = json.loads((source / "shards" / shard_name).read_text(encoding="utf-8"))
    arm, seed = original["arm"], original["seed"]
    allocation = json.loads((source / "shards/_allocation.json").read_text(encoding="utf-8"))
    worker = seed % allocation["workers"]
    config = ExperimentConfig.model_validate(json.loads(
        (source / "shards" / f"_config_w{worker}.json").read_text(encoding="utf-8")))
    cache_path = portable_path(str(Path(config.llm.cache_dir) / "cache.json"), source)
    cache = AuditedCache(cache_path)
    if original.get("llm_journal_path"):
        journal_path = portable_path(original["llm_journal_path"], source)
    else:
        journals = list(source.glob(f"work*/{arm.replace('@', '_')}_{seed:04d}_*/llm_calls.jsonl"))
        if len(journals) != 1:
            raise AssertionError(f"Expected one episode journal, got {len(journals)} for {arm}/{seed}")
        journal_path = journals[0]
    events = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    client = JournalClient(events, cache, config.llm)
    agent = JournalAgent(client, config.llm, config.slo, config.actions, original["records"])
    gate = gate_class("always_reject") if arm == "always_reject" else TwinValidator
    provider = NoNetworkProvider()
    budget = BudgetGuard(0, 0)
    with patch.object(runner, "_make_agent", return_value=agent), patch.object(runner, "TwinValidator", gate):
        summary, _, records = runner.run_single(
            config, RunSpec("LLM", "llm", original["gated"], seed, original["fidelity"]),
            output / "live_replay_work" / Path(shard_name).stem,
            lambda: provider, budget, cache, True, MemorySaver(),
        )
    mismatches = []
    summary_fields = (("violation_ticks", "slo_violation_ticks"), ("proposals", "proposals"),
                      ("rejections", "rejections"), ("retries", "retries"),
                      ("llm_calls", "llm_calls"), ("llm_tokens_in", "llm_tokens_in"),
                      ("llm_tokens_out", "llm_tokens_out"))
    for old_key, new_key in summary_fields:
        if original[old_key] != summary[new_key]:
            mismatches.append({"field": old_key, "original": original[old_key], "replay": summary[new_key]})
    compared_fields = FIELDS + ("llm_tokens_in", "llm_tokens_out", "llm_latency_ms", "llm_cache_hit")
    for index, (old, new) in enumerate(zip(original["records"], records)):
        for field in compared_fields:
            if old.get(field) != new.get(field):
                mismatches.append({"record": index, "field": field,
                                   "original": old.get(field), "replay": new.get(field)})
    if len(records) != len(original["records"]) or agent.index != len(original["records"]):
        mismatches.append({"field": "record_count"})
    if client.index != len(events):
        mismatches.append({"field": "unconsumed_journal_events", "count": len(events) - client.index})
    return {"arm": arm, "seed": seed, "source_shard": shard_name,
            "journal_path": journal_path.relative_to(source).as_posix(),
            "violation_ticks": summary["slo_violation_ticks"], "decision_points": summary["proposals"],
            "attempts": len(records), "non_noop_candidates": sum(r["proposed_action"]["type"] != "no_op" for r in records),
            "verified_prompt_keys": len(client.verified_keys), "journal_events": dict(Counter(e["event"] for e in events)),
            "mismatches": mismatches, "provider_calls": provider.calls,
            "charged_calls": budget.calls, "charged_tokens": budget.tokens}


def live_main(args):
    source = args.source if args.source != ROOT / "results/llm_descriptive_study/source" else ROOT / "results/llm_descriptive_study/live"
    if args.archive_only:
        raise ValueError("Use a complete live audit to create its matching archive")
    started = time.perf_counter()
    paper_before = sha256(ROOT / "paper/main.tex")
    shards = sorted((source / "shards").glob("*__*.json"))
    pairs = [(payload["arm"], payload["seed"]) for payload in
             (json.loads(path.read_text(encoding="utf-8")) for path in shards)]
    if len(set(pairs)) != len(pairs):
        raise ValueError("Duplicate arm/seed records")
    if not args.allow_partial and set(pairs) != {(arm, seed) for arm in ARMS for seed in range(30)}:
        raise ValueError("Expected exactly four arms and seeds 0 through 29")
    manifest = [{"path": path.relative_to(source).as_posix(), "bytes": path.stat().st_size, "sha256": sha256(path)}
                for path in sorted(source.rglob("*")) if path.is_file() and path.suffix not in (".lock", ".tmp")]
    episodes = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        jobs = [pool.submit(live_replay_one, (str(source), str(args.output), path.name)) for path in shards]
        for job in as_completed(jobs):
            episodes.append(job.result())
            if len(episodes) % 10 == 0:
                print(f"Replayed live journals for {len(episodes)}/{len(shards)} episodes", flush=True)
    episodes.sort(key=lambda item: (ARMS.index(item["arm"]), item["seed"]))
    changed = [entry["path"] for entry in manifest if sha256(source / entry["path"]) != entry["sha256"]]
    result = {"method": "Regenerate every LLM prompt, replay chronological cached responses/provider errors and recorded decision clock readings, then independently recompute all simulator outcomes and counterfactuals.",
              "complete_study": len(episodes) == 120, "episodes": len(episodes),
              "decision_points": sum(e["decision_points"] for e in episodes),
              "attempts": sum(e["attempts"] for e in episodes),
              "non_noop_candidates": sum(e["non_noop_candidates"] for e in episodes),
              "verified_prompt_keys": sum(e["verified_prompt_keys"] for e in episodes),
              "mismatches": sum(len(e["mismatches"]) for e in episodes),
              "provider_calls": sum(e["provider_calls"] for e in episodes),
              "charged_calls": sum(e["charged_calls"] for e in episodes),
              "charged_tokens": sum(e["charged_tokens"] for e in episodes), "incremental_cost_usd": 0.0,
              "source_files_changed_during_audit": changed,
              "paper_main_sha256_before": paper_before, "paper_main_sha256_after": sha256(ROOT / "paper/main.tex"),
              "limits": ["Replay verifies saved provider responses and simulator computations; it does not establish provider billing independently or imply future calls reproduce the same model responses.",
                         "A0 is reused by the study analysis; this audit does not rerun it."],
              "wall_seconds": time.perf_counter() - started, "per_episode": episodes}
    args.output.mkdir(parents=True, exist_ok=True)
    audit_path = args.output / ("live_partial_replay_audit.json" if args.allow_partial else "live_replay_audit.json")
    audit_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if not args.allow_partial and not result["mismatches"] and not changed:
        manifest_path = args.output / "live_source_manifest.json"
        manifest_path.write_text(json.dumps({"files": manifest}, indent=2) + "\n", encoding="utf-8")
        snapshot = args.output / "live_runtime_snapshot"
        paths = list((RUNTIME_ROOT / "src/twinloop").rglob("*.py")) + list((RUNTIME_ROOT / "src/twinloop/agent/prompts").glob("*.txt"))
        paths += [RUNTIME_ROOT / "scripts/gateway_uncertainty_ablation.py", RUNTIME_ROOT / "requirements.txt"]
        for path in paths:
            destination = snapshot / path.relative_to(RUNTIME_ROOT)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if path.resolve() != destination.resolve():
                shutil.copy2(path, destination)
        result["code_sha256"] = {path.relative_to(RUNTIME_ROOT).as_posix(): sha256(path) for path in paths}
        result["code_sha256"]["scripts/llm_study_replay.py"] = sha256(Path(__file__))
        audit_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        archive_path = ROOT / "docs/research/llm_descriptive_study_artifacts.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            for entry in manifest:
                path = source / entry["path"]
                if re.search(rb"AIza[0-9A-Za-z_-]{30,}|[?&]key=[0-9A-Za-z_-]{20,}|Bearer\s+[0-9A-Za-z._-]{20,}", path.read_bytes()):
                    raise ValueError("Possible credential in artifact " + entry["path"])
                bundle.write(path, "live/" + entry["path"])
            for path in sorted(snapshot.rglob("*")):
                if path.is_file():
                    bundle.write(path, path.relative_to(args.output).as_posix())
            for path in (audit_path, manifest_path):
                bundle.write(path, path.name)
            bundle.write(Path(__file__), "scripts/llm_study_replay.py")
        print(json.dumps({"archive": str(archive_path), "bytes": archive_path.stat().st_size, "sha256": sha256(archive_path)}))
    print(json.dumps({key: value for key, value in result.items() if key not in ("per_episode", "code_sha256")}, indent=2))
    if result["mismatches"] or (changed and not args.allow_partial) or paper_before != result["paper_main_sha256_after"]:
        raise SystemExit(1)


def replay_one(arguments):
    source_text, output_text, shard_name = arguments
    source = Path(source_text)
    output = Path(output_text)
    original = json.loads((source / "shards" / shard_name).read_text(encoding="utf-8"))
    arm, seed = original["arm"], original["seed"]
    cache_directory = "cache" if seed < 2 else f"cache_w{seed % 4}"
    cache = AuditedCache(source / cache_directory / "cache.json")
    config = ExperimentConfig(
        sim=SimConfig(episode_ticks=120), fault=FaultConfig(gateway_faultable=False),
        graph=GraphConfig(decision_interval_ticks=10, retry_cap=2),
        twin=TwinConfig(horizon_ticks=20, tolerance_margin=0.0),
        evaluation=EvaluationConfig(harm_threshold_ticks=3),
        llm=LLMConfig(provider="gemini", model="gemini-3.6-flash", temperature=0.0,
                      cache_only=True, cache_dir=str(source / cache_directory),
                      max_calls=0, max_tokens=0, timeout_seconds=30.0,
                      react_timeout_seconds=20.0, react_max_steps=4, prompt_version="v1"),
    )
    provider = NoNetworkProvider()
    budget = BudgetGuard(0, 0)
    episode_output = output / "replay_work" / Path(shard_name).stem
    client = LLMClient(provider, config.llm, cache=cache, budget=budget,
                       log_path=episode_output / "cache_lookups.jsonl")
    agent = RecordedAgent(original["records"], LLMAgent(client, config.llm, config.slo, config.actions), cache)
    gate = gate_class("always_reject") if arm == "always_reject" else TwinValidator
    with patch.object(runner, "_make_agent", return_value=agent), patch.object(runner, "TwinValidator", gate):
        summary, _, records = runner.run_single(
            config, RunSpec("LLM", "llm", original["gated"], seed, original["fidelity"]),
            episode_output, lambda: provider, budget, cache, True, MemorySaver(),
        )
    mismatches = []
    for historical_key, replay_key in (("violation_ticks", "slo_violation_ticks"),
                                       ("proposals", "proposals"), ("rejections", "rejections"),
                                       ("retries", "retries")):
        if original[historical_key] != summary[replay_key]:
            mismatches.append({"field": historical_key, "original": original[historical_key],
                               "replay": summary[replay_key]})
    if len(records) != len(original["records"]):
        mismatches.append({"field": "record_count", "original": len(original["records"]), "replay": len(records)})
    for index, (old, new) in enumerate(zip(original["records"], records)):
        for field in FIELDS:
            if old.get(field) != new.get(field):
                mismatches.append({"record": index, "field": field,
                                   "original": old.get(field), "replay": new.get(field)})
    if agent.index != len(original["records"]):
        mismatches.append({"field": "unconsumed_proposals", "count": len(original["records"]) - agent.index})
    return {"arm": arm, "seed": seed, "source_shard": shard_name,
            "violation_ticks": summary["slo_violation_ticks"], "decision_points": summary["proposals"],
            "attempts": len(records), "non_noop_candidates": sum(r["proposed_action"]["type"] != "no_op" for r in records),
            "mismatches": mismatches, "cache_directory": cache_directory,
            "cache_attempt_status": dict(Counter(r["status"] for r in agent.cache_audit)),
            "cache_attempts": agent.cache_audit, "provider_calls": provider.calls,
            "budget_calls": budget.calls, "budget_tokens": budget.tokens}


def archive_artifacts(source, output, archive_path):
    archive_path = Path(archive_path)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                bundle.write(path, "source/" + path.relative_to(source).as_posix())
        for name in ("source_manifest.json", "replay_audit.json", "historical_replay_audit.json"):
            if (output / name).exists():
                bundle.write(output / name, name)
        for directory in ("runtime_snapshot", "historical_report"):
            for path in sorted((output / directory).rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    bundle.write(path, path.relative_to(output).as_posix())
        bundle.write(Path(__file__), "scripts/llm_study_replay.py")
    return {"path": str(archive_path.relative_to(ROOT)) if archive_path.is_relative_to(ROOT) else str(archive_path),
            "bytes": archive_path.stat().st_size, "sha256": sha256(archive_path)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "results/llm_descriptive_study/source")
    parser.add_argument("--output", type=Path, default=ROOT / "results/llm_descriptive_study")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--archive", type=Path, default=ROOT / "docs/research/llm_descriptive_study_artifacts.zip")
    parser.add_argument("--archive-only", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--historical", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    if args.live and args.historical:
        raise ValueError("Choose either historical or live replay")
    if args.live:
        live_main(args)
        return
    if args.archive_only:
        print(json.dumps(archive_artifacts(args.source, args.output, args.archive), indent=2))
        return
    started = time.perf_counter()
    args.output.mkdir(parents=True, exist_ok=True)
    paper_before = sha256(ROOT / "paper/main.tex")
    manifest = json.loads((args.output / "source_manifest.json").read_text(encoding="utf-8"))
    artifact_mismatches = [entry["path"] for entry in manifest["files"]
                           if sha256(args.source / entry["path"]) != entry["sha256"]]
    shards = [p for p in sorted((args.source / "shards").glob("*.json")) if not p.name.startswith("_")]
    by_arm = {arm: [] for arm in ARMS}
    for path in shards:
        payload = json.loads(path.read_text(encoding="utf-8"))
        by_arm[payload["arm"]].append(payload["seed"])
    if any(sorted(seeds) != list(range(30)) for seeds in by_arm.values()):
        raise ValueError("Expected exactly the four specified arms, each with seeds 0 through 29")
    source_hashes_before = {entry["path"]: sha256(args.source / entry["path"]) for entry in manifest["files"]}
    episodes = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        jobs = [pool.submit(replay_one, (str(args.source), str(args.output), path.name)) for path in shards]
        for job in as_completed(jobs):
            episode = job.result()
            episodes.append(episode)
            if len(episodes) % 10 == 0:
                print(f"Replayed {len(episodes)}/120 episodes; simulator mismatches "
                      f"{sum(len(e['mismatches']) for e in episodes)}", flush=True)
    episodes.sort(key=lambda item: (ARMS.index(item["arm"]), item["seed"]))
    cache_status = Counter()
    cache_exception_outcomes = Counter()
    for episode in episodes:
        cache_status.update(episode["cache_attempt_status"])
        cache_exception_outcomes.update(attempt["historical_outcome"] for attempt in episode["cache_attempts"]
                                        if attempt["status"] != "matched")
    source_modified = [name for name, old_hash in source_hashes_before.items() if sha256(args.source / name) != old_hash]
    module_paths = sorted((RUNTIME_ROOT / "src/twinloop").rglob("*.py"))
    module_paths += [RUNTIME_ROOT / "src/twinloop/agent/prompts/llm_agent_v1.txt", Path(__file__),
                     RUNTIME_ROOT / "scripts/gateway_uncertainty_ablation.py"]
    result = {
        "method": "Recompute simulator and scheduled counterfactuals from recorded action/trace streams; separately regenerate cached model decisions at every historical state.",
        "episodes": len(episodes), "seeds": list(range(30)), "arms": list(ARMS),
        "decision_points": sum(e["decision_points"] for e in episodes),
        "attempts": sum(e["attempts"] for e in episodes),
        "non_noop_candidates": sum(e["non_noop_candidates"] for e in episodes),
        "compared_record_fields": list(FIELDS),
        "simulator_mismatches": sum(len(e["mismatches"]) for e in episodes),
        "provider_calls": sum(e["provider_calls"] for e in episodes),
        "charged_calls": sum(e["budget_calls"] for e in episodes),
        "charged_tokens": sum(e["budget_tokens"] for e in episodes), "incremental_cost_usd": 0.0,
        "cache_replay_attempt_status": dict(cache_status),
        "cache_nonmatching_historical_outcomes": dict(cache_exception_outcomes),
        "limits": [
            "Action replay verifies simulation, candidate labels and gates; recorded model traces are supplied, so it does not independently establish how the model originally reasoned.",
            "Cache audit uses the original worker cache and historical states; after each diagnostic it restores the recorded action, so success counts are per attempt rather than a claim of full autonomous episode cache replay.",
            "The historical cache stores successful response text and visible input/output tokens, without provider errors, response timing, full prompts or returned provider model identity.",
            "Historical deadlines and failed provider requests are not reproducible from successful response entries alone; missing or different cache attempts must be replayed from the preserved decision trace.",
            "The original full configuration was not serialized; protocol and command-line defaults reconstruct 30-second provider timeout, 20-second decision timeout, four calls per attempt and reserve fraction 0.25.",
            "A0 is reused from the existing sweep by the study analysis and is not rerun here.",
        ],
        "artifact_manifest_mismatches": artifact_mismatches, "source_files_modified_during_audit": source_modified,
        "paper_main_sha256_before": paper_before, "paper_main_sha256_after": sha256(ROOT / "paper/main.tex"),
        "code_sha256": {path.relative_to(ROOT).as_posix(): sha256(path) for path in module_paths},
        "runtime_source": RUNTIME_ROOT.relative_to(ROOT).as_posix(),
        "wall_seconds": time.perf_counter() - started, "per_episode": episodes,
    }
    (args.output / "replay_audit.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (args.output / "historical_replay_audit.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    archive = archive_artifacts(args.source, args.output, args.archive)
    print(json.dumps({key: value for key, value in result.items() if key not in ("per_episode", "code_sha256")}, indent=2))
    print(json.dumps({"archive": archive}, indent=2))
    if result["simulator_mismatches"] or artifact_mismatches or source_modified or paper_before != result["paper_main_sha256_after"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
