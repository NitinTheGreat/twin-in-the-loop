from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Request:
    arrival_time: float
    network_delay: float = 0.0
    work: float = 0.0


@dataclass
class Service:
    id: str
    host_node_id: str
    cpu_demand_per_req: float
    mem_footprint: float
    replicas: int = 1
    queue: list[Request] = field(default_factory=list)
    status: str = "healthy"
    in_service: Optional[Request] = None


def service_rate(allocated_cpu: float, replicas: int, cpu_demand_per_req: float) -> float:
    return (allocated_cpu * replicas) / cpu_demand_per_req


def process_queue(
    service: Service,
    allocated_cpu: float,
    tick_start: float,
    tick_seconds: float,
    queue_cap: int,
    work_generator,
) -> tuple[list[float], int, float]:
    dropped = 0
    if len(service.queue) > queue_cap:
        overflow = len(service.queue) - queue_cap
        del service.queue[queue_cap:]
        dropped += overflow

    capacity = allocated_cpu * max(service.replicas, 1)
    budget = capacity * tick_seconds
    clock = tick_start
    completed: list[float] = []
    work_done = 0.0

    while budget > 0.0:
        if service.in_service is None:
            if service.queue:
                head = service.queue.pop(0)
                head.work = float(work_generator.exponential(service.cpu_demand_per_req))
                service.in_service = head
            else:
                break
        active = service.in_service
        if active.work <= budget:
            elapsed = active.work / capacity
            clock += elapsed
            budget -= active.work
            work_done += active.work
            completed.append((clock - active.arrival_time) + active.network_delay)
            service.in_service = None
        else:
            elapsed = budget / capacity
            clock += elapsed
            active.work -= budget
            work_done += budget
            budget = 0.0

    return completed, dropped, work_done
