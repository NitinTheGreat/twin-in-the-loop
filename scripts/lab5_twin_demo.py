from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from twinloop.actions.schema import MigrateService, NoOp
from twinloop.config import SimConfig, TwinConfig
from twinloop.faults.schedule import FaultEvent, FaultSchedule
from twinloop.sim.engine import NetworkSim, Topology
from twinloop.sim.link import Link
from twinloop.sim.node import Node
from twinloop.sim.service import Service
from twinloop.sim.workload import PoissonWorkload
from twinloop.twin.rollout import FAULT_MODE_SCHEDULED, rollout
from twinloop.twin.validator import TwinValidator


def _topology(rate=25.0):
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


def _mean_p95(metrics):
    return [float(np.mean(list(m.service_p95.values()))) * 1000 for m in metrics]


def _sim(schedule, advance=25, seed=3):
    sim = NetworkSim(_topology(), SimConfig(), seed=seed, schedule=schedule)
    for _ in range(advance):
        sim.step()
    return sim


def _scenario(title, schedule, action, fidelity, horizon=30):
    sim = _sim(schedule)
    util = sim.state.nodes["busy"].cpu_used / sim.state.nodes["busy"].cpu_capacity
    validator = TwinValidator(
        TwinConfig(fidelity=fidelity, horizon_ticks=horizon), seed=0, log_path="results/logs/twin_demo.jsonl"
    )
    verdict = validator.validate(sim, action, None)

    real_action = rollout(sim, action, horizon, FAULT_MODE_SCHEDULED)
    real_noop = rollout(sim, NoOp(), horizon, FAULT_MODE_SCHEDULED)

    twin_action = _mean_p95(verdict.action_metrics)
    twin_noop = _mean_p95(verdict.noop_metrics)
    real_a = _mean_p95(real_action.metrics)
    real_n = _mean_p95(real_noop.metrics)

    print("=" * 78)
    print(title)
    print(f"  symptom now: busy-node utilisation {util * 100:.0f}%, twin fidelity {fidelity:.1f}")
    print(f"  proposed action: {action.model_dump()}")
    print("  predicted mean p95 over horizon (ms), twin vs reality, sampled every 6 ticks:")
    print("    tick |  twin+action  twin+noop | real+action  real+noop")
    for k in range(0, horizon, 6):
        print(
            f"    {k:>4} | {twin_action[k]:11.0f}  {twin_noop[k]:9.0f} | "
            f"{real_a[k]:10.0f}  {real_n[k]:9.0f}"
        )
    print(f"  TWIN VERDICT: {'APPROVE' if verdict.approved else 'REJECT'}")
    print(f"    predicted violation-ticks: action {verdict.action_violation_ticks}, no-op {verdict.noop_violation_ticks}")
    print(f"    reason: {verdict.reason}")
    real_helpful = real_action.violation_ticks <= real_noop.violation_ticks
    print(f"  REALITY: action {real_action.violation_ticks} violation-ticks, no-op {real_noop.violation_ticks}"
          f"  -> action was {'helpful' if real_helpful else 'harmful'}")
    correct = verdict.approved == real_helpful
    print(f"  OUTCOME: twin was {'CORRECT' if correct else 'WRONG'}")
    return correct


def main() -> None:
    Path("results/logs").mkdir(parents=True, exist_ok=True)
    print("Twin-in-the-Loop  Lab 5  twin validator demo")
    print("The twin forks the network fault-blind, simulates the action and no-op over a horizon,")
    print("and approves only if the action is not predicted to be worse. It is useful and fallible.\n")

    _scenario(
        "CASE 1  correct approval: a lasting saturation, migration escapes it",
        FaultSchedule([FaultEvent("node_cpu_saturation", "busy", 10, 300, 4.5)]),
        MigrateService(service_id="svcX", target_node_id="spare"),
        fidelity=1.0,
    )
    print()
    _scenario(
        "CASE 2  correct rejection: a healthy service, migration only adds downtime",
        FaultSchedule([]),
        MigrateService(service_id="svcX", target_node_id="spare"),
        fidelity=1.0,
    )
    print()
    _scenario(
        "CASE 3  the twin is WRONG (labelled): saturation about to expire on its own",
        FaultSchedule([FaultEvent("node_cpu_saturation", "busy", 10, 18, 4.5)]),
        MigrateService(service_id="svcX", target_node_id="spare"),
        fidelity=1.0,
    )

    print("\n" + "=" * 78)
    print("Case 3 is the honest limit: the twin rolls forward fault-blind, so it cannot see")
    print("that the saturation was about to lift. It approved a migration whose downtime cost")
    print("more than simply waiting. A perfect twin would be clairvoyant, which is impossible;")
    print("the paper measures exactly how often imperfect twins like this help versus hurt.")


if __name__ == "__main__":
    main()
