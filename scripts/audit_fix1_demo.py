"""PRIVILEGED diagnostic evidence for Priority 1; never an experimental arm."""
import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from twinloop.config import SimConfig, TopologyConfig
from twinloop.diagnostics.enumerator import enumerate_opportunities
from twinloop.faults.schedule import FaultEvent, FaultSchedule
from twinloop.sim.engine import NetworkSim, build_topology


def run(depth=1):
    cases = [
        ("node_cpu_saturation", "edge0", 4.0, 1),
        ("node_crash", "edge0", 1.0, 1),
        ("link_degradation", "l_gw_edge0", 3.0, 1),
        ("link_failure", "l_gw_edge0", 1.0, 1),
        ("service_memory_leak", "svc0", 2.0, 40),
        ("traffic_surge", "svc0", 3.0, 1),
    ]
    reports = []
    for kind, target, magnitude, decision_tick in cases:
        config = SimConfig()
        topology = build_topology(TopologyConfig(), config)
        sim = NetworkSim(topology, config, seed=0, schedule=FaultSchedule([
            FaultEvent(kind, target, 0, 80, magnitude)]))
        for _ in range(decision_tick):
            sim.step()
        report = asdict(enumerate_opportunities(
            sim, horizon=60, max_depth=depth, sequence_interval=10))
        report.update(fault_type=kind, fault_target=target, fault_magnitude=magnitude,
                      fault_start=0, fault_duration=80, decision_tick=decision_tick, seed=0)
        reports.append(report)
        print(json.dumps(report, allow_nan=False), flush=True)
    return reports


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--depth", type=int, choices=(1, 2), default=1,
                        help="Depth 2 exhausts feasible action pairs and is substantially slower.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = run(args.depth)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(reports, indent=2, allow_nan=False) + "\n", encoding="utf-8")
