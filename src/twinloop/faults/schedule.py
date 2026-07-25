from __future__ import annotations

from dataclasses import dataclass

from ..config import FaultConfig
from ..seeding import SeedManager
from .catalog import (
    FAULT_TYPES,
    LINK_TARGET,
    NODE_TARGET,
    SERVICE_TARGET,
    TARGET_KIND,
)


@dataclass
class FaultEvent:
    type: str
    target: str
    start_tick: int
    duration: int
    magnitude: float


def targets_from_topology(topology) -> dict[str, list[str]]:
    return {
        NODE_TARGET: [n.id for n in topology.nodes if n.role == "edge"],
        LINK_TARGET: [link.id for link in topology.links],
        SERVICE_TARGET: [s.id for s in topology.services],
    }


class FaultSchedule:
    def __init__(self, events: list[FaultEvent]) -> None:
        self.events = events

    @classmethod
    def generate(
        cls,
        seed: int,
        config: FaultConfig,
        targets: dict[str, list[str]],
    ) -> "FaultSchedule":
        generator = SeedManager(seed).stream("fault_schedule")
        events: list[FaultEvent] = []
        for _ in range(config.faults_per_episode):
            fault_type = FAULT_TYPES[int(generator.integers(0, len(FAULT_TYPES)))]
            pool = targets[TARGET_KIND[fault_type]]
            target = pool[int(generator.integers(0, len(pool)))]
            start_tick = int(
                generator.integers(config.min_start_tick, config.max_start_tick + 1)
            )
            duration = int(
                generator.integers(config.min_duration, config.max_duration + 1)
            )
            magnitude = float(
                generator.uniform(config.min_magnitude, config.max_magnitude)
            )
            events.append(
                FaultEvent(
                    type=fault_type,
                    target=target,
                    start_tick=start_tick,
                    duration=duration,
                    magnitude=magnitude,
                )
            )
        events.sort(key=lambda e: (e.start_tick, e.type, e.target))
        return cls(events)
