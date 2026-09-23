"""Declarative operation model and isolated mutable execution state."""

from copy import deepcopy
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any

from .ir import CircuitDAG

Cell = tuple[int, int]

RELOCATION_MODES = ("serialized", "disjoint_paths", "space_time")

OPERATION_DESCRIPTIONS = {
    "swap": (
        "adjacent data/data or owned idle/data",
        "both patches and contact",
        "exchange occupants; preserve logical gate operands",
    ),
    "initialize": ("owned transaction endpoint", "site and MSC", "initialized MSC"),
    "produce": ("initialized MSC", "site and MSC", "one owned output"),
    "consume": (
        "ready assigned T and adjacent output",
        "two patches, contact, MSC",
        "complete T; destroy output; release reusable site",
    ),
    "single": ("ready one-qubit gate", "operand patch", "complete gate"),
    "two": ("ready adjacent two-qubit gate", "two patches and contact", "complete gate"),
}


def cell(value: Any) -> Cell:
    if len(value) != 2 or any(type(x) is not int for x in value):
        raise ValueError(f"invalid integer grid coordinate: {value}")
    return tuple(value)


def adjacent(one: Cell, other: Cell) -> bool:
    return abs(one[0] - other[0]) + abs(one[1] - other[1]) == 1


@dataclass(frozen=True)
class Layout:
    usable: frozenset[Cell]
    blocked: frozenset[Cell]

    def __post_init__(self) -> None:
        if not self.usable or self.usable & self.blocked:
            raise ValueError("usable cells must be nonempty and disjoint from blocked cells")

    def neighbors(self, origin: Cell) -> tuple[Cell, ...]:
        return tuple(c for c in sorted(self.usable) if adjacent(origin, c))

    @property
    def edges(self) -> tuple[tuple[Cell, Cell], ...]:
        return tuple((a, b) for a in sorted(self.usable) for b in self.neighbors(a) if a < b)


class Phase(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    INITIALIZING = "initializing"
    INITIALIZED = "initialized"
    PRODUCING = "producing"
    OUTPUT = "output"
    CONSUMING = "consuming"


@dataclass(frozen=True)
class OperationSpec:
    kind: str
    duration: int
    preconditions: str
    resources: str
    effects: str


@dataclass(frozen=True)
class OperationModel:
    swap: int = 1
    initialize: int = 1
    produce: int = 3
    consume: int = 1
    single: int = 1
    two: int = 2

    def __post_init__(self) -> None:
        for spec in fields(self):
            duration = getattr(self, spec.name)
            if type(duration) is not int or duration <= 0:
                raise ValueError("operation durations must be positive integers")

    @property
    def specs(self) -> dict[str, OperationSpec]:
        return {
            kind: OperationSpec(kind, getattr(self, kind), *description)
            for kind, description in OPERATION_DESCRIPTIONS.items()
        }


@dataclass(frozen=True)
class Action:
    kind: str
    patches: tuple[Cell, ...] = ()
    msc: str | None = None
    gate: str | None = None
    path: tuple[Cell, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "patches": [list(p) for p in self.patches],
            "msc": self.msc,
            "gate": self.gate,
            "path": [list(p) for p in self.path],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Action":
        return cls(
            raw["kind"],
            tuple(cell(p) for p in raw.get("patches", [])),
            raw.get("msc"),
            raw.get("gate"),
            tuple(cell(p) for p in raw.get("path", [])),
        )


@dataclass
class MSC:
    site: Cell
    phase: Phase = Phase.IDLE
    request: str | None = None
    output: str | None = None
    generation: int = 0


@dataclass
class RelocationPlan:
    msc: str
    request: str
    path: tuple[Cell, ...]
    started: int
    index: int = 0

    @property
    def destination(self) -> Cell:
        return self.path[-1]

    @property
    def affected(self) -> frozenset[Cell]:
        return frozenset(self.path)


@dataclass(frozen=True)
class ActiveOperation:
    id: int
    action: Action
    start: int
    end: int
    resources: frozenset[str]


@dataclass
class SystemState:
    placement: dict[str, Cell]
    mscs: dict[str, MSC]
    time: int = 0
    completed: dict[str, int] = field(default_factory=dict)
    active: list[ActiveOperation] = field(default_factory=list)
    transactions: dict[str, RelocationPlan] = field(default_factory=dict)
    next_operation: int = 0
    swaps: int = 0
    relocations: int = 0

    @property
    def reverse(self) -> dict[Cell, str]:
        return {patch: qubit for qubit, patch in self.placement.items()}

    @property
    def assignments(self) -> dict[str, str]:
        return {m.request: msc_id for msc_id, m in self.mscs.items() if m.request is not None}

    @property
    def reservations(self) -> frozenset[str]:
        return frozenset(r for op in self.active for r in op.resources)

    def snapshot(self) -> "SystemState":
        return deepcopy(self)


@dataclass(frozen=True)
class Problem:
    circuit: CircuitDAG
    layout: Layout
    placement: dict[str, Cell]
    sites: dict[str, Cell]
    costs: OperationModel
    mode: str = "serialized"

    def __post_init__(self) -> None:
        if self.mode not in RELOCATION_MODES:
            raise ValueError(f"unsupported relocation mode: {self.mode}")
        if not self.sites or any(not isinstance(k, str) or not k for k in self.sites):
            raise ValueError("at least one named MSC is required")
        if set(self.placement) != set(self.circuit.qubits):
            raise ValueError("placement must cover exactly the circuit logical qubits")
        self._validate_footprint()

    def _validate_footprint(self) -> None:
        """Exactly N data cells plus M distinct MSC sites: no hidden routing buffers."""
        occupied = list(self.placement.values()) + list(self.sites.values())
        if len(set(occupied)) != len(occupied) or set(occupied) != set(self.layout.usable):
            raise ValueError("usable cells must contain exactly N data + M distinct MSC sites")

    def initial_state(self) -> SystemState:
        return SystemState(
            dict(self.placement), {msc_id: MSC(site) for msc_id, site in self.sites.items()}
        )


def patch_resource(patch: Cell) -> str:
    return f"patch:{patch[0]},{patch[1]}"


def boundary_resource(one: Cell, other: Cell) -> str:
    first, second = sorted((one, other))
    return f"boundary:{first}:{second}"


def resources(action: Action) -> frozenset[str]:
    required = {patch_resource(p) for p in action.patches}
    if len(action.patches) == 2:
        required.add(boundary_resource(*action.patches))
    if action.msc is not None:
        required.add(f"msc:{action.msc}")
    return frozenset(required)
