"""Stable logical gate identities and dependency-preserving circuit import.

Dependencies follow commutation by default: on a shared qubit two gates commute when
both act as functions of Z there (T, TDG, S, SDG, Z, CZ, CX control) or both as functions
of X (X, CX target); H commutes with neither. Commuting gates keep no mutual edge, so
any topological order of the DAG implements the same unitary. `program_order` keeps
the stricter per-qubit input order.
"""

from dataclasses import dataclass, field
from typing import Any

ARITY = {"T": 1, "TDG": 1, "H": 1, "S": 1, "SDG": 1, "X": 1, "Z": 1, "CX": 2, "CZ": 2}
# Gates that consume one magic state. T-dagger is modeled like T (abstract assumption:
# the differing Clifford correction is folded into the consumption).
MAGIC_KINDS = frozenset({"T", "TDG"})
DEPENDENCY_MODES = ("commutation", "program_order")
Z_ROLE_KINDS = frozenset({"T", "TDG", "S", "SDG", "Z", "CZ"})
# Pauli gates tracked classically by `pauli_frame` instead of executed.
FRAME_KINDS = frozenset({"X", "Z"})


def operand_role(kind: str, position: int) -> str | None:
    """Commutation class of a gate on its operand at `position`: "Z", "X", or None."""
    if kind in Z_ROLE_KINDS:
        return "Z"
    if kind == "CX":
        return "Z" if position == 0 else "X"
    return "X" if kind == "X" else None


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
    def from_dict(cls, raw: dict[str, Any], dependencies: str = "commutation") -> "CircuitDAG":
        if dependencies not in DEPENDENCY_MODES:
            raise ValueError(f"dependencies must be one of {', '.join(DEPENDENCY_MODES)}")
        qubits = _imported_qubits(raw["qubits"])
        gates: list[Gate] = []
        declared_ids: set[str] = set()
        runs = {q: _Run() for q in qubits}
        for position, item in enumerate(raw["gates"]):
            gate = _imported_gate(item, position, qubits, declared_ids, runs, dependencies)
            gates.append(gate)
            declared_ids.add(gate.id)
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


@dataclass
class _Run:
    """Per-qubit dependency state: the latest run of mutually commuting gates (`role`,
    `members`) and the run before it, which every member depends on."""

    role: str | None = None
    members: list[str] = field(default_factory=list)
    before: list[str] = field(default_factory=list)

    def add(self, gate_id: str, role: str | None) -> list[str]:
        """Record `gate_id` and return the gates on this qubit it must follow."""
        if role is not None and role == self.role:
            self.members.append(gate_id)
            return self.before
        self.role, self.before, self.members = role, self.members, [gate_id]
        return self.before


def _imported_gate(
    item: dict[str, Any],
    position: int,
    qubits: tuple[str, ...],
    declared_ids: set[str],
    runs: dict[str, _Run],
    dependencies: str,
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
    declared = set(item.get("predecessors", []))
    if not declared <= declared_ids:
        raise ValueError(f"{gate_id}: explicit predecessors must precede the gate")
    implied: set[str] = set()
    for index, q in enumerate(operands):
        # program_order: no gate shares a run, so each follows the previous one on q.
        role = operand_role(kind, index) if dependencies == "commutation" else None
        implied.update(runs[q].add(gate_id, role))
    return Gate(gate_id, kind, operands, frozenset(declared | implied))


@dataclass(frozen=True)
class FrameRecord:
    """Effect of `pauli_frame`: removed Pauli gates, T/TDG swaps, and the residual frame
    (per qubit "X", "Z", or "Y") that classically corrects final measurement outcomes."""

    removed: tuple[str, ...]
    swapped_t: tuple[str, ...]
    residual: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "removed": list(self.removed),
            "swapped_t": list(self.swapped_t),
            "residual": dict(sorted(self.residual.items())),
        }


def pauli_frame(raw: dict[str, Any]) -> tuple[dict[str, Any], FrameRecord]:
    """Track X/Z gates classically instead of executing them (modeling choice).

    A Pauli frame F before gate G becomes G F G^dag after it: Cliffords map it to
    another Pauli and leave G unchanged; at T or TDG an X component turns the gate
    into its inverse (T X = e^{i pi/4} X T^dag) and the frame continues. No other gate
    changes, and every removed gate keeps no execution resources.
    """
    frame = {q: [0, 0] for q in raw["qubits"]}  # [x, z] bits per qubit
    gates, removed, swapped = [], [], []
    for position, item in enumerate(raw["gates"]):
        kind, operands = str(item["kind"]).upper(), list(item["qubits"])
        gate_id = item.get("id", f"g{position:04d}")
        if any(q not in frame for q in operands):
            gates.append(item)  # left for import validation
            continue
        if kind in FRAME_KINDS and len(operands) == 1:
            frame[operands[0]][kind == "Z"] ^= 1
            removed.append(gate_id)
            continue
        if removed and set(item.get("predecessors", [])) & set(removed):
            raise ValueError(f"{gate_id}: explicit predecessors cannot name Pauli-frame gates")
        if kind in MAGIC_KINDS and frame[operands[0]][0]:
            item = {**item, "kind": "TDG" if kind == "T" else "T"}
            swapped.append(gate_id)
        _conjugate(frame, kind, operands)
        gates.append({**item, "id": gate_id})
    residual = {q: "IZXY"[2 * x + z] for q, (x, z) in frame.items() if x or z}
    return {**raw, "gates": gates}, FrameRecord(tuple(removed), tuple(swapped), residual)


def _conjugate(frame: dict[str, list[int]], kind: str, operands: list[str]) -> None:
    """Move the frame past one Clifford gate (T/TDG leave it unchanged)."""
    if kind == "H":
        f = frame[operands[0]]
        f[0], f[1] = f[1], f[0]
    elif kind in {"S", "SDG"}:  # X -> +-Y; signs are irrelevant to the frame
        frame[operands[0]][1] ^= frame[operands[0]][0]
    elif kind == "CX" and len(operands) == 2:
        control, target = frame[operands[0]], frame[operands[1]]
        target[0] ^= control[0]
        control[1] ^= target[1]
    elif kind == "CZ" and len(operands) == 2:
        a, b = frame[operands[0]], frame[operands[1]]
        a[1], b[1] = a[1] ^ b[0], b[1] ^ a[0]
