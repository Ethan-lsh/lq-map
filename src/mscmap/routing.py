"""Bounded executable movement search; no geometric-distance cost substitution."""

from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from .model import Action, Cell, SystemState
from .sim import Simulator

Goal = Callable[[SystemState], bool]
Positions = tuple[Cell, ...]


@dataclass(frozen=True)
class Route:
    swaps: tuple[Action, ...]
    explored: int


def data_route(sim: Simulator, goal: Goal, max_states: int = 2000) -> Route | None:
    """BFS over data-data permutations; occupied MSCs remain impassable.

    Reservations constrain every edge. The caller commits only the first SWAP and
    reroutes after completion. Detours and temporary distance increases are legal.
    """
    if goal(sim.state):
        return Route((), 1)
    qubits = tuple(sorted(sim.state.placement))
    start = tuple(sim.state.placement[q] for q in qubits)
    queue = deque([(start, ())])
    seen = {start}
    while queue and len(seen) <= max_states:
        positions, swaps_so_far = queue.popleft()
        probe = _probe_at(sim, qubits, positions)
        for action in _legal_data_swaps(probe):
            reached = _after_swap(positions, action.patches)
            if reached in seen:
                continue
            route = swaps_so_far + (action,)
            if goal(_placement_state(probe.state, qubits, reached)):
                return Route(route, len(seen) + 1)
            seen.add(reached)
            queue.append((reached, route))
    return None


def _probe_at(sim: Simulator, qubits: tuple[str, ...], positions: Positions) -> Simulator:
    probe = sim.snapshot()
    probe.state.placement = dict(zip(qubits, positions))
    return probe


def _placement_state(
    state: SystemState, qubits: tuple[str, ...], positions: Positions
) -> SystemState:
    reached = state.snapshot()
    reached.placement = dict(zip(qubits, positions))
    return reached


def _legal_data_swaps(probe: Simulator) -> Iterator[Action]:
    occupied = probe.state.reverse
    for one, other in probe.problem.layout.edges:
        if one not in occupied or other not in occupied:
            continue
        action = Action("swap", (one, other))
        if probe.can_start(action):
            yield action


def _after_swap(positions: Positions, patches: tuple[Cell, ...]) -> Positions:
    exchanged = list(positions)
    left, right = (positions.index(p) for p in patches)
    exchanged[left], exchanged[right] = exchanged[right], exchanged[left]
    return tuple(exchanged)


def site_paths(sim: Simulator, msc_id: str, limit: int, max_hops: int) -> list[tuple[Cell, ...]]:
    """Retention first, then bounded simple paths (multiple paths/site are allowed)."""
    start = sim.state.mscs[msc_id].site
    foreign_sites = {m.site for other, m in sim.state.mscs.items() if other != msc_id}
    queue = deque([(start,)])
    paths: list[tuple[Cell, ...]] = []
    while queue and len(paths) < limit:
        path = queue.popleft()
        paths.append(path)
        if len(path) - 1 < max_hops:
            for step in sim.problem.layout.neighbors(path[-1]):
                if step not in path and step not in foreign_sites:
                    queue.append(path + (step,))
    return paths


def all_walks(sim: Simulator, msc_id: str, max_hops: int) -> Iterator[tuple[Cell, ...]]:
    """Oracle enumeration: includes revisits, detours, and returning to the source."""
    stack = [(sim.state.mscs[msc_id].site,)]
    while stack:
        path = stack.pop()
        yield path
        if len(path) - 1 < max_hops:
            stack.extend(
                path + (step,) for step in reversed(sim.problem.layout.neighbors(path[-1]))
            )
