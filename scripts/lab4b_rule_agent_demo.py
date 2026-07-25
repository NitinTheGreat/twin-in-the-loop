from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from twinloop.actions.executor import execute_action
from twinloop.actions.schema import NoOp
from twinloop.config import ActionsConfig, SimConfig, SLOConfig, TopologyConfig
from twinloop.agent.base import context_from_sim
from twinloop.agent.null_agent import NullAgent
from twinloop.agent.rule_agent import RuleAgent
from twinloop.faults.schedule import FaultEvent, FaultSchedule
from twinloop.sim.engine import NetworkSim, Topology, build_topology
from twinloop.sim.link import Link
from twinloop.sim.node import Node
from twinloop.sim.service import Service
from twinloop.sim.workload import PoissonWorkload


def _drive(sim, agent, ticks):
    from twinloop.telemetry.collector import Collector, summarize_topology

    collector = Collector(summarize_topology(sim.topology), SLOConfig())
    actions_config = ActionsConfig()
    observations = []
    events = []
    for _ in range(ticks):
        metrics = sim.step()
        obs = collector.observe(metrics)
        observations.append(obs)
        agent.set_context(context_from_sim(sim))
        action = agent.decide(obs)
        if not isinstance(action, NoOp):
            result = execute_action(sim, action, actions_config)
            events.append((obs.tick, action, agent.last_reason, result))
    return observations, events


def _window(observations, sid, lo, hi):
    picks = [
        observations[t].slo_status[sid]
        for t in range(lo, hi)
        if 0 <= t < len(observations)
    ]
    p95 = float(np.mean([s.p95_ms for s in picks])) if picks else 0.0
    viol = sum(1 for s in picks if not s.compliant)
    return p95, viol


def _spare_topology(rate=25.0):
    gateway = Node("gw0", "gateway", 100.0, 100.0)
    busy = Node("busy", "edge", 100.0, 100.0)
    spare = Node("spare", "edge", 100.0, 100.0)
    device = Node("dev0", "device", 10.0, 10.0)
    svc = Service("svcX", "busy", 1.0, 5.0)
    links = [
        Link("l_busy", ("gw0", "busy"), 1000.0, 5.0, 5.0),
        Link("l_spare", ("gw0", "spare"), 1000.0, 5.0, 5.0),
        Link("l_dev", ("gw0", "dev0"), 1000.0, 5.0, 5.0),
    ]
    routes = {"svcX": ["l_dev", "l_busy"]}
    workloads = {"svcX": PoissonWorkload(rate)}
    return Topology([gateway, busy, spare, device], links, [svc], routes, workloads)


def _main_episode():
    schedule = FaultSchedule(
        [FaultEvent("node_cpu_saturation", "edge2", 25, 90, 4.5)]
    )
    sim = NetworkSim(
        build_topology(TopologyConfig(), SimConfig()), SimConfig(), seed=7, schedule=schedule
    )
    observations, events = _drive(sim, RuleAgent(), 130)

    print("PART 1  a fault the rule agent handles well")
    print("node_cpu_saturation hits edge2 (host of svc2) at tick 25 for 90 ticks.\n")
    print(f"decisions that acted: {len(events)}")
    for tick, action, reason, result in events:
        sid = getattr(action, "service_id", "?")
        b_p95, b_viol = _window(observations, sid, tick - 8, tick)
        a_p95, a_viol = _window(observations, sid, tick + 12, tick + 25)
        print("-" * 74)
        print(f"  tick {tick:>3}: {result.reason} (cost {result.cost:.0f})")
        print(f"           trigger: {reason}")
        print(f"           {sid}: p95 {b_p95:.0f}ms/{b_viol} viol  ->  {a_p95:.0f}ms/{a_viol} viol")
    print()


def _harmful_case():
    schedule = FaultSchedule([FaultEvent("node_cpu_saturation", "busy", 20, 6, 4.5)])

    rule_obs, rule_events = _drive(
        NetworkSim(_spare_topology(), SimConfig(), seed=3, schedule=schedule),
        RuleAgent(),
        90,
    )
    null_obs, _ = _drive(
        NetworkSim(_spare_topology(), SimConfig(), seed=3, schedule=schedule),
        NullAgent(),
        90,
    )

    rule_viol = sum(1 for o in rule_obs if not o.slo_status["svcX"].compliant)
    null_viol = sum(1 for o in null_obs if not o.slo_status["svcX"].compliant)

    print("=" * 74)
    print("PART 2  a case where the rule agent's action does NOT help (labelled)")
    print("A SHORT node_cpu_saturation hits busy at tick 20 for only 6 ticks.")
    print("It would self-resolve. But the agent sees a sustained violation and migrates")
    print("svcX; the migration downtime drops traffic for ~10 ticks, outlasting the fault.\n")
    for tick, action, reason, result in rule_events:
        print(f"  rule agent acted at tick {tick}: {result.reason} (cost {result.cost:.0f})")
    print()
    print(f"  svcX violation-ticks WITH the rule agent : {rule_viol}")
    print(f"  svcX violation-ticks doing NOTHING (null): {null_viol}")
    verdict = "WORSE" if rule_viol > null_viol else "not better"
    print(f"  --> the rule agent made svcX {verdict} on this transient fault.")
    print()
    print("This is not a bug. Thresholds are linear tests on a nonlinear system and")
    print("migration cost is real. A digital twin that simulates the action's consequences")
    print("before applying it is exactly what would catch this. That is the project.")


def main() -> None:
    print("Twin-in-the-Loop  Lab 4b  rule agent demo\n")
    _main_episode()
    _harmful_case()


if __name__ == "__main__":
    main()
