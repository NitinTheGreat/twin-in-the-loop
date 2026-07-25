from __future__ import annotations

import copy
from dataclasses import dataclass

import numpy as np
from numpy.random import Generator, PCG64

from ..config import SimConfig, TopologyConfig
from ..seeding import SeedManager
from .link import Link
from .metrics import TickMetrics
from .node import Node
from .service import Request, Service, process_queue
from .state import SimState
from .workload import PoissonWorkload, Workload


def _copy_request(request: Request) -> Request:
    return Request(
        arrival_time=request.arrival_time,
        network_delay=request.network_delay,
        work=request.work,
    )


def _copy_service(service: Service) -> Service:
    return Service(
        id=service.id,
        host_node_id=service.host_node_id,
        cpu_demand_per_req=service.cpu_demand_per_req,
        mem_footprint=service.mem_footprint,
        replicas=service.replicas,
        queue=[_copy_request(r) for r in service.queue],
        status=service.status,
        in_service=_copy_request(service.in_service)
        if service.in_service is not None
        else None,
    )


def _copy_entities(state: SimState) -> SimState:
    return SimState(
        tick=state.tick,
        nodes={nid: Node(**vars(n)) for nid, n in state.nodes.items()},
        links={lid: Link(**vars(l)) for lid, l in state.links.items()},
        services={sid: _copy_service(s) for sid, s in state.services.items()},
    )


def _clone_generator(generator: Generator) -> Generator:
    clone = Generator(PCG64())
    clone.bit_generator.state = copy.deepcopy(generator.bit_generator.state)
    return clone


@dataclass
class Topology:
    nodes: list[Node]
    links: list[Link]
    services: list[Service]
    routes: dict[str, list[str]]
    workloads: dict[str, Workload]


def build_topology(
    topology_config: TopologyConfig,
    sim_config: SimConfig,
    arrival_rate: float = 25.0,
    cpu_demand_per_req: float = 1.0,
    mem_footprint: float = 5.0,
) -> Topology:
    nodes: list[Node] = []
    links: list[Link] = []
    services: list[Service] = []
    routes: dict[str, list[str]] = {}
    workloads: dict[str, Workload] = {}

    gateway = Node(
        id="gw0",
        role="gateway",
        cpu_capacity=topology_config.edge_cpu_capacity,
        mem_capacity=topology_config.edge_mem_capacity,
    )
    nodes.append(gateway)

    edges = []
    for i in range(topology_config.n_edge_servers):
        node = Node(
            id=f"edge{i}",
            role="edge",
            cpu_capacity=topology_config.edge_cpu_capacity,
            mem_capacity=topology_config.edge_mem_capacity,
        )
        nodes.append(node)
        edges.append(node)

    devices = []
    for j in range(topology_config.n_devices):
        node = Node(
            id=f"dev{j}",
            role="device",
            cpu_capacity=topology_config.device_cpu_capacity,
            mem_capacity=topology_config.device_mem_capacity,
        )
        nodes.append(node)
        devices.append(node)

    for i, edge in enumerate(edges):
        links.append(
            Link(
                id=f"l_gw_{edge.id}",
                endpoints=("gw0", edge.id),
                bandwidth=sim_config.default_link_bandwidth,
                base_latency_ms=sim_config.default_base_latency_ms,
                current_latency_ms=sim_config.default_base_latency_ms,
            )
        )
    for j, device in enumerate(devices):
        links.append(
            Link(
                id=f"l_gw_{device.id}",
                endpoints=("gw0", device.id),
                bandwidth=sim_config.default_link_bandwidth,
                base_latency_ms=sim_config.default_base_latency_ms,
                current_latency_ms=sim_config.default_base_latency_ms,
            )
        )

    for s in range(topology_config.n_services):
        host = edges[s % len(edges)]
        service = Service(
            id=f"svc{s}",
            host_node_id=host.id,
            cpu_demand_per_req=cpu_demand_per_req,
            mem_footprint=mem_footprint,
        )
        services.append(service)
        source = devices[s % len(devices)]
        routes[service.id] = [f"l_gw_{source.id}", f"l_gw_{host.id}"]
        workloads[service.id] = PoissonWorkload(rate=arrival_rate)

    return Topology(
        nodes=nodes,
        links=links,
        services=services,
        routes=routes,
        workloads=workloads,
    )


