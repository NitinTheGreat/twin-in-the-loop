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


class NodeCpuSaturation:
    def apply(self, event, state):
        node = state.nodes[event.target]
        saved = {"cpu_reserved": node.cpu_reserved, "status": node.status}
        fraction = min(0.98, max(0.0, event.magnitude / 5.0))
        node.cpu_reserved = node.cpu_capacity * fraction
        node.status = "degraded"
        return saved

    def on_tick(self, event, state, saved):
        return None

    def revert(self, event, state, saved):
        node = state.nodes[event.target]
        node.cpu_reserved = saved["cpu_reserved"]
        node.status = saved["status"]


class NodeCrash:
    def apply(self, event, state):
        node = state.nodes[event.target]
        saved = {"status": node.status}
        node.status = "down"
        return saved

    def on_tick(self, event, state, saved):
        return None

    def revert(self, event, state, saved):
        state.nodes[event.target].status = saved["status"]


class LinkDegradation:
    def apply(self, event, state):
        link = state.links[event.target]
        saved = {
            "latency_multiplier": link.latency_multiplier,
            "loss_rate": link.loss_rate,
        }
        link.latency_multiplier = link.latency_multiplier * event.magnitude
        link.loss_rate = min(1.0, link.loss_rate + 0.1 * event.magnitude)
        return saved

    def on_tick(self, event, state, saved):
        return None

    def revert(self, event, state, saved):
        link = state.links[event.target]
        link.latency_multiplier = saved["latency_multiplier"]
        link.loss_rate = saved["loss_rate"]


class LinkFailure:
    def apply(self, event, state):
        link = state.links[event.target]
        saved = {"status": link.status}
        link.status = "down"
        return saved

    def on_tick(self, event, state, saved):
        return None

    def revert(self, event, state, saved):
        state.links[event.target].status = saved["status"]


class ServiceMemoryLeak:
    def apply(self, event, state):
        service = state.services[event.target]
        return {"mem_footprint": service.mem_footprint}

    def on_tick(self, event, state, saved):
        service = state.services[event.target]
        service.mem_footprint = service.mem_footprint + event.magnitude

    def revert(self, event, state, saved):
        state.services[event.target].mem_footprint = saved["mem_footprint"]


class TrafficSurge:
    def apply(self, event, state):
        workload = state.workloads[event.target]
        saved = {"rate": workload.rate}
        workload.rate = workload.rate * event.magnitude
        return saved

    def on_tick(self, event, state, saved):
        return None

    def revert(self, event, state, saved):
        state.workloads[event.target].rate = saved["rate"]


def build_catalog():
    return {
        NODE_CPU_SATURATION: NodeCpuSaturation(),
        NODE_CRASH: NodeCrash(),
        LINK_DEGRADATION: LinkDegradation(),
        LINK_FAILURE: LinkFailure(),
        SERVICE_MEMORY_LEAK: ServiceMemoryLeak(),
        TRAFFIC_SURGE: TrafficSurge(),
    }
