from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from twinloop.config import SimConfig, TopologyConfig
from twinloop.faults.schedule import FaultEvent, FaultSchedule
from twinloop.sim.engine import NetworkSim, build_topology


PROBES = [
    ("node_cpu_saturation", "edge0", "util_edge0", "% util", 100.0, "up"),
    ("node_crash", "edge1", "thr_svc1", "req/s", 1.0, "down"),
    ("link_degradation", "l_gw_edge2", "lat_edge2", "ms", 1.0, "up"),
    ("link_failure", "l_gw_edge3", "drop_svc3", "% drop", 100.0, "up"),
    ("service_memory_leak", "svc0", "mem_svc0", "MB", 1.0, "up"),
    ("traffic_surge", "svc2", "thr_svc2", "req/s", 1.0, "up"),
]


def _record(metrics, state):
    return {
        "tick": metrics.tick,
        "util_edge0": metrics.node_utilisation["edge0"],
        "thr_svc1": metrics.service_throughput["svc1"],
        "lat_edge2": metrics.link_latency["l_gw_edge2"],
        "drop_svc3": metrics.service_drop_rate["svc3"],
        "mem_svc0": state.services["svc0"].mem_footprint,
        "thr_svc2": metrics.service_throughput["svc2"],
    }


def _mean(records, key, lo, hi):
    values = [records[t][key] for t in range(lo, hi) if 0 <= t < len(records)]
    return float(np.mean(values)) if values else 0.0


def main() -> None:
    events = [
        FaultEvent("node_cpu_saturation", "edge0", 20, 25, 4.0),
        FaultEvent("node_crash", "edge1", 60, 25, 1.0),
        FaultEvent("link_degradation", "l_gw_edge2", 100, 25, 3.0),
        FaultEvent("link_failure", "l_gw_edge3", 140, 25, 1.0),
        FaultEvent("service_memory_leak", "svc0", 180, 25, 8.0),
        FaultEvent("traffic_surge", "svc2", 220, 25, 3.0),
    ]
    schedule = FaultSchedule(events)
    sim = NetworkSim(build_topology(TopologyConfig(), SimConfig()), SimConfig(), seed=4, schedule=schedule)

    total = 260
    records = [_record(sim.step(), sim.state) for _ in range(total)]

    print("Twin-in-the-Loop  Lab 2  fault injection demo")
    print("scripted schedule of all six fault types, one after another\n")
    print("scheduled faults:")
    for event in events:
        end = event.start_tick + event.duration
        print(f"  tick {event.start_tick:>3}-{end:<3}  {event.type:<20} target={event.target:<10} magnitude={event.magnitude:.1f}")

    print("\ntimeline  (marker column shows the fault active at that tick)")
    header = "tick | active fault          | edge0util  svc1thr  edge2lat  svc3drop  svc0mem  svc2thr"
    print("-" * len(header))
    print(header)
    print("-" * len(header))
    for t in range(0, total, 5):
        rec = records[t]
        active = "-"
        for ftype, target, *_ in PROBES:
            for event in events:
                if event.type == ftype and event.start_tick <= t < event.start_tick + event.duration:
                    active = f"{ftype}"
        line = (
            f"{t:4d} | {active:<21} | "
            f"{rec['util_edge0'] * 100:7.1f}%  "
            f"{rec['thr_svc1']:6.1f}  "
            f"{rec['lat_edge2']:7.1f}  "
            f"{rec['drop_svc3'] * 100:7.1f}%  "
            f"{rec['mem_svc0']:6.1f}  "
            f"{rec['thr_svc2']:6.1f}"
        )
        print(line)

    print("-" * len(header))
    print("\nper-fault effect  (target metric: quiet before -> broken during -> recovered after)")
    for ftype, target, key, unit, scale, direction in PROBES:
        event = next(e for e in events if e.type == ftype)
        s, d = event.start_tick, event.duration
        before = _mean(records, key, s - 6, s - 1) * scale
        during = _mean(records, key, s + 5, s + d) * scale
        after = _mean(records, key, s + d + 3, s + d + 8) * scale
        arrow = "rose" if direction == "up" else "fell"
        print(
            f"  {ftype:<20} ({target:<10}) [{unit:>6}]: "
            f"{before:8.2f}  ->  {during:8.2f} ({arrow})  ->  {after:8.2f}  recovered"
        )


if __name__ == "__main__":
    main()
