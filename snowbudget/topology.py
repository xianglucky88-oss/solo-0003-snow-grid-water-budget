"""Responsibility-zone geometry and acyclic single-outlet topology checks."""
from .common import require
from .geometry import read_boxes


def read_zones(rows):
    # read_boxes(nonoverlap=True) rejects any interior overlap between zones.
    return read_boxes(rows, nonoverlap=True)


def read_topology(reservoir_ids, routes):
    """Validate an acyclic graph where every node has at most one outlet.

    routes maps a reservoir id to the downstream reservoir receiving its
    release. The graph must have exactly one terminal (single outlet) and
    contain no cycle. Returns an upstream list per reservoir and a processing
    order (topological), plus the terminal id.
    """
    ids = set(reservoir_ids)
    outlet = {}
    for edge in routes or []:
        up, down = edge['from'], edge['to']
        require(up in ids and down in ids, f'route references unknown reservoir: {up}->{down}')
        require(up != down, f'self loop on reservoir: {up}')
        require(up not in outlet, f'reservoir has multiple outlets: {up}')
        outlet[up] = down
    terminals = ids - set(outlet)
    require(len(terminals) == 1, f'topology must have exactly one outlet, got {sorted(terminals)}')
    upstream = {key: [] for key in ids}
    for up, down in outlet.items():
        upstream[down].append(up)
    order, state = [], {}

    def visit(node, trail):
        mark = state.get(node)
        require(mark != 'active', f'topology cycle: {" -> ".join(trail + [node])}')
        if mark == 'done':
            return
        state[node] = 'active'
        for parent in upstream[node]:
            visit(parent, trail + [node])
        state[node] = 'done'
        order.append(node)

    for key in reservoir_ids:
        visit(key, [])
    return upstream, order, next(iter(terminals)), outlet