class NetworkSim:
    def __init__(self, topology: Topology, config: SimConfig, seed: int) -> None:
        self.config = config
        self.topology = topology
        self.seed_manager = SeedManager(seed)

        nodes = {n.id: Node(**vars(n)) for n in topology.nodes}
        links = {l.id: Link(**vars(l)) for l in topology.links}
        services = {
            s.id: Service(
                id=s.id,
                host_node_id=s.host_node_id,
                cpu_demand_per_req=s.cpu_demand_per_req,
                mem_footprint=s.mem_footprint,
                replicas=s.replicas,
            )
            for s in topology.services
        }
        self.state = SimState(tick=0, nodes=nodes, links=links, services=services)

        self._routes = topology.routes
        self._workloads = topology.workloads

        counts: dict[str, int] = {}
        for service in services.values():
            counts[service.host_node_id] = counts.get(service.host_node_id, 0) + 1
        self._allocated = {
            service.id: nodes[service.host_node_id].cpu_capacity
            / counts[service.host_node_id]
            for service in services.values()
        }

        self._arrival_streams = {
            sid: self.seed_manager.stream(f"workload::{sid}") for sid in services
        }
        self._service_streams = {
            sid: self.seed_manager.stream(f"service::{sid}") for sid in services
        }
        self._routing_stream = self.seed_manager.stream("routing")

    def _route_delay_and_loss(self, service_id: str) -> tuple[float, float]:
        delay_ms = 0.0
        survival = 1.0
        for link_id in self._routes.get(service_id, []):
            link = self.state.links[link_id]
            delay_ms += link.current_latency_ms
            survival *= 1.0 - link.loss_rate
        return delay_ms / 1000.0, 1.0 - survival

    def step(self) -> TickMetrics:
        tau = self.config.tick_seconds
        tick_start = self.state.tick * tau

        arrivals: dict[str, list[Request]] = {}
        arrived_counts: dict[str, int] = {}
        for sid, service in self.state.services.items():
            count = self._workloads[sid].sample(self._arrival_streams[sid], tau)
            arrived_counts[sid] = count
            arrivals[sid] = [Request(arrival_time=tick_start) for _ in range(count)]

        dropped_counts: dict[str, int] = {sid: 0 for sid in self.state.services}
        for sid, service in self.state.services.items():
            delay, loss = self._route_delay_and_loss(sid)
            for request in arrivals[sid]:
                if loss > 0.0 and self._routing_stream.random() < loss:
                    dropped_counts[sid] += 1
                    continue
                request.network_delay = delay
                service.queue.append(request)

        completed: dict[str, list[float]] = {}
        work_done: dict[str, float] = {}
        for sid, service in self.state.services.items():
            responses, overflow, done = process_queue(
                service,
                self._allocated[sid],
                tick_start,
                tau,
                self.config.queue_cap,
                self._service_streams[sid],
            )
            completed[sid] = responses
            dropped_counts[sid] += overflow
            work_done[sid] = done

        for node in self.state.nodes.values():
            node.cpu_used = 0.0
            node.mem_used = 0.0
        for sid, service in self.state.services.items():
            node = self.state.nodes[service.host_node_id]
            node.cpu_used += work_done[sid] / tau
            node.mem_used += service.mem_footprint * max(service.replicas, 1)

        for link in self.state.links.values():
            link.current_latency_ms = link.base_latency_ms

        metrics = TickMetrics(tick=self.state.tick)
        for sid, service in self.state.services.items():
            responses = completed[sid]
            if responses:
                array = np.array(responses, dtype=float)
                metrics.service_p50[sid] = float(np.percentile(array, 50))
                metrics.service_p95[sid] = float(np.percentile(array, 95))
            else:
                metrics.service_p50[sid] = 0.0
                metrics.service_p95[sid] = 0.0
            metrics.service_throughput[sid] = len(responses) / tau
            arrived = arrived_counts[sid]
            metrics.service_drop_rate[sid] = (
                dropped_counts[sid] / arrived if arrived > 0 else 0.0
            )
            metrics.service_queue_len[sid] = len(service.queue)
        for nid, node in self.state.nodes.items():
            metrics.node_utilisation[nid] = (
                node.cpu_used / node.cpu_capacity if node.cpu_capacity > 0 else 0.0
            )
        for lid, link in self.state.links.items():
            metrics.link_latency[lid] = link.current_latency_ms

        self.state.tick += 1
        return metrics

    def _iter_streams(self):
        for sid, generator in self._arrival_streams.items():
            yield f"arrival::{sid}", generator
        for sid, generator in self._service_streams.items():
            yield f"service::{sid}", generator
        yield "routing", self._routing_stream

    def _capture_rng(self) -> dict[str, dict]:
        return {
            key: copy.deepcopy(generator.bit_generator.state)
            for key, generator in self._iter_streams()
        }

    def _apply_rng(self, states: dict[str, dict]) -> None:
        for key, generator in self._iter_streams():
            generator.bit_generator.state = copy.deepcopy(states[key])

    def snapshot(self) -> SimState:
        captured = _copy_entities(self.state)
        captured.rng_states = self._capture_rng()
        return captured

    def restore(self, state: SimState) -> None:
        self.state = _copy_entities(state)
        self._apply_rng(state.rng_states)

    def fork(self) -> "NetworkSim":
        label = f"fork::tick={self.state.tick}"
        child = NetworkSim.__new__(NetworkSim)
        child.config = self.config
        child.topology = self.topology
        child.seed_manager = self.seed_manager.child(label)
        child.state = _copy_entities(self.state)
        child._routes = self.topology.routes
        child._workloads = self.topology.workloads
        child._allocated = dict(self._allocated)
        child._arrival_streams = {
            sid: _clone_generator(generator)
            for sid, generator in self._arrival_streams.items()
        }
        child._service_streams = {
            sid: _clone_generator(generator)
            for sid, generator in self._service_streams.items()
        }
        child._routing_stream = _clone_generator(self._routing_stream)
        return child
