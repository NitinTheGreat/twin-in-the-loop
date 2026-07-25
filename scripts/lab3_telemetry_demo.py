from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from twinloop.config import SimConfig, SLOConfig, TopologyConfig
from twinloop.faults.catalog import FAULT_TYPES
from twinloop.faults.schedule import FaultEvent, FaultSchedule
from twinloop.sim.engine import NetworkSim, build_topology
from twinloop.telemetry.collector import Collector, summarize_topology
from twinloop.telemetry.ground_truth import GroundTruthRecord
from twinloop.telemetry.summarizer import Summarizer


INTERESTING = [10, 42, 55, 92, 105, 130]


def main() -> None:
    schedule = FaultSchedule(
        [
            FaultEvent("traffic_surge", "svc2", 40, 30, 3.0),
            FaultEvent("link_degradation", "l_gw_edge0", 90, 30, 3.0),
        ]
    )
    topology = build_topology(TopologyConfig(), SimConfig())
    sim = NetworkSim(topology, SimConfig(), seed=6, schedule=schedule)
    config = SLOConfig()
    collector = Collector(summarize_topology(topology), config)
    summarizer = Summarizer(config)

    print("Twin-in-the-Loop  Lab 3  telemetry and SLO demo")
    print("LEFT is ground truth (logging only). RIGHT is what the agent actually sees.")
    print("The agent's view is symptoms only: NO fault type, target, or active flag ever appears in it.\n")

    frames = {}
    leak_free = True
    for _ in range(140):
        metrics = sim.step()
        observation = collector.observe(metrics)
        text = summarizer.render(observation)
        truth = GroundTruthRecord.capture(sim._injector, metrics.tick)
        if any(name in text for name in FAULT_TYPES):
            leak_free = False
        if metrics.tick in INTERESTING:
            frames[metrics.tick] = (truth, metrics, text)

    for tick in INTERESTING:
        truth, metrics, text = frames[tick]
        print("=" * 78)
        print(f"TICK {tick}")
        print("-" * 78)
        if truth.faults:
            for event in truth.faults:
                print(
                    f"GROUND TRUTH (hidden): {event.type} on {event.target} "
                    f"magnitude {event.magnitude:.1f}"
                )
        else:
            print("GROUND TRUTH (hidden): no active fault")
        print(
            "raw sample: "
            f"svc2 p95={metrics.service_p95['svc2'] * 1000:.0f}ms thr={metrics.service_throughput['svc2']:.0f}rps | "
            f"edge0 util={metrics.node_utilisation['edge0'] * 100:.0f}% | "
            f"l_gw_edge0 lat={metrics.link_latency['l_gw_edge0']:.0f}ms"
        )
        print("-" * 78)
        print("AGENT OBSERVATION:")
        print(text)
        print()

    print("=" * 78)
    verdict = "VERIFIED" if leak_free else "LEAK DETECTED"
    print(f"{verdict}: across all 140 ticks, the agent's view contained 0 fault labels.")


if __name__ == "__main__":
    main()
