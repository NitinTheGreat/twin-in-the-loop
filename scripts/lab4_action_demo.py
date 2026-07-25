from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from twinloop.actions.executor import execute_action
from twinloop.actions.schema import (
    MigrateService,
    NoOp,
    RerouteTraffic,
    RestartService,
    ScaleService,
    ThrottleService,
)
from twinloop.actions.validator import validate_action
from twinloop.config import ActionsConfig, SimConfig, TopologyConfig
from twinloop.faults.schedule import FaultEvent, FaultSchedule
from twinloop.sim.engine import NetworkSim, Topology, build_topology
from twinloop.sim.link import Link
from twinloop.sim.node import Node
from twinloop.sim.service import Service
from twinloop.sim.workload import PoissonWorkload


def _topology(rate_a=42.0, rate_n=42.0):
    gateway = Node("gw0", "gateway", 100.0, 100.0)
    busy = Node("busy", "edge", 100.0, 100.0)
    free = Node("free", "edge", 100.0, 100.0)
    device = Node("dev0", "device", 10.0, 10.0)
    svc_a = Service("svcA", "busy", 1.0, 5.0)
    svc_n = Service("svcN", "busy", 1.0, 5.0)
    links = [
        Link("l_busy", ("gw0", "busy"), 1000.0, 5.0, 5.0),
        Link("l_free", ("gw0", "free"), 1000.0, 5.0, 5.0),
        Link("l_dev", ("gw0", "dev0"), 1000.0, 5.0, 5.0),
    ]
    routes = {"svcA": ["l_dev", "l_busy"], "svcN": ["l_dev", "l_busy"]}
    workloads = {"svcA": PoissonWorkload(rate_a), "svcN": PoissonWorkload(rate_n)}
    return Topology([gateway, busy, free, device], links, [svc_a, svc_n], routes, workloads)


def _summary(sim, sid, ticks):
    metrics = [sim.step() for _ in range(ticks)]
    tail = metrics[-15:]
    p95 = np.mean([m.service_p95[sid] for m in tail]) * 1000
    drop = np.mean([m.service_drop_rate[sid] for m in tail]) * 100
    thr = np.mean([m.service_throughput[sid] for m in tail])
    return p95, drop, thr


def _line(label, p95, drop, thr):
    return f"  {label:<16} p95={p95:7.1f}ms  drop={drop:5.1f}%  thr={thr:5.1f}rps"


def _scenario(title, sid, make_sim, make_action, note, warm=60, after=40):
    config = ActionsConfig()
    sim = make_sim()
    b_p95, b_drop, b_thr = _summary(sim, sid, warm)

    action = make_action(sim)
    verdict = validate_action(action, sim.state, config)
    result = execute_action(sim, action, config) if verdict.valid else None

    a_p95, a_drop, a_thr = _summary(sim, sid, after)

    print("=" * 72)
    print(f"{title}   (watching {sid})")
    print(f"  note: {note}")
    print(_line("BEFORE", b_p95, b_drop, b_thr))
    if result is not None:
        print(f"  ACTION: {result.reason}  (cost={result.cost:.1f})")
    else:
        print(f"  ACTION REJECTED: {verdict.reason}")
    print(_line("+30 TICKS LATER", a_p95, a_drop, a_thr))
    print()


def main() -> None:
    print("Twin-in-the-Loop  Lab 4  action demo")
    print("Each action shows service state before, the action, its cost, and state 30+ ticks later.")
    print("Actions are not free and not always helpful. One below is deliberately harmful.\n")

    def _busy(rate_a=42.0):
        return lambda: NetworkSim(_topology(rate_a=rate_a), SimConfig(), seed=11)

    def _leaky():
        schedule = FaultSchedule(
            [FaultEvent("service_memory_leak", "svc0", 5, 400, 6.0)]
        )
        return NetworkSim(
            build_topology(TopologyConfig(), SimConfig()),
            SimConfig(),
            seed=11,
            schedule=schedule,
        )

    _scenario(
        "no_op on a healthy service",
        "svcA",
        _busy(),
        lambda sim: NoOp(),
        "explicitly doing nothing is a valid, zero-cost choice",
    )

    _scenario(
        "restart_service to clear a memory leak (correct remedy)",
        "svc0",
        _leaky,
        lambda sim: RestartService(service_id="svc0"),
        "a real memory leak is active; restart resets the leaked footprint",
        warm=40,
        after=10,
    )

    _scenario(
        "scale_service up on a contended node (helpful)",
        "svcA",
        _busy(),
        lambda sim: ScaleService(service_id="svcA", delta_replicas=1),
        "svcA shares 'busy' with svcN; adding a replica takes a larger CPU share",
    )

    _scenario(
        "throttle_service under overload (trades availability for latency)",
        "svcA",
        _busy(rate_a=55.0),
        lambda sim: ThrottleService(service_id="svcA", rate_limit=28.0),
        "capping admitted rate lowers p95 but sheds load as drops",
    )

    _scenario(
        "reroute_traffic to an alternate path",
        "svcA",
        _busy(),
        lambda sim: RerouteTraffic(service_id="svcA", path_hint=["l_dev", "l_busy"]),
        "swaps the route for svcA in place",
    )

    _scenario(
        "HARMFUL: migrate_service on a healthy service under load",
        "svcA",
        _busy(),
        lambda sim: MigrateService(service_id="svcA", target_node_id="free"),
        "migration downtime drops traffic for many ticks; here it hurts before it helps",
        after=8,
    )

    print("=" * 72)
    print("Takeaway: migrating svcA caused a burst of dropped requests during downtime.")
    print("An agent that acts without checking consequences can make an episode worse.")
    print("That risk is exactly what the digital twin (later levels) is meant to catch.")


if __name__ == "__main__":
    main()
