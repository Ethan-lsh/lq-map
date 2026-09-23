"""Integer-time exhaustive search for explicitly bounded tiny patch instances."""

from collections.abc import Iterator
from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import count
from time import perf_counter

from .model import Action, Phase
from .routing import all_walks
from .sim import Simulator


@dataclass(frozen=True)
class OracleResult:
    limit_reached: bool
    simulator: Simulator | None
    explored: int
    planning_seconds: float


def legal_actions(sim: Simulator, horizon: int) -> Iterator[Action]:
    state, problem = sim.state, sim.problem
    actions = []
    for x, y in problem.layout.edges:
        actions.append(Action("swap", (x, y)))
    for msc_id, msc in sorted(state.mscs.items()):
        if msc.phase == Phase.IDLE:
            mandatory = problem.costs.initialize + problem.costs.produce + problem.costs.consume
            hops = (horizon - state.time - mandatory) // problem.costs.swap
            if hops >= 0:
                for gate in problem.circuit.gates:
                    if (
                        gate.kind == "T"
                        and gate.id not in state.completed
                        and gate.id not in state.assignments
                    ):
                        for path in all_walks(sim, msc_id, hops):
                            action = Action("begin", msc=msc_id, gate=gate.id, path=path)
                            if sim.can_start(action):
                                yield action
        elif msc.phase == Phase.PREPARING:
            transaction = state.transactions[msc_id]
            actions.append(
                Action("swap", transaction.path[transaction.index : transaction.index + 2], msc_id)
                if transaction.index + 1 < len(transaction.path)
                else Action("initialize", (msc.site,), msc_id)
            )
        elif msc.phase == Phase.INITIALIZED:
            actions.append(Action("produce", (msc.site,), msc_id))
        elif msc.phase == Phase.OUTPUT:
            gate = problem.circuit.by_id[msc.request]
            actions.append(
                Action("consume", (msc.site, state.placement[gate.qubits[0]]), msc_id, gate.id)
            )
    for gate in problem.circuit.gates:
        if gate.kind != "T":
            actions.append(
                Action(
                    "single" if len(gate.qubits) == 1 else "two",
                    tuple(state.placement[q] for q in gate.qubits),
                    gate=gate.id,
                )
            )
    for action in actions:
        if (
            sim.can_start(action)
            and state.time + problem.costs.specs[action.kind].duration <= horizon
        ):
            yield action


def solve(sim: Simulator, *, state_limit: int = 100000, horizon: int = 14) -> OracleResult:
    problem = sim.problem
    if (
        len(problem.sites) != 1
        or len(problem.layout.usable) > 3
        or len(problem.circuit.gates) > 4
        or type(horizon) is not int
        or not 1 <= horizon <= 14
    ):
        raise ValueError(
            "oracle supports one MSC, <=3 usable cells, <=4 gates, search horizon 1..14"
        )
    if type(state_limit) is not int or state_limit <= 0:
        raise ValueError("oracle state_limit must be a positive integer")
    if sim.trace.events or sim.state != problem.initial_state():
        raise ValueError("oracle requires an initial state")
    started = perf_counter()
    serial = count()
    queue = [(0, 0, next(serial), sim.snapshot())]
    seen: dict[tuple, int] = {}
    explored = 0
    incumbent = None
    best_cost = float("inf")
    while queue and explored < state_limit:
        bound, _, _, node = heappop(queue)
        if bound >= best_cost:
            continue
        key = node.signature()
        if seen.get(key, float("inf")) <= bound:
            continue
        seen[key] = bound
        explored += 1
        if node.complete:
            incumbent, best_cost = node, node.state.swaps
            continue
        if node.state.time >= horizon:
            continue
        for action in legal_actions(node, horizon):
            child = node.snapshot()
            child.start(action)
            cost = _committed_swap_count(child)
            if cost < best_cost:
                heappush(queue, (cost, child.state.time, next(serial), child))
        # Unit waits include deliberate idling even when no operation is active.
        child = node.snapshot()
        child.advance(child.state.time + 1)
        child.emit("tick")
        cost = _committed_swap_count(child)
        if cost < best_cost:
            heappush(queue, (cost, child.state.time, next(serial), child))
    limit_reached = bool(queue) and any(item[0] < best_cost for item in queue)
    return OracleResult(limit_reached, incumbent, explored, perf_counter() - started)


def _committed_swap_count(sim: Simulator) -> int:
    """Lower bound including SWAPs that have started but not yet completed."""
    return sim.state.swaps + sum(operation.action.kind == "swap" for operation in sim.state.active)
