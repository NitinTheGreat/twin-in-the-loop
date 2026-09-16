"""Read-only architecture probes. Run from the repository; no LLM/network calls.

This is an investigation artifact, not a replacement functional test suite.
Prints observations from the existing implementation without changing source/config.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from twinloop.actions.executor import execute_action
from twinloop.actions.schema import MigrateService, RerouteTraffic, RestartService, ScaleService
from twinloop.actions.validator import validate_action
from twinloop.agent.base import context_from_sim
from twinloop.agent.llm_agent import LLMAgent
from twinloop.config import ActionsConfig, LLMConfig, SimConfig, TopologyConfig
from twinloop.faults.schedule import FaultEvent, FaultSchedule
from twinloop.sim.engine import NetworkSim, build_topology
from twinloop.telemetry.collector import Collector, summarize_topology
from twinloop.config import SLOConfig
from twinloop.twin.fidelity import fidelity_to_config


def sim(events=(), seed=0):
    cfg = SimConfig()
    top = build_topology(TopologyConfig(), cfg)
    return NetworkSim(top, cfg, seed, FaultSchedule(list(events)))


def run():
    out = {}
    healthy = sim()
    leaking = sim([FaultEvent("service_memory_leak", "svc0", 0, 80, 2.0)])
    hm = [asdict(healthy.step()) for _ in range(60)]
    lm = [asdict(leaking.step()) for _ in range(60)]
    out["memory_leak"] = {
        "ticks_compared": 60,
        "all_emitted_metrics_equal": hm == lm,
        "healthy_memory": healthy.state.services["svc0"].mem_footprint,
        "leaking_memory": leaking.state.services["svc0"].mem_footprint,
        "host_mem_capacity": leaking.state.nodes["edge0"].mem_capacity,
        "leaking_host_memory_used": leaking.state.nodes["edge0"].mem_used,
    }
    execute_action(leaking, RestartService(service_id="svc0"), ActionsConfig())
    after = []
    for _ in range(5):
        leaking.step()
        s = leaking.state.services["svc0"]
        after.append({"tick": leaking.state.tick, "memory": s.mem_footprint, "status": s.status})
    out["restart_during_leak"] = after

    n = sim()
    out["topology"] = {"nodes": len(n.state.nodes), "links": len(n.state.links)}
    original = list(n.state.routes["svc0"])
    execute_action(n, MigrateService(service_id="svc0", target_node_id="edge1"), ActionsConfig())
    for _ in range(10):
        n.step()
    moved = n.state.services["svc0"]
    endpoints = {e for lid in n.state.routes["svc0"] for e in n.state.links[lid].endpoints}
    out["migration_route"] = {
        "new_host": moved.host_node_id,
        "original_route": original,
        "route_after": n.state.routes["svc0"],
        "route_reaches_new_host": moved.host_node_id in endpoints,
    }
    n = sim()
    incomplete = RerouteTraffic(service_id="svc0", path_hint=["l_gw_edge0"])
    out["route_without_client_link"] = asdict(validate_action(incomplete, n.state, ActionsConfig()))

    # Isolated same-target faults: second ends later, but the first restores 'up'
    # during the overlap and the second eventually restores its saved 'down'.
    n = sim([
        FaultEvent("node_crash", "edge0", 0, 10, 1.0),
        FaultEvent("node_crash", "edge0", 5, 10, 1.0),
    ])
    trace = []
    for _ in range(17):
        n.step()
        trace.append({"tick_processed": n.state.tick - 1, "status": n.state.nodes["edge0"].status})
    out["overlapping_crashes"] = trace

    n = sim([FaultEvent("link_failure", "l_gw_edge0", 0, 30, 1.0)])
    m = n.step()
    collector = Collector(summarize_topology(n.topology), SLOConfig())
    obs = collector.observe(m)
    agent = LLMAgent(None)
    agent.set_context(context_from_sim(n))
    out["failed_link_signal"] = {
        "reported_link_latency": m.link_latency["l_gw_edge0"],
        "service_p95": m.service_p95["svc0"],
        "service_drop_rate": m.service_drop_rate["svc0"],
        "tool_output": agent._link_metrics("l_gw_edge0", obs),
    }

    class ToolOnlyClient:
        def complete(self, messages, *, timeout_seconds=None):
            return '{"tool":"get_topology","tool_input":{}}', None

    agent = LLMAgent(ToolOnlyClient(), LLMConfig(react_max_steps=4))
    agent.set_context(context_from_sim(n))
    decision = agent.decide(obs)
    out["tool_budget_exhaustion"] = {"action": decision.model_dump(), "trace": agent.last_trace}

    class FourToolsThenAction:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, *, timeout_seconds=None):
            self.calls += 1
            if self.calls <= 4:
                return '{"tool":"get_topology","tool_input":{}}', None
            return '{"action":{"type":"restart_service","service_id":"svc0"}}', None

    budget_comparison = []
    for budget in (4, 5):
        client = FourToolsThenAction()
        agent = LLMAgent(client, LLMConfig(react_max_steps=budget))
        agent.set_context(context_from_sim(n))
        decision = agent.decide(obs)
        budget_comparison.append({"budget": budget, "calls": client.calls,
                                  "action": decision.type, "exhausted": agent.last_trace["exhausted"]})
    out["four_tools_then_action"] = budget_comparison

    n = sim()
    def cpu_by_service():
        return {sid: n._allocated_cpu(n.state.services[sid]) * n.state.services[sid].replicas
                for sid in ("svc0", "svc4") if sid in n.state.services}
    allocation = {"before": cpu_by_service()}
    execute_action(n, ScaleService(service_id="svc0", delta_replicas=1), ActionsConfig())
    allocation["after_scale"] = cpu_by_service()
    n.state.services["svc4"].status = "down"
    allocation["neighbor_down"] = cpu_by_service()
    del n.state.services["svc4"]
    allocation["alone_before_scale"] = cpu_by_service()
    execute_action(n, ScaleService(service_id="svc0", delta_replicas=1), ActionsConfig())
    allocation["alone_after_scale"] = cpu_by_service()
    out["cpu_allocation"] = allocation

    n = sim()
    n.state.workloads["svc0"].rate = 0.0
    n.state.workloads["svc4"].rate = 0.0
    # Force a backlog of old requests, then crash; current arrivals remain zero.
    from twinloop.sim.service import Request
    n.state.services["svc0"].queue = [Request(arrival_time=-1.0) for _ in range(10)]
    n.state.nodes["edge0"].status = "down"
    m = n.step()
    out["zero_arrival_crash_with_backlog"] = {
        "p95": m.service_p95["svc0"],
        "throughput": m.service_throughput["svc0"],
        "drop_rate": m.service_drop_rate["svc0"],
        "slo_compliant": collector.evaluator.evaluate(m)["svc0"].compliant,
    }
    out["fidelity_mapping"] = {str(f): fidelity_to_config(f).model_dump() for f in (0.0, 0.8, 1.0)}

    # Reproduce the provided report's headline count, then separate its arms.
    path = ROOT / "results/sweep/proposals.jsonl"
    if path.exists():
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        grouped = {}
        for arm in sorted({r["arm"] for r in rows}):
            rr = [r for r in rows if r["arm"] == arm]
            grouped[arm] = {"rows": len(rr), "actions": dict(Counter(r["proposed_action"]["type"] for r in rr))}
        out["saved_sweep_counts"] = {"total_rows": len(rows), "by_arm": grouped}
    return out


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
