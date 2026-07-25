from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GroundTruthRecord:
    tick: int
    faults: list = field(default_factory=list)

    @classmethod
    def capture(cls, injector, tick: int) -> "GroundTruthRecord":
        if injector is None:
            return cls(tick=tick, faults=[])
        return cls(tick=tick, faults=list(injector.active_events(tick)))
