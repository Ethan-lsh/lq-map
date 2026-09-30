"""Stable logical gate identities and dependency-preserving circuit import."""

from dataclasses import dataclass
from typing import Any

ARITY = {"T": 1, "TDG": 1, "H": 1, "S": 1, "SDG": 1, "X": 1, "Z": 1, "CX": 2, "CZ": 2}
# Gates that consume one magic state. T-dagger is modeled like T (abstract assumption:
# the differing Clifford correction is folded into the consumption).
MAGIC_KINDS = frozenset({"T", "TDG"})


@dataclass(frozen=True)
class Gate:
    id: str
    kind: str
    qubits: tuple[str, ...]
    predecessors: frozenset[str]

    @property
    def is_magic(self) -> bool:
        return self.kind in MAGIC_KINDS


@dataclass(frozen=True)
class CircuitDAG:
    qubits: tuple[str, ...]
    gates: tuple[Gate, ...]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CircuitDAG":
        qubits = _imported_qubits(raw["qubits"])
        gates: list[Gate] = []
        declared_ids: set[str] = set()
        last_writer: dict[str, str] = {}
        for position, item in enumerate(raw["gates"]):
            gate = _imported_gate(item, position, qubits, declared_ids, last_writer)
            gates.append(gate)
            declared_ids.add(gate.id)
            last_writer.update({q: gate.id for q in gate.qubits})
        return cls(qubits, tuple(gates))

    @property
    def by_id(self) -> dict[str, Gate]:
        return {g.id: g for g in self.gates}

    @property
    def successors(self) -> dict[str, tuple[str, ...]]:
        return {g.id: tuple(h.id for h in self.gates if g.id in h.predecessors) for g in self.gates}

    def to_dict(self) -> dict[str, Any]:
        return {
            "qubits": list(self.qubits),
            "gates": [
                {
                    "id": g.id,
                    "kind": g.kind,
                    "qubits": list(g.qubits),
                    "predecessors": sorted(g.predecessors),
                }
                for g in self.gates
            ],
        }


def _imported_qubits(raw: Any) -> tuple[str, ...]:
    qubits = tuple(raw)
    if not qubits or any(not isinstance(q, str) or not q for q in qubits):
        raise ValueError("qubits must be nonempty string IDs")
    if len(set(qubits)) != len(qubits):
        raise ValueError("duplicate logical qubit ID")
    return qubits


def _imported_gate(
    item: dict[str, Any],
    position: int,
    qubits: tuple[str, ...],
    declared_ids: set[str],
    last_writer: dict[str, str],
) -> Gate:
    gate_id = item.get("id", f"g{position:04d}")
    kind = item["kind"].upper()
    operands = tuple(item["qubits"])
    if not isinstance(gate_id, str) or not gate_id or gate_id in declared_ids:
        raise ValueError(f"invalid or duplicate gate ID: {gate_id}")
    if kind not in ARITY or len(operands) != ARITY[kind]:
        raise ValueError(f"unsupported gate/arity: {kind} {operands}")
    if len(set(operands)) != len(operands) or not set(operands) <= set(qubits):
        raise ValueError(f"invalid logical operands at {gate_id}")
    return Gate(
        gate_id,
        kind,
        operands,
        _predecessors_of(item, gate_id, operands, declared_ids, last_writer),
    )


def _predecessors_of(
    item: dict[str, Any],
    gate_id: str,
    operands: tuple[str, ...],
    declared_ids: set[str],
    last_writer: dict[str, str],
) -> frozenset[str]:
    """Declared dependencies plus the implicit per-qubit program order."""
    declared = set(item.get("predecessors", []))
    if not declared <= declared_ids:
        raise ValueError(f"{gate_id}: explicit predecessors must precede the gate")
    return frozenset(declared | {last_writer[q] for q in operands if q in last_writer})
