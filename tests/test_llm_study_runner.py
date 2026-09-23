import json
from argparse import Namespace
from pathlib import Path

import pytest

from scripts import llm_descriptive_study as study


def arguments(tmp_path, worker=0, workers=1, **overrides):
    values = {
        "shards": str(tmp_path / "shards"), "workdir": str(tmp_path / f"work_w{worker}"),
        "cache_dir": str(tmp_path / f"cache_w{worker}"), "env_file": None,
        "project": "test-project", "location": "global", "model": "test-model",
        "seed_start": 0, "seed_count": 72, "worker": worker, "workers": workers, "arms": None,
        "max_calls": 16000, "max_tokens": 44000000, "timeout_seconds": 30.0,
        "react_timeout_seconds": 20.0, "cache_only": False, "pilot": False,
    }
    values.update(overrides)
    return Namespace(**values)


@pytest.fixture
def recorded_budget_failure(monkeypatch):
    calls = []

    def no_network(*args, **kwargs):
        pytest.fail("runner tests must not query a provider")

    def run_single(config, spec, output_dir, factory, budget, cache, counterfactual, checkpointer):
        calls.append({"seed": spec.seed, "max_calls": budget.max_calls, "max_tokens": budget.max_tokens})
        budget.charge_call(1)
        budget.charge_tokens(1)
        summary = {"slo_violation_ticks": 0, "llm_calls": 1, "llm_tokens_in": 1,
                   "llm_tokens_out": 0, "proposals": 1, "rejections": 0, "retries": 0}
        rows = [{"seed": spec.seed, "tick": 0, "decision_index": 0, "proposal_index": 0,
                 "retry_index": 0, "decision_outcome": "provider_budget_exhausted",
                 "decision_fallback": True, "proposed_action": {"type": "no_op"}}]
        return summary, None, rows

    monkeypatch.setattr(study.GeminiProvider, "complete", no_network)
    monkeypatch.setattr(study, "run_single", run_single)
    return calls


def test_stops_after_budget_exhaustion_classified_inside_agent(tmp_path, recorded_budget_failure):
    args = arguments(tmp_path)
    study.command_run(args)
    shards = Path(args.shards)
    assert len(recorded_budget_failure) == 1
    assert [p.name for p in shards.glob("*__*.json")] == ["ungated__0000.json"]
    status = json.loads((shards / "_status.json").read_text())
    assert status["stopped_early"]["reason"] == "provider budget exhausted during recorded episode"
    assert status["stopped_early"]["seed"] == 0
    assert status["budget_calls_used"] == 1
    assert not (shards / "_worker_0.lock").exists()
    assert study.runner_mod.TwinValidator is study.REAL_TWIN


@pytest.mark.parametrize("global_calls,global_tokens", [(16000, 44000000)])
def test_all_workers_share_one_global_ledger(tmp_path, recorded_budget_failure, global_calls, global_tokens):
    for worker in range(4):
        study.command_run(arguments(tmp_path, worker=worker, workers=4,
                                    max_calls=global_calls, max_tokens=global_tokens))
    assert all(row["max_calls"] == global_calls for row in recorded_budget_failure)
    assert all(row["max_tokens"] == global_tokens for row in recorded_budget_failure)
    assert [row["seed"] for row in recorded_budget_failure] == [0, 1, 2, 3]
    shards = tmp_path / "shards"
    allocation = json.loads((shards / "_allocation.json").read_text())
    assert allocation["global_max_calls"] == global_calls
    assert allocation["global_max_tokens"] == global_tokens
    assert allocation["policy"] == "shared_global_ledger"
    ledger = json.loads((shards / "_budget.json").read_text())
    assert ledger["max_calls"] == global_calls and ledger["calls"] == 4
    assert sorted(ledger["by_owner"]) == ["w0", "w1", "w2", "w3"]
    assert not list(shards.glob("_budget_w*.json"))
    statuses = [json.loads(p.read_text()) for p in shards.glob("_status_w*.json")]
    assert all(row["budget_max_calls"] == global_calls for row in statuses)
    assert sum(row["budget_calls_used"] for row in statuses) == ledger["calls"]
    assert not list(shards.glob("*.lock"))


def test_pilot_is_limited_to_two_seeds(tmp_path, recorded_budget_failure):
    with pytest.raises(SystemExit, match="pilot is limited"):
        study.command_run(arguments(tmp_path, pilot=True, seed_count=3))
    with pytest.raises(SystemExit, match="pilot is limited"):
        study.command_run(arguments(tmp_path, pilot=True, seed_count=2, arms="ungated"))
    assert not recorded_budget_failure


