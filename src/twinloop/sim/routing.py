"""Ordered client-to-host paths and physical rerouting feasibility."""
from collections import deque


def path_error(state, path, source, host):
    if source not in state.nodes or host not in state.nodes:
        return "unknown source or host"
    if state.nodes[source].status == "down":
        return "path starts at a down node"
    current = source
    visited = {source}
    if not path:
        return "" if source == host else "empty path is not connected"
    for lid in path:
        if lid not in state.links:
            return f"unknown link {lid} in path"
        link = state.links[lid]
        if link.status != "up":
            return f"path traverses down link {lid}"
        if current not in link.endpoints:
            return f"path is not ordered from client {source} to host {host}"
        current = link.endpoints[1] if current == link.endpoints[0] else link.endpoints[0]
        if current not in state.nodes or state.nodes[current].status == "down":
            return "path traverses an unknown or down node"
        if current in visited:
            return "path contains a cycle"
        visited.add(current)
    return "" if current == host else "path does not end at the service host"


def paths(state, source, host, *, operational=True, limit=None):
    """Yield simple paths, shortest first; optionally include failed links."""
    if source not in state.nodes or host not in state.nodes:
        return
    if operational and (state.nodes[source].status == "down" or state.nodes[host].status == "down"):
        return
    queue = deque([(source, [], {source})])
    emitted = 0
    while queue:
        node, path, visited = queue.popleft()
        if node == host:
            yield path
            emitted += 1
            if limit is not None and emitted >= limit:
                return
            continue
        for lid, link in sorted(state.links.items()):
            if node not in link.endpoints or (operational and link.status != "up"):
                continue
            other = link.endpoints[1] if node == link.endpoints[0] else link.endpoints[0]
            if other not in state.nodes or (operational and state.nodes[other].status == "down"):
                continue
            if other not in visited:
                queue.append((other, path + [lid], visited | {other}))


def route_to(state, service_id, host):
    service = state.services[service_id]
    return next(paths(state, service.source_node_id, host, limit=1), None)


def has_alternative_path(state, service_id, *, operational=False):
    """Physical feasibility by default; operational=True also excludes down links."""
    service = state.services[service_id]
    current = state.routes[service_id]
    return any(path != current for path in paths(
        state, service.source_node_id, service.host_node_id,
        operational=operational, limit=2,
    ))


def reroute_feasibility(state, service_ids):
    return {sid: ("feasible" if has_alternative_path(state, sid) else "topology-infeasible")
            for sid in sorted(service_ids)}
