from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class Workload(Protocol):
    def sample(self, generator, tick_seconds: float) -> int: ...

    def mean_rate(self) -> float: ...

    def copy(self) -> "Workload": ...


@dataclass
class PoissonWorkload:
    rate: float

    def sample(self, generator, tick_seconds: float) -> int:
        return int(generator.poisson(self.rate * tick_seconds))

    def mean_rate(self) -> float:
        return self.rate

    def copy(self) -> "PoissonWorkload":
        return PoissonWorkload(rate=self.rate)
