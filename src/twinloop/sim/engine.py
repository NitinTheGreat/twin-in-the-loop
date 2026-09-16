from __future__ import annotations

import copy
from dataclasses import dataclass

import numpy as np
from numpy.random import Generator, PCG64

from ..config import SimConfig, TopologyConfig
from ..faults.injector import FaultInjector
from ..seeding import SeedManager
from .link import Link
from .metrics import TickMetrics
from .routing import path_error, route_to
from ..faults.catalog import reset_leak_memory
from .node import Node
from .service import Request, Service, process_queue
from .state import PendingEffect, SimState
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
        baseline_mem=service.baseline_mem,
        rate_limit=service.rate_limit,
        source_node_id=service.source_node_id,
        pending_request_losses=service.pending_request_losses,
        total_request_losses=service.total_request_losses,
    )


def _copy_pending(effect: PendingEffect) -> PendingEffect:
    return PendingEffect(
        kind=effect.kind,
        service_id=effect.service_id,
        remaining=effect.remaining,
        target_node_id=effect.target_node_id,
    )


def _copy_entities(state: SimState) -> SimState:
    return SimState(
        tick=state.tick,
        nodes={nid: Node(**vars(n)) for nid, n in state.nodes.items()},
        links={lid: Link(**vars(l)) for lid, l in state.links.items()},
        services={sid: _copy_service(s) for sid, s in state.services.items()},
        routes={sid: list(path) for sid, path in state.routes.items()},
        workloads={sid: w.copy() for sid, w in state.workloads.items()},
        active_faults=copy.deepcopy(state.active_faults),
        fault_baselines=copy.deepcopy(state.fault_baselines),
        pending=[_copy_pending(e) for e in state.pending],
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

    def __post_init__(self):
        for collection in (self.nodes, self.links, self.services):
            identifiers = [obj.id for obj in collection]
            if len(identifiers) != len(set(identifiers)):
                raise ValueError("duplicate topology identifiers")


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

    if topology_config.redundant_links:
        # Edge mesh supplies host-link detours; dual-homed clients supply access detours.
        pairs = [(a.id, b.id) for i, a in enumerate(edges) for b in edges[i + 1:]]
        pairs += [(device.id, edges[i % len(edges)].id) for i, device in enumerate(devices)]
        for a, b in pairs:
            links.append(Link(f"l_redundant_{a}_{b}", (a, b),
                              sim_config.default_link_bandwidth,
                              sim_config.default_base_latency_ms,
                              sim_config.default_base_latency_ms))

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
        service.source_node_id = source.id
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
    def __init__(
        self,
        topology: Topology,
        config: SimConfig,
        seed: int,
        schedule=None,
    ) -> None:
        self.config = config
        topology.__post_init__()
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
                baseline_mem=s.baseline_mem if s.baseline_mem > 0 else s.mem_footprint,
                source_node_id=s.source_node_id or self._infer_source(topology, s.id),
            )
            for s in topology.services
        }
        routes = {sid: list(path) for sid, path in topology.routes.items()}
        workloads = {sid: w.copy() for sid, w in topology.workloads.items()}
        self.state = SimState(
            tick=0,
            nodes=nodes,
            links=links,
            services=services,
            routes=routes,
            workloads=workloads,
        )

        self._injector = FaultInjector(schedule) if schedule is not None else None

        self._arrival_streams = {
            sid: self.seed_manager.stream(f"workload::{sid}") for sid in services
        }
        self._service_streams = {
            sid: self.seed_manager.stream(f"service::{sid}") for sid in services
        }
        self._routing_stream = self.seed_manager.stream("routing")

    @staticmethod
    def _infer_source(topology, sid):
        route = topology.routes[sid]
        if not route:
            return next(s.host_node_id for s in topology.services if s.id == sid)
        endpoints = next(link.endpoints for link in topology.links if link.id == route[0])
        devices = {n.id for n in topology.nodes if n.role == "device"}
        return next((n for n in endpoints if n in devices), endpoints[0])

    def _allocated_cpu(self, service: Service) -> float:
        node = self.state.nodes[service.host_node_id]
        total_replicas = sum(
            max(s.replicas, 1)
            for s in self.state.services.values()
            if s.host_node_id == service.host_node_id
        )
        available = max(node.cpu_capacity - node.cpu_reserved, 1e-9)
        return available / max(total_replicas, 1)

    def _advance_pending_effects(self) -> None:
        remaining: list[PendingEffect] = []
        for effect in self.state.pending:
            if effect.remaining <= 1:
                service = self.state.services[effect.service_id]
                if effect.kind == "migrate":
                    path = route_to(self.state, service.id, effect.target_node_id)
                    if path is not None and self.state.nodes[effect.target_node_id].status != "down":
                        service.host_node_id = effect.target_node_id
                        self.state.routes[service.id] = path
                    # If connectivity disappeared in transit, retain old placement/route.
                if effect.kind == "restart":
                    reset_leak_memory(self.state, service.id)
                service.status = "healthy"
            else:
                effect.remaining -= 1
                remaining.append(effect)
        self.state.pending = remaining

    def _route_delay_and_loss(self, service_id: str) -> tuple[float, float]:
        delay_ms = 0.0
        survival = 1.0
        service = self.state.services[service_id]
        if path_error(self.state, self.state.routes.get(service_id, []),
                      service.source_node_id, service.host_node_id):
            return 0.0, 1.0
        for link_id in self.state.routes.get(service_id, []):
            link = self.state.links[link_id]
            if link.status != "up":
                return delay_ms / 1000.0, 1.0
            delay_ms += link.current_latency_ms
            survival *= 1.0 - link.loss_rate
        return delay_ms / 1000.0, 1.0 - survival

    def step(self) -> TickMetrics:
        tau = self.config.tick_seconds
        tick_start = self.state.tick * tau

        if self._injector is not None:
            self._injector.apply_tick(self.state.tick, self.state)

        for link in self.state.links.values():
            link.current_latency_ms = link.base_latency_ms * link.latency_multiplier

        outstanding_before = {sid: len(s.queue) + int(s.in_service is not None)
                              + s.pending_request_losses
                              for sid, s in self.state.services.items()}
        memory_used = {nid: sum(s.mem_footprint * max(s.replicas, 1)
                               for s in self.state.services.values() if s.host_node_id == nid)
                       for nid in self.state.nodes}

        arrivals: dict[str, list[Request]] = {}
        arrived_counts: dict[str, int] = {}
        throttle_dropped: dict[str, int] = {}
        for sid, service in self.state.services.items():
            count = self.state.workloads[sid].sample(self._arrival_streams[sid], tau)
            arrived_counts[sid] = count
            admitted = count
            if service.rate_limit is not None:
                cap = int(min(count, service.rate_limit * tau))
                admitted = min(count, cap)
            throttle_dropped[sid] = count - admitted
            arrivals[sid] = [Request(arrival_time=tick_start) for _ in range(admitted)]

        dropped_counts: dict[str, int] = {
            sid: throttle_dropped[sid] + service.pending_request_losses
            for sid, service in self.state.services.items()
        }
        for service in self.state.services.values():
            service.pending_request_losses = 0
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
            node = self.state.nodes[service.host_node_id]
            # Hard memory-capacity pressure: all requests on an over-capacity host fail.
            # Restart reclaims memory; the scheduled leak may grow again.
            if (node.status == "down" or service.status == "down"
                    or memory_used[node.id] > node.mem_capacity):
                dropped_counts[sid] += len(service.queue) + int(service.in_service is not None)
                service.queue.clear()
                service.in_service = None
                completed[sid] = []
                work_done[sid] = 0.0
                continue
            responses, overflow, done = process_queue(
                service,
                self._allocated_cpu(service),
                tick_start,
                tau,
                self.config.queue_cap,
                self._service_streams[sid],
            )
            completed[sid] = responses
            dropped_counts[sid] += overflow
            work_done[sid] = done

        for node in self.state.nodes.values():
            node.cpu_used = node.cpu_reserved
            node.mem_used = 0.0
        for sid, service in self.state.services.items():
            node = self.state.nodes[service.host_node_id]
            node.cpu_used += work_done[sid] / tau
            node.mem_used += service.mem_footprint * max(service.replicas, 1)

        for link in self.state.links.values():
            link.current_latency_ms = link.base_latency_ms * link.latency_multiplier

        metrics = TickMetrics(tick=self.state.tick)
        for sid, service in self.state.services.items():
            responses = completed[sid]
            if responses:
                array = np.array(responses, dtype=float)
                p50, p95 = np.percentile(array, [50, 95])
                metrics.service_p50[sid] = float(p50)
                metrics.service_p95[sid] = float(p95)
            else:
                metrics.service_p50[sid] = None
                metrics.service_p95[sid] = None
            metrics.service_throughput[sid] = len(responses) / tau
            at_risk = outstanding_before[sid] + arrived_counts[sid]
            metrics.service_drop_rate[sid] = dropped_counts[sid] / at_risk if at_risk else 0.0
            metrics.service_arrivals[sid] = arrived_counts[sid]
            metrics.service_dropped[sid] = dropped_counts[sid]
            metrics.service_completed[sid] = len(responses)
            metrics.service_outstanding[sid] = len(service.queue) + int(service.in_service is not None)
            metrics.service_queue_len[sid] = len(service.queue)
        for nid, node in self.state.nodes.items():
            metrics.node_utilisation[nid] = (
                node.cpu_used / node.cpu_capacity if node.cpu_capacity > 0 else 0.0
            )
        self._advance_pending_effects()
        # Placement and memory are end-of-tick state, as exposed to controller tools.
        # Throughput, latency and CPU still describe work during this tick.
        for nid, node in self.state.nodes.items():
            node.mem_used = sum(s.mem_footprint * max(s.replicas, 1)
                                for s in self.state.services.values() if s.host_node_id == nid)
            metrics.node_memory_utilisation[nid] = node.mem_used / node.mem_capacity if node.mem_capacity else 0.0
        metrics.service_hosts = {sid: s.host_node_id for sid, s in self.state.services.items()}
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
        child._injector = self._injector
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
