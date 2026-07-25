from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class Workload(Protocol):
    def sample(self, generator, tick_seconds: float) -> int: ...

    def mean_rate(self) -> float: ...


@dataclass
class PoissonWorkload:
    rate: float

    def sample(self, generator, tick_seconds: float) -> int:
        return int(generator.poisson(self.rate * tick_seconds))

    def mean_rate(self) -> float:
        return self.rate
