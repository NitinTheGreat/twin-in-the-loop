from __future__ import annotations

from .catalog import build_catalog


class FaultInjector:
    def __init__(self, schedule) -> None:
        self.schedule = schedule
        self._catalog = build_catalog()

    def apply_tick(self, tick: int, state) -> None:
        for index, event in enumerate(self.schedule.events):
            fault = self._catalog[event.type]
            end_tick = event.start_tick + event.duration
            if tick == event.start_tick:
                state.active_faults[index] = fault.apply(event, state)
            if event.start_tick <= tick < end_tick:
                fault.on_tick(event, state, state.active_faults[index])
            if tick == end_tick and index in state.active_faults:
                fault.revert(event, state, state.active_faults.pop(index))

    def active_events(self, tick: int) -> list:
        return [
            event
            for event in self.schedule.events
            if event.start_tick <= tick < event.start_tick + event.duration
        ]
