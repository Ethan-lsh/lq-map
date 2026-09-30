"""Dependency-only demand estimates; they order decisions but never authorize execution."""

from collections import Counter
from collections.abc import Mapping

from ir import CircuitDAG


def asap_starts(circuit: CircuitDAG, durations: Mapping[str, int]) -> dict[str, int]:
    """Earliest dependency-permitted start of every gate, assuming unlimited resources."""
    ends: dict[str, int] = {}
    starts: dict[str, int] = {}
    for gate in circuit.gates:
        starts[gate.id] = max((ends[p] for p in gate.predecessors), default=0)
        ends[gate.id] = starts[gate.id] + durations[gate.id]
    return starts


def request_order(circuit: CircuitDAG, durations: Mapping[str, int]) -> tuple[str, ...]:
    """T requests by estimated readiness, then input position (stable tie breaker)."""
    starts = asap_starts(circuit, durations)
    positions = {g.id: i for i, g in enumerate(circuit.gates)}
    requests = [g.id for g in circuit.gates if g.is_magic]
    return tuple(sorted(requests, key=lambda r: (starts[r], positions[r])))


def interaction_demand(circuit: CircuitDAG) -> tuple[Counter, Counter]:
    """Counts of (gate kind, first, second) Clifford interactions and T gates per qubit."""
    pairs: Counter = Counter()
    t_demand: Counter = Counter()
    for gate in circuit.gates:
        if gate.is_magic:
            t_demand[gate.qubits[0]] += 1
        elif len(gate.qubits) == 2:
            pairs[(gate.kind, *gate.qubits)] += 1
    return pairs, t_demand
