from time import perf_counter

from twinloop.config import SimConfig, TopologyConfig
from twinloop.sim.engine import NetworkSim, Topology, build_topology
from twinloop.sim.node import Node
from twinloop.sim.service import Request, Service
from twinloop.sim.workload import PoissonWorkload


def _run(sim, ticks):
    return [sim.step() for _ in range(ticks)]


def _fresh(seed=11):
    topology = build_topology(TopologyConfig(), SimConfig())
    return NetworkSim(topology, SimConfig(), seed=seed)


def test_fork_continuation_identical():
    sim = _fresh()
    _run(sim, 50)
    child = sim.fork()
    parent_seq = _run(sim, 100)
    child_seq = _run(child, 100)
    assert parent_seq == child_seq


def test_parent_unchanged_after_child_mutation():
    sim = _fresh()
    _run(sim, 50)
    pre = sim.snapshot()

    child = sim.fork()
    del child.state.services["svc0"]
    child.state.nodes["edge0"].status = "down"
    child.state.nodes["edge0"].cpu_used = 999.0
    for service in child.state.services.values():
        service.queue.clear()
    _run(child, 50)

    after = sim.snapshot()
    assert after == pre


def test_two_forks_at_same_tick_identical():
    sim = _fresh()
    _run(sim, 50)
    first = sim.fork()
    second = sim.fork()
    assert _run(first, 100) == _run(second, 100)


def test_snapshot_restore_reproducible():
    sim = _fresh()
    _run(sim, 30)
    snap = sim.snapshot()
    first = _run(sim, 100)
    sim.restore(snap)
    second = _run(sim, 100)
    assert first == second


def test_fork_rng_independence():
    control = _fresh()
    _run(control, 50)
    control_seq = _run(control, 100)

    sim = _fresh()
    _run(sim, 50)
    child = sim.fork()
    for _, generator in child._iter_streams():
        generator.standard_normal(10000)

    parent_seq = _run(sim, 100)
    assert parent_seq == control_seq


def test_fork_queue_contents_distinct_instances():
    node = Node(id="n0", role="edge", cpu_capacity=1.0, mem_capacity=100.0)
    service = Service(
        id="svc0", host_node_id="n0", cpu_demand_per_req=1.0, mem_footprint=1.0
    )
    topology = Topology(
        nodes=[node],
        links=[],
        services=[service],
        routes={"svc0": []},
        workloads={"svc0": PoissonWorkload(rate=0.95)},
    )
    sim = NetworkSim(topology, SimConfig(tick_seconds=1.0, queue_cap=10**9), seed=5)
    _run(sim, 400)

    parent_service = sim.state.services["svc0"]
    assert len(parent_service.queue) > 0
    assert parent_service.in_service is not None

    child = sim.fork()
    child_service = child.state.services["svc0"]

    assert child_service.queue[0] is not parent_service.queue[0]
    assert child_service.queue[0].arrival_time == parent_service.queue[0].arrival_time
    assert child_service.in_service is not parent_service.in_service
    assert all(
        c is not p for c, p in zip(child_service.queue, parent_service.queue)
    )


def test_fork_benchmark():
    sim = _fresh()
    _run(sim, 50)
    start = perf_counter()
    sim.fork()
    elapsed = perf_counter() - start
    print(f"\nfork wall-clock on small topology: {elapsed * 1000:.3f} ms")
