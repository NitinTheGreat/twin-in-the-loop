from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from twinloop.actions.executor import execute_action
from twinloop.actions.schema import NoOp
from twinloop.agent.base import context_from_sim
from twinloop.agent.llm_agent import LLMAgent
from twinloop.config import ActionsConfig, LLMConfig, SimConfig, SLOConfig, TopologyConfig
from twinloop.faults.schedule import FaultEvent, FaultSchedule
from twinloop.llm.budget import BudgetGuard
from twinloop.llm.cache import ResponseCache
from twinloop.llm.client import LLMClient
from twinloop.llm.providers import ProviderResponse
from twinloop.sim.engine import NetworkSim, build_topology
from twinloop.telemetry.collector import Collector, summarize_topology


def _first_violator(prompt):
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped.startswith("svc") and "(host" in stripped:
            return stripped.split(" ")[0]
    return "svc0"


class ScriptedDiagnostician:
    def __init__(self):
        self.calls = 0
        self.tokens_in = 0
        self.tokens_out = 0

    def complete(self, messages, model, temperature, timeout):
        prompt = "\n".join(m["content"] for m in messages)
        victim = _first_violator(prompt)
        if "VIOLATIONS:" not in prompt:
            reply = '{"thought": "All services are within SLO; do nothing.", "action": {"type": "no_op"}}'
        elif "TOOL RESULT" not in prompt:
            reply = (
                '{"thought": "A service is breaching SLO. Inspect its recent history before acting.",'
                ' "tool": "get_service_history", "tool_input": {"service_id": "' + victim + '"}}'
            )
        else:
            reply = (
                '{"thought": "Latency is elevated with availability intact and spare capacity, so I '
                'add a replica rather than risk migration downtime.", "action": {"type": '
                '"scale_service", "service_id": "' + victim + '", "delta_replicas": 1}}'
            )
        tin = max(1, len(prompt) // 4)
        tout = max(1, len(reply) // 4)
        self.calls += 1
        self.tokens_in += tin
        self.tokens_out += tout
        return ProviderResponse(text=reply, tokens_in=tin, tokens_out=tout)


def _violation_count(obs):
    return sum(1 for s in obs.slo_status.values() if not s.compliant)


def main() -> None:
    schedule = FaultSchedule(
        [
            FaultEvent("node_cpu_saturation", "edge0", 25, 40, 4.0),
            FaultEvent("traffic_surge", "svc2", 90, 35, 3.0),
        ]
    )
    config = LLMConfig(
        model="scripted-demo",
        cache_dir="results/llm_cache_demo",
        log_path="results/logs/llm_demo.jsonl",
    )
    cache_file = Path("results/llm_cache_demo/cache.json")
    if cache_file.exists():
        cache_file.unlink()
    provider = ScriptedDiagnostician()
    client = LLMClient(
        provider,
        config,
        cache=ResponseCache(cache_file),
        budget=BudgetGuard(10000, 10**9),
        log_path="results/logs/llm_demo.jsonl",
    )
    sim = NetworkSim(
        build_topology(TopologyConfig(), SimConfig()), SimConfig(), seed=8, schedule=schedule
    )
    collector = Collector(summarize_topology(sim.topology), SLOConfig())
    agent = LLMAgent(client, config)
    actions_config = ActionsConfig()

    print("Twin-in-the-Loop  Lab 3b  LLM diagnosis demo")
    print("Runs offline with a scripted, deterministic diagnostician (no API key, no network).")
    print("The agent sees only symptoms, may call read-only tools, then proposes one action.\n")

    observations = []
    for _ in range(140):
        obs = collector.observe(sim.step())
        observations.append(obs)
        agent.set_context(context_from_sim(sim))
        action = agent.decide(obs)
        trace = agent.last_trace
        if not isinstance(action, NoOp):
            before = _violation_count(obs)
            result = execute_action(sim, action, actions_config)
            print("=" * 78)
            print(f"TICK {obs.tick}   SLO before: {before} services in violation")
            print("  what the agent saw (top of its observation):")
            for line in agent.summarizer.render(obs).splitlines()[:3]:
                print(f"    | {line}")
            print(f"  tools called : {trace['tools_called'] or 'none'}")
            if trace["reasoning"]:
                print(f"  reasoning    : {trace['reasoning'][-1]}")
            print(f"  action taken : {result.reason}  (cost {result.cost:.0f})")

    tail = observations[-20:]
    tail_viol = sum(1 for o in tail for s in o.slo_status.values() if not s.compliant)
    tokens_in = sum(r.tokens_in for r in client.records)
    tokens_out = sum(r.tokens_out for r in client.records)
    cache_hits = sum(1 for r in client.records if r.cache_hit)
    est_cost = (tokens_in * 0.15 + tokens_out * 0.60) / 1_000_000
    print("=" * 78)
    print(f"SLO violation-ticks over the final 20 ticks (summed over services): {tail_viol}")
    print("\nLLM usage this episode (from the client's own JSONL records):")
    print(f"  total calls    : {len(client.records)}  (provider hits {provider.calls}, cache hits {cache_hits})")
    print(f"  tokens in / out: {tokens_in} / {tokens_out}")
    print(f"  estimated cost : ${est_cost:.6f}  (at $0.15 / $0.60 per 1M input/output tokens)")


if __name__ == "__main__":
    main()