def test_pilot_records_run_identity(tmp_path, recorded_budget_failure):
    study.command_run(arguments(tmp_path, pilot=True, seed_count=2, max_calls=400))
    allocation = json.loads((tmp_path / "shards/_allocation.json").read_text())
    identity = allocation["experiment_identity"]
    run = identity["run"]
    assert identity["pilot"] is True and identity["seeds"] == [0, 1]
    assert allocation["global_max_calls"] == 400
    assert run["provider"] == "gemini" and run["provider_platform"] == "vertex_ai"
    assert run["vertex_project"] == "test-project" and run["vertex_location"] == "global"
    assert run["model"] == "test-model" and run["temperature"] == 0.0
    assert run["prompt_version"] == "v1" and run["parser_version"]
    assert run["parser_matches_verified"] is True
    assert run["thinking_config"] == {"sent": None, "policy": "model default",
                                      "usage_field": "usageMetadata.thoughtsTokenCount"}
    status = json.loads((tmp_path / "shards/_status.json").read_text())
    assert status["run_identity"] == run


@pytest.mark.parametrize("change", ["model", "prompt"])
def test_changed_experiment_identity_rejects_resume_and_releases_lock(tmp_path, monkeypatch,
                                                                    recorded_budget_failure, change):
    args = arguments(tmp_path)
    study.command_run(args)
    shards = Path(args.shards)
    original_manifest = (shards / "_allocation.json").read_bytes()
    if change == "model":
        args.model = "different-model"
    else:
        original_read = Path.read_bytes
        prompt = study.ROOT / "src/twinloop/agent/prompts/llm_agent_v1.txt"

        def changed_read(path):
            value = original_read(path)
            return value + b"\nChanged prompt" if path == prompt else value

        monkeypatch.setattr(Path, "read_bytes", changed_read)
    with pytest.raises(SystemExit, match="allocation cannot change on resume"):
        study.command_run(args)
    assert len(recorded_budget_failure) == 1
    assert (shards / "_allocation.json").read_bytes() == original_manifest
    assert not (shards / "_worker_0.lock").exists()


def test_different_model_in_another_worker_is_rejected_before_episode(tmp_path, recorded_budget_failure):
    study.command_run(arguments(tmp_path, worker=0, workers=4))
    with pytest.raises(SystemExit, match="allocation cannot change on resume"):
        study.command_run(arguments(tmp_path, worker=1, workers=4, model="different-model"))
    assert len(recorded_budget_failure) == 1
    assert not (tmp_path / "shards/_worker_1.lock").exists()


@pytest.mark.parametrize("overrides", [{"seed_count": 30}, {"max_calls": 9000},
                                       {"max_tokens": 20000000}, {"arms": "ungated"}])
def test_registered_run_requires_72_seeds_and_the_global_ceiling(tmp_path, recorded_budget_failure, overrides):
    with pytest.raises(SystemExit, match="registered study requires"):
        study.command_run(arguments(tmp_path, **overrides))
    assert not recorded_budget_failure


def test_run_identity_records_restored_retry_policy(tmp_path, recorded_budget_failure):
    study.command_run(arguments(tmp_path, pilot=True, seed_count=2, max_calls=400))
    run = json.loads((tmp_path / "shards/_allocation.json").read_text())["experiment_identity"]["run"]
    assert run["provider_max_attempts"] == 4
    policy = run["provider_retry_policy"]
    assert policy["retry_statuses"] == [429, 499, 500, 502, 503, 504]
    assert policy["timeouts_retried"] is False and policy["shared_deadline"] is True
    assert policy["backoff_seconds"] == [1, 2, 4] and policy["min_attempt_seconds"] == 1.0


def test_shared_ledger_analysis_requires_72_seeds(tmp_path):
    shards = tmp_path / "shards"
    shards.mkdir()
    for arm in study.ARMS:
        for seed in range(30):
            (shards / f"{arm.replace('@', '_')}__{seed:04d}.json").write_text(
                json.dumps({"arm": arm, "seed": seed}))
    (shards / "_status.json").write_text(json.dumps({"budget_policy": "shared_global_ledger",
                                                     "budget_max_calls": 16000, "budget_max_tokens": 44000000}))
    args = Namespace(shards=str(shards))
    with pytest.raises(ValueError, match="seeds 0..71"):
        study.command_analyze(args)
