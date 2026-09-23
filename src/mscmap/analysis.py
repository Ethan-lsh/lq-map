"""Dependency lower bounds are estimates, never execution authorization."""

from dataclasses import dataclass

from .model import Problem, SystemState


@dataclass(frozen=True)
class Demand:
    request: str
    target: str
    predecessors: frozenset[str]
    estimated_ready: int
    critical_tail: int


@dataclass(frozen=True)
class DemandWindow:
    requests: tuple[Demand, ...]


def demand_window(problem: Problem, state: SystemState, limit: int) -> DemandWindow:
    durations = _gate_durations(problem)
    ready_at = _estimated_ready_times(problem, state, durations)
    critical_tail = _critical_tails(problem, durations)
    demands = [
        Demand(
            gate.id,
            gate.qubits[0],
            gate.predecessors,
            ready_at[gate.id],
            critical_tail[gate.id],
        )
        for gate in _unassigned_requests(problem, state)
    ]
    demands.sort(key=lambda d: (d.estimated_ready, d.request))
    return DemandWindow(tuple(demands[:limit]))


def _unassigned_requests(problem: Problem, state: SystemState):
    assigned = state.assignments
    return [
        g
        for g in problem.circuit.gates
        if g.kind == "T" and g.id not in state.completed and g.id not in assigned
    ]


def _gate_durations(problem: Problem) -> dict[str, int]:
    costs = problem.costs
    return {
        g.id: (
            costs.consume if g.kind == "T" else costs.single if len(g.qubits) == 1 else costs.two
        )
        for g in problem.circuit.gates
    }


def _estimated_ready_times(
    problem: Problem, state: SystemState, durations: dict[str, int]
) -> dict[str, int]:
    """Earliest dependency-permitted start, assuming unlimited resources."""
    active_ends = {op.action.gate: op.end for op in state.active if op.action.gate}
    ends = dict(state.completed)
    starts: dict[str, int] = {}
    for gate in problem.circuit.gates:
        starts[gate.id] = max([state.time] + [ends[d] for d in gate.predecessors])
        ends[gate.id] = ends.get(
            gate.id, active_ends.get(gate.id, starts[gate.id] + durations[gate.id])
        )
    return starts


def _critical_tails(problem: Problem, durations: dict[str, int]) -> dict[str, int]:
    """Longest remaining dependency chain, inclusive of the gate itself."""
    successors = problem.circuit.successors
    tails: dict[str, int] = {}
    for gate in reversed(problem.circuit.gates):
        tails[gate.id] = durations[gate.id] + max(
            (tails[s] for s in successors[gate.id]), default=0
        )
    return tails
