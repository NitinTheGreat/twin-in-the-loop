import numpy as np

from twinloop.config import SimConfig, TopologyConfig
from twinloop.sim.engine import NetworkSim, Topology, build_topology
from twinloop.sim.node import Node
from twinloop.sim.service import Service
from twinloop.sim.workload import PoissonWorkload


def _run(sim, ticks):
    return [sim.step() for _ in range(ticks)]


def test_steady_load_stabilises():
    topology = build_topology(TopologyConfig(), SimConfig())
    sim = NetworkSim(topology, SimConfig(), seed=7)
    metrics = _run(sim, 300)

    tail = metrics[-100:]
    utilisation = np.array([m.node_utilisation["edge0"] for m in tail])
    p95 = np.array(
        [float(np.mean(list(m.service_p95.values()))) for m in tail]
    )

    assert 0.0 < utilisation.mean() < 1.0
    assert utilisation.std() < 0.1
    assert p95.mean() > 0.0
    assert p95.std() / p95.mean() < 0.5


def test_same_seed_identical_sequences():
    topology = build_topology(TopologyConfig(), SimConfig())
    first = _run(NetworkSim(topology, SimConfig(), seed=42), 120)
    second = _run(NetworkSim(topology, SimConfig(), seed=42), 120)
    assert first == second


def test_mm1_queue_length():
    capacity = 1.0
    demand = 1.0
    arrival = 0.6
    mu = capacity / demand
    rho = arrival / mu
    expected = rho**2 / (1.0 - rho)

    node = Node(id="n0", role="edge", cpu_capacity=capacity, mem_capacity=100.0)
    service = Service(
        id="svc0", host_node_id="n0", cpu_demand_per_req=demand, mem_footprint=1.0
    )
    topology = Topology(
        nodes=[node],
        links=[],
        services=[service],
        routes={"svc0": []},
        workloads={"svc0": PoissonWorkload(rate=arrival)},
    )
    config = SimConfig(tick_seconds=0.05, queue_cap=10**9)
    sim = NetworkSim(topology, config, seed=2024)

    warmup = 40000
    measure = 360000
    _run(sim, warmup)
    samples = np.array([sim.step().service_queue_len["svc0"] for _ in range(measure)])

    measured = samples.mean()
    assert abs(measured - expected) / expected < 0.15
