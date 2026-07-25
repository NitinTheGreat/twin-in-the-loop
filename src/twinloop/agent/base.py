from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from ..actions.schema import BaseAction
from ..sim.state import PendingEffect, SimState
from ..telemetry.collector import Observation


@dataclass
class TwinFeedback:
    approved: bool
    reason: str
    predicted_metrics: dict = field(default_factory=dict)


@dataclass
class DecisionContext:
    tick: int
    pending: list[PendingEffect] = field(default_factory=list)
    state: Optional[SimState] = None


class Agent(Protocol):
    def decide(
        self, obs: Observation, feedback: Optional[TwinFeedback] = None
    ) -> BaseAction: ...


def context_from_sim(sim) -> DecisionContext:
    return DecisionContext(
        tick=sim.state.tick,
        pending=sim.state.pending,
        state=sim.state,
    )
