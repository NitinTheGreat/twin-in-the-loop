from __future__ import annotations

NODE_CPU_SATURATION = "node_cpu_saturation"
NODE_CRASH = "node_crash"
LINK_DEGRADATION = "link_degradation"
LINK_FAILURE = "link_failure"
SERVICE_MEMORY_LEAK = "service_memory_leak"
TRAFFIC_SURGE = "traffic_surge"

FAULT_TYPES = (
    NODE_CPU_SATURATION,
    NODE_CRASH,
    LINK_DEGRADATION,
    LINK_FAILURE,
    SERVICE_MEMORY_LEAK,
    TRAFFIC_SURGE,
)

NODE_TARGET = "node"
LINK_TARGET = "link"
SERVICE_TARGET = "service"

TARGET_KIND = {
    NODE_CPU_SATURATION: NODE_TARGET,
    NODE_CRASH: NODE_TARGET,
    LINK_DEGRADATION: LINK_TARGET,
    LINK_FAILURE: LINK_TARGET,
    SERVICE_MEMORY_LEAK: SERVICE_TARGET,
    TRAFFIC_SURGE: SERVICE_TARGET,
}


# Composition is computed from one baseline per affected field, never per-event undo.
# CPU reservation and loss add (capped); latency and traffic multipliers multiply.
# Down dominates degraded dominates baseline status. Active leak bytes add.
def fault_fields(event):
    return {
        NODE_CPU_SATURATION: ("nodes", ("cpu_reserved", "status")),
        NODE_CRASH: ("nodes", ("status",)),
        LINK_DEGRADATION: ("links", ("latency_multiplier", "loss_rate")),
        LINK_FAILURE: ("links", ("status",)),
        SERVICE_MEMORY_LEAK: ("services", ("mem_footprint",)),
        TRAFFIC_SURGE: ("workloads", ("rate",)),
    }[event.type]


def reset_leak_memory(state, sid):
    """Restart reclaims all accumulated bytes, but active leaks resume next tick."""
    service = state.services[sid]
    service.mem_footprint = service.baseline_mem
    key = f"services:{sid}:mem_footprint"
    if key in state.fault_baselines:
        state.fault_baselines[key]["value"] = service.baseline_mem
    for contribution in state.active_faults.values():
        if contribution["type"] == SERVICE_MEMORY_LEAK and contribution["target"] == sid:
            contribution["bytes"] = 0.0


def compose_faults(state):
    for baseline in state.fault_baselines.values():
        obj = getattr(state, baseline["collection"])[baseline["target"]]
        setattr(obj, baseline["field"], baseline["value"])
    for contribution in state.active_faults.values():
        kind, target, magnitude = (contribution[k] for k in ("type", "target", "magnitude"))
        if kind == NODE_CPU_SATURATION:
            node = state.nodes[target]
            node.cpu_reserved = min(node.cpu_capacity * .98,
                                    node.cpu_reserved + node.cpu_capacity * min(.98, max(0, magnitude / 5)))
            if node.status != "down":
                node.status = "degraded"
        elif kind == NODE_CRASH:
            state.nodes[target].status = "down"
        elif kind == LINK_DEGRADATION:
            link = state.links[target]
            link.latency_multiplier *= magnitude
            link.loss_rate = min(1.0, link.loss_rate + .1 * magnitude)
        elif kind == LINK_FAILURE:
            state.links[target].status = "down"
        elif kind == SERVICE_MEMORY_LEAK:
            state.services[target].mem_footprint += contribution["bytes"]
        elif kind == TRAFFIC_SURGE:
            state.workloads[target].rate *= magnitude
