from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from twinloop.config import ExperimentConfig
from twinloop.experiment.logging import read_jsonl
from twinloop.experiment.runner import run_sweep
from twinloop.llm.providers import GeminiProvider, ProviderResponse


class ScriptedProvider:
    def complete(self, messages, model, temperature, timeout):
        prompt = "\n".join(m["content"] for m in messages)
        if "TOOL RESULT" not in prompt and "VIOLATIONS:" in prompt:
            victim = _first_violator(prompt)
            reply = (
                '{"thought": "inspect the worst service", "tool": "get_service_history", '
                '"tool_input": {"service_id": "' + victim + '"}}'
            )
        elif "VIOLATIONS:" not in prompt:
            reply = '{"thought": "healthy", "action": {"type": "no_op"}}'
        else:
            victim = _first_violator(prompt)
            reply = (
                '{"thought": "add capacity rather than risk migration downtime", '
                '"action": {"type": "scale_service", "service_id": "' + victim + '", "delta_replicas": 1}}'
            )
        return ProviderResponse(text=reply, tokens_in=max(1, len(prompt) // 4), tokens_out=max(1, len(reply) // 4))


def _first_violator(prompt):
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped.startswith("svc") and "(host" in stripped:
            return stripped.split(" ")[0]
    return "svc0"


def _load_dotenv():
    path = Path(__file__).resolve().parents[1] / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


PROVIDER_PRESETS = {
    "gemini": {
        "provider": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "model": "gemini-3.6-flash",
        "api_key_env": "GEMINI_API_KEY",
    },
    "openai": {
        "provider": "cloud",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
    },
}


def _build_provider_factory(name, config):
    if name == "scripted":
        return lambda: ScriptedProvider()
    if config.llm.provider == "gemini":
        api_key = os.environ.get(config.llm.api_key_env)
        base_url = config.llm.base_url
        return lambda: GeminiProvider(api_key, base_url)
    return None


def _parse_list(value, cast):
    if value is None:
        return None
    return [cast(item) for item in value.split(",") if item != ""]


def main() -> None:
    parser = argparse.ArgumentParser(description="Twin-in-the-Loop experiment sweep")
    parser.add_argument("--output", default="results/sweep")
    parser.add_argument("--arms", default=None)
    parser.add_argument("--seeds", default=None)
    parser.add_argument("--fidelity-levels", default=None)
    parser.add_argument("--episode-ticks", type=int, default=None)
    parser.add_argument(
        "--provider",
        default="scripted",
        choices=["scripted", "local", "cloud", "gemini", "openai"],
    )
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key-env", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()

    _load_dotenv()

    config = ExperimentConfig()
    if args.episode_ticks is not None:
        config.sim.episode_ticks = args.episode_ticks

    preset = PROVIDER_PRESETS.get(args.provider)
    if preset is not None:
        config.llm.provider = preset["provider"]
        config.llm.base_url = preset["base_url"]
        config.llm.model = preset["model"]
        config.llm.api_key_env = preset["api_key_env"]
    elif args.provider in ("local", "cloud"):
        config.llm.provider = args.provider

    if args.model is not None:
        config.llm.model = args.model
    if args.base_url is not None:
        config.llm.base_url = args.base_url
    if args.api_key_env is not None:
        config.llm.api_key_env = args.api_key_env

    uses_llm = args.provider != "scripted"
    needs_key = args.provider in ("gemini", "openai", "cloud")
    if needs_key and not os.environ.get(config.llm.api_key_env):
        print(f"warning: {config.llm.api_key_env} is not set (checked environment and .env)")
    if uses_llm and "localhost" in config.llm.base_url and args.base_url is None:
        print(f"warning: targeting {config.llm.base_url}; a local server must be running or pass --base-url")

    provider_factory = _build_provider_factory(args.provider, config)

    arm_ids = _parse_list(args.arms, str)
    seeds = _parse_list(args.seeds, int)
    fidelity_levels = _parse_list(args.fidelity_levels, float)

    plan = run_sweep(
        config,
        args.output,
        provider_factory=provider_factory,
        arm_ids=arm_ids,
        seeds=seeds,
        fidelity_levels=fidelity_levels,
        dry_run=True,
    )
    print("Twin-in-the-Loop experiment sweep")
    print(f"  output dir        : {args.output}")
    print(f"  provider          : {args.provider}")
    if uses_llm:
        print(f"  model             : {config.llm.model}")
        print(f"  endpoint          : {config.llm.base_url}")
        print(f"  api key env       : {config.llm.api_key_env}")
    print(f"  planned runs      : {plan.planned_runs}")
    print(f"  estimated LLM calls: {plan.estimated_llm_calls}")
    if args.dry_run:
        print("  dry run: nothing executed.")
        return

    print("running (each dot is one decision; LLM arms call the model per decision):", flush=True)

    def _on_start(run, index, total):
        print(
            f"  [{index}/{total}] {run.arm_id} seed={run.seed} fidelity={run.fidelity} ",
            end="",
            flush=True,
        )

    def _on_decision(decision_index):
        print(".", end="", flush=True)

    def _progress(run, executed, total, summary):
        print(
            f" viol={summary['slo_violation_ticks']} "
            f"harmful={summary['harmful_proposals']} "
            f"blocked={summary['harmful_proposals_blocked']} "
            f"calls={summary['llm_calls']}",
            flush=True,
        )

    result = run_sweep(
        config,
        args.output,
        provider_factory=provider_factory,
        arm_ids=arm_ids,
        seeds=seeds,
        fidelity_levels=fidelity_levels,
        resume=not args.no_resume,
        progress=_progress,
        on_start=_on_start,
        decision_progress=_on_decision,
    )
    print(f"done: executed {result.executed} of {result.planned_runs} runs")

    summaries = read_jsonl(Path(args.output) / "summaries.jsonl")
    calls = sum(s.get("llm_calls", 0) for s in summaries)
    tokens_in = sum(s.get("llm_tokens_in", 0) for s in summaries)
    tokens_out = sum(s.get("llm_tokens_out", 0) for s in summaries)
    cost = (tokens_in * 0.15 + tokens_out * 0.60) / 1_000_000
    print("LLM usage across the sweep (from episode summaries):")
    print(f"  total calls    : {calls}")
    print(f"  tokens in / out: {tokens_in} / {tokens_out}")
    print(f"  estimated cost : ${cost:.4f}  (at $0.15 / $0.60 per 1M input/output tokens)")


if __name__ == "__main__":
    main()
