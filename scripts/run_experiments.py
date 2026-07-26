from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from twinloop.config import ExperimentConfig
from twinloop.experiment.logging import read_jsonl
from twinloop.experiment.runner import run_sweep
from twinloop.llm.providers import ProviderResponse


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


def _provider_factory(name):
    if name == "scripted":
        return lambda: ScriptedProvider()
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
    parser.add_argument("--provider", default="scripted", choices=["scripted", "local", "cloud"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()

    config = ExperimentConfig()
    if args.episode_ticks is not None:
        config.sim.episode_ticks = args.episode_ticks
    if args.provider in ("local", "cloud"):
        config.llm.provider = args.provider
    if args.model is not None:
        config.llm.model = args.model
    if args.base_url is not None:
        config.llm.base_url = args.base_url

    if args.provider == "cloud" and not os.environ.get(config.llm.api_key_env):
        print(f"warning: --provider cloud but {config.llm.api_key_env} is not set in the environment")
    if args.provider in ("local", "cloud") and "localhost" in config.llm.base_url and args.base_url is None:
        print(f"warning: --provider {args.provider} is targeting {config.llm.base_url}; pass --base-url for a remote endpoint")

    arm_ids = _parse_list(args.arms, str)
    seeds = _parse_list(args.seeds, int)
    fidelity_levels = _parse_list(args.fidelity_levels, float)

    plan = run_sweep(
        config,
        args.output,
        provider_factory=_provider_factory(args.provider),
        arm_ids=arm_ids,
        seeds=seeds,
        fidelity_levels=fidelity_levels,
        dry_run=True,
    )
    print("Twin-in-the-Loop experiment sweep")
    print(f"  output dir        : {args.output}")
    print(f"  provider          : {args.provider}")
    print(f"  planned runs      : {plan.planned_runs}")
    print(f"  estimated LLM calls: {plan.estimated_llm_calls}")
    if args.dry_run:
        print("  dry run: nothing executed.")
        return

    def _progress(run, executed, total, summary):
        print(
            f"  [{executed}/{total}] {run.arm_id} seed={run.seed} fidelity={run.fidelity} "
            f"-> violation_ticks={summary['slo_violation_ticks']} "
            f"proposals={summary['proposals']} harmful={summary['harmful_proposals']} "
            f"blocked={summary['harmful_proposals_blocked']}"
        )

    result = run_sweep(
        config,
        args.output,
        provider_factory=_provider_factory(args.provider),
        arm_ids=arm_ids,
        seeds=seeds,
        fidelity_levels=fidelity_levels,
        resume=not args.no_resume,
        progress=_progress,
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
