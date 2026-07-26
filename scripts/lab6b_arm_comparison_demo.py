from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from twinloop.config import ExperimentConfig, GraphConfig, SimConfig, TwinConfig
from twinloop.experiment.logging import read_jsonl
from twinloop.experiment.runner import run_sweep
from twinloop.llm.providers import ProviderResponse


class ScriptedProvider:
    def complete(self, messages, model, temperature, timeout):
        prompt = "\n".join(m["content"] for m in messages)
        if "VIOLATIONS:" not in prompt:
            reply = '{"thought": "healthy", "action": {"type": "no_op"}}'
        elif "TOOL RESULT" not in prompt:
            reply = '{"thought": "look", "tool": "get_topology", "tool_input": {}}'
        else:
            victim = _first_violator(prompt)
            reply = (
                '{"thought": "add a replica", "action": {"type": "scale_service", '
                '"service_id": "' + victim + '", "delta_replicas": 1}}'
            )
        return ProviderResponse(text=reply, tokens_in=max(1, len(prompt) // 4), tokens_out=max(1, len(reply) // 4))


def _first_violator(prompt):
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped.startswith("svc") and "(host" in stripped:
            return stripped.split(" ")[0]
    return "svc0"


def _aggregate(summaries):
    rows = {}
    for summary in summaries:
        arm = summary["arm"]
        row = rows.setdefault(
            arm,
            {"episodes": 0, "violation_ticks": 0, "proposals": 0, "harmful": 0, "rejections": 0, "blocked": 0},
        )
        row["episodes"] += 1
        row["violation_ticks"] += summary["slo_violation_ticks"]
        row["proposals"] += summary["proposals"]
        row["harmful"] += summary["harmful_proposals"]
        row["rejections"] += summary["rejections"]
        row["blocked"] += summary["harmful_proposals_blocked"]
    return rows


def main() -> None:
    output = Path("results/lab6b_demo")
    config = ExperimentConfig(
        seeds=[0, 1, 2],
        fidelity_levels=[0.4, 1.0],
        sim=SimConfig(episode_ticks=120),
        graph=GraphConfig(decision_interval_ticks=10),
        twin=TwinConfig(horizon_ticks=20),
    )
    config.llm.cache_dir = "results/lab6b_demo/llm_cache"

    print("Twin-in-the-Loop  Lab 6b  arm comparison demo")
    print("A short sweep: 5 arms x 3 seeds (A3 also across 2 fidelities), 120-tick episodes.")
    print("ILLUSTRATIVE ONLY. Three seeds is far too few for real conclusions; numbers are noisy.\n")

    plan = run_sweep(
        config,
        output,
        provider_factory=lambda: ScriptedProvider(),
        seeds=[0, 1, 2],
        fidelity_levels=[0.4, 1.0],
        resume=False,
    )

    summaries = read_jsonl(output / "summaries.jsonl")
    rows = _aggregate(summaries)

    header = f"{'arm':<5} {'episodes':>8} {'viol-ticks':>11} {'proposals':>10} {'harmful(GT)':>12} {'rejections':>11} {'blocked':>8}"
    print(header)
    print("-" * len(header))
    for arm in sorted(rows):
        row = rows[arm]
        print(
            f"{arm:<5} {row['episodes']:>8} {row['violation_ticks']:>11} {row['proposals']:>10} "
            f"{row['harmful']:>12} {row['rejections']:>11} {row['blocked']:>8}"
        )
    print("-" * len(header))
    print("\nColumns: total SLO violation-ticks (lower is better), proposals made,")
    print("proposals that were harmful by counterfactual ground truth, twin rejections,")
    print("and harmful proposals the twin actually blocked (rejected AND truly harmful).")
    print(f"\nexecuted {plan.executed} runs. Reminder: illustrative at 3 seeds, not a result.")


if __name__ == "__main__":
    main()
