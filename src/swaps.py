"""SWAP planning under the shared local contact rules: contacts, exchanges, paths.

Nothing here constructs a path between distant operands. A nonlocal gate becomes
executable only after a sequence of single SWAP/move exchanges brings its operands
into a local contact.
"""

from collections import deque
from collections.abc import Container, Iterator
from functools import cache

from ir import Gate
from model import (
    SIDE_OFFSETS,
    Action,
    Contact,
    ContactRule,
    Geometry,
    Problem,
    SystemState,
    Tile,
    contact_rule,
    contacts,
)

UNREACHABLE = 10**6  # static distance used when no exchange sequence exists; ranking only
NO_TILES: frozenset[Tile] = frozenset()


def live_contact(
    problem: Problem,
    state: SystemState,
    kind: str,
    tiles: tuple[Tile, Tile],
    gate: Gate | None = None,
) -> Contact | None:
    """First legal contact whose ancilla tile is not reserved by an active operation."""
    rule = contact_rule(kind, gate.kind if gate else None)
    for contact in contacts(problem.geometry, *tiles, rule):
        if not any(t in state.reserved for t in contact.ancilla):
            return contact
    return None


@cache
def exchange_neighbors(geometry: Geometry, origin: Tile) -> tuple[Tile, ...]:
    """Slots one SWAP/move away: in-line cardinal neighbors, adjacent or across one
    junction tile, that have an auxiliary tile."""
    rule = contact_rule("swap")
    candidates = []
    for side in SIDE_OFFSETS:
        near = geometry.neighbor(origin, side)
        candidates += [near, geometry.neighbor(near, side)]
    return tuple(
        t for t in candidates if t in geometry.slots and contacts(geometry, origin, t, rule)
    )


def exchange_tree(
    geometry: Geometry, origin: Tile, blocked: Container[Tile] = NO_TILES
) -> tuple[dict[Tile, Tile | None], dict[Tile, int]]:
    """Breadth-first exchange tree from `origin` avoiding blocked slots: parents and
    exchange counts, in stable order."""
    parents: dict[Tile, Tile | None] = {origin: None}
    depth = {origin: 0}
    queue = deque([origin])
    while queue:
        current = queue.popleft()
        for step in exchange_neighbors(geometry, current):
            if step not in parents and step not in blocked:
                parents[step], depth[step] = current, depth[current] + 1
                queue.append(step)
    return parents, depth


@cache
def static_depths(geometry: Geometry, origin: Tile) -> dict[Tile, int]:
    """Exchange counts from `origin` ignoring every other patch (static estimate)."""
    return exchange_tree(geometry, origin)[1]


@cache
def contact_pairs(geometry: Geometry, rule: ContactRule) -> tuple[tuple[Tile, Tile], ...]:
    """Every ordered slot pair with a local contact under `rule`, in stable order."""
    near = [(dr, dc) for dr in range(-2, 3) for dc in range(-2, 3) if (dr, dc) != (0, 0)]
    return tuple(
        (a, b)
        for a in sorted(geometry.slots)
        for b in ((a[0] + dr, a[1] + dc) for dr, dc in near)
        if b in geometry.slots and contacts(geometry, a, b, rule)
    )


def best_arrangement(
    geometry: Geometry, rule: ContactRule, depths: tuple[dict[Tile, int], dict[Tile, int]]
) -> tuple[int, Tile, Tile] | None:
    """Contact pair (x, y) minimizing depth0[x] + depth1[y]; a fixed endpoint has a
    single zero-depth entry. Ties break by stable pair order."""
    first, second = depths
    return min(
        (
            (first[x] + second[y], x, y)
            for x, y in contact_pairs(geometry, rule)
            if x in first and y in second
        ),
        default=None,
    )


def movable_endpoints(gate: Gate) -> tuple[int, ...]:
    """Endpoints a SWAP plan may move; a T's output patch stays at its MSC site."""
    return (0,) if gate.is_magic else (0, 1)


@cache
def pair_distance(
    geometry: Geometry, rule: ContactRule, tiles: tuple[Tile, Tile], movable: tuple[int, ...]
) -> int:
    """Static exchanges (every movable endpoint may move) before the pair touches."""
    depths = tuple(
        static_depths(geometry, t) if i in movable else {t: 0} for i, t in enumerate(tiles)
    )
    best = best_arrangement(geometry, rule, depths)
    return UNREACHABLE if best is None else best[0]


def gate_distance(problem: Problem, gate: Gate, tiles: tuple[Tile, ...]) -> int:
    """Static exchanges needed before the gate's contact exists at `tiles`."""
    rule = contact_rule(problem.gate_kind(gate), gate.kind)
    return pair_distance(problem.geometry, rule, tuple(tiles), movable_endpoints(gate))


def exchange_action(
    problem: Problem, state: SystemState, origin: Tile, destination: Tile
) -> Action | None:
    """SWAP (occupied destination) or move (empty destination) with a free junction."""
    kind = "swap" if destination in state.data_at else "move"
    contact = live_contact(problem, state, kind, (origin, destination))
    return Action(kind, (origin, destination), contact) if contact is not None else None


def exchange_options(problem: Problem, state: SystemState, qubit: str) -> Iterator[Action]:
    """Legal-shaped single exchanges of one data patch; callers still check `can_start`."""
    origin = state.placement[qubit]
    for destination in exchange_neighbors(problem.geometry, origin):
        if state.msc_at(destination) is None:
            action = exchange_action(problem, state, origin, destination)
            if action is not None:
                yield action


def relocation_action(state: SystemState, msc: str, site: Tile) -> Action:
    """Idle MSC relocation to an empty C slot; no quantum state or data moves."""
    return Action("relocate", (state.mscs[msc].site, site), msc=msc)
