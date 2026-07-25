from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from twinloop.config import SimConfig, TopologyConfig
from twinloop.sim.engine import NetworkSim, build_topology


def main() -> None:
    topology_config = TopologyConfig()
    sim_config = SimConfig()
    topology = build_topology(topology_config, sim_config)
    sim = NetworkSim(topology, sim_config, seed=1)

    edge_ids = [f"edge{i}" for i in range(topology_config.n_edge_servers)]
    service_ids = [f"svc{i}" for i in range(topology_config.n_services)]

    header = (
        "tick | "
        + " ".join(f"{e:>6}" for e in edge_ids)
        + " | "
        + " ".join(f"{s:>8}" for s in service_ids)
    )
    print("Twin-in-the-Loop  Lab 1  network demo")
    print(f"topology: {topology_config.n_gateways} gateway, "
          f"{topology_config.n_edge_servers} edge, "
          f"{topology_config.n_devices} devices, "
          f"{topology_config.n_services} services")
    print("edge columns show CPU utilisation; service columns show p95 latency (ms)")
    print("-" * len(header))
    print(header)
    print("-" * len(header))

    history = []
    total_ticks = 120
    for _ in range(total_ticks):
        metrics = sim.step()
        history.append(metrics)
        if metrics.tick % 10 == 0 or metrics.tick == total_ticks - 1:
            util = " ".join(
                f"{metrics.node_utilisation[e] * 100:5.1f}%" for e in edge_ids
            )
            lat = " ".join(
                f"{metrics.service_p95[s] * 1000:8.2f}" for s in service_ids
            )
            print(f"{metrics.tick:4d} | {util} | {lat}")

    print("-" * len(header))
    tail = history[-60:]
    print("summary over final 60 ticks:")
    for e in edge_ids:
        values = np.array([m.node_utilisation[e] for m in tail])
        print(f"  {e}: mean utilisation {values.mean() * 100:5.1f}%  "
              f"(std {values.std() * 100:4.1f}%)")
    for s in service_ids:
        p95 = np.array([m.service_p95[s] for m in tail])
        thr = np.array([m.service_throughput[s] for m in tail])
        drop = np.array([m.service_drop_rate[s] for m in tail])
        print(f"  {s}: p95 {p95.mean() * 1000:7.2f} ms  "
              f"throughput {thr.mean():6.2f} req/s  "
              f"drop {drop.mean() * 100:4.1f}%")


if __name__ == "__main__":
    main()
