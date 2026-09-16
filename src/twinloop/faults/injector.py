from __future__ import annotations

from .catalog import SERVICE_MEMORY_LEAK, compose_faults, fault_fields


class FaultInjector:
    def __init__(self, schedule) -> None:
        self.schedule = schedule

    def apply_tick(self, tick: int, state) -> None:
        # Restore the common baseline before capturing any newly affected fields.
        for baseline in state.fault_baselines.values():
            obj = getattr(state, baseline["collection"])[baseline["target"]]
            setattr(obj, baseline["field"], baseline["value"])
        active_keys = set()
        for index, event in enumerate(self.schedule.events):
            if not event.start_tick <= tick < event.start_tick + event.duration:
                state.active_faults.pop(index, None)
                continue
            collection, fields = fault_fields(event)
            obj = getattr(state, collection)[event.target]
            for field in fields:
                key = f"{collection}:{event.target}:{field}"
                active_keys.add(key)
                state.fault_baselines.setdefault(key, dict(
                    collection=collection, target=event.target, field=field,
                    value=getattr(obj, field)))
            contribution = state.active_faults.setdefault(index, dict(
                type=event.type, target=event.target, magnitude=event.magnitude, bytes=0.0))
            if event.type == SERVICE_MEMORY_LEAK:
                contribution["bytes"] += event.magnitude
        compose_faults(state)
        state.fault_baselines = {key: value for key, value in state.fault_baselines.items()
                                 if key in active_keys}

    def active_events(self, tick: int) -> list:
        return [event for event in self.schedule.events
                if event.start_tick <= tick < event.start_tick + event.duration]
