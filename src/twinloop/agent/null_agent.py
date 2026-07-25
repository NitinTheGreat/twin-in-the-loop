from __future__ import annotations

from typing import Optional

from ..actions.schema import NoOp
from .base import TwinFeedback


class NullAgent:
    name = "null"

    def set_context(self, context) -> None:
        return None

    def decide(self, obs, feedback: Optional[TwinFeedback] = None) -> NoOp:
        return NoOp()
