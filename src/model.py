"""Tile geometry, local lattice-surgery and SWAP contracts, and isolated execution state.

There are no routing paths: every two-patch operation acts through a local contact
(direct contact or one shared ancilla tile). Distant operands are first brought into
contact by sequences of exchanges (SWAPs).
"""

from copy import deepcopy
from dataclasses import dataclass, field, fields
from enum import Enum
from functools import cache, cached_property
from math import ceil, floor
from random import Random
from typing import Any

from analysis import request_order
from ir import CircuitDAG, Gate

Tile = tuple[int, int]  # (row, column)

SIDE_OFFSETS = {"N": (-1, 0), "E": (0, 1), "S": (1, 0), "W": (0, -1)}
OPPOSITE = {"N": "S", "E": "W", "S": "N", "W": "E"}
# Every logical patch has the same fixed orientation; no rotation operation exists.
BOUNDARY_SIDES = {"Z": ("N", "S"), "X": ("E", "W"), "any": ("N", "E", "S", "W")}

SLOT, CULTIVATION, BUS, BLOCKED = "S", "C", "B", "."
TILE_KINDS = {
    SLOT: "patch slot (data only)",
    CULTIVATION: "patch slot where an MSC resource may cultivate",
    BUS: "local ancilla/junction tile; never holds a patch",
    BLOCKED: "unavailable tile; not provisioned",
}

GATE_KINDS = frozenset({"single", "two", "consume"})
MOVEMENT_KINDS = frozenset({"exchange", "relocate"})
CULTIVATION_KINDS = frozenset({"initialize", "cultivate"})

OPERATION_CONTRACTS = {
    # kind: (preconditions, reserved tiles, completion effect)
    "assign": (
        "idle MSC; unassigned unfinished T request",
        "none (zero duration)",
        "MSC owns the request",
    ),
    "relocate": (
        "idle or assigned MSC before initialization; destination is an empty C slot",
        "source and destination slots",
        "MSC site moves; no quantum state is transferred",
    ),
    "initialize": (
        "assigned MSC",
        "site slot (0 lcycles under exponential supply)",
        "MSC initialized",
    ),
    "cultivate": (
        "initialized MSC",
        "site slot",
        "success: one owned output; failure: back to assigned (retry); duration is "
        "costs.cultivate, or an exponential sample under supply.distribution=exponential",
    ),
    "consume": (
        "dependency-ready T; owned output; data and output Z sides in local contact",
        "data slot, output slot, contact ancilla tile",
        "T completed; output destroyed; MSC idle",
    ),
    "single": ("dependency-ready one-qubit Clifford", "operand slot", "gate completed"),
    "two": (
        "dependency-ready CX (L-shaped ancilla: control Z, target X) or CZ (Z-Z ancilla)",
        "both operand slots and the ancilla tile",
        "gate completed",
    ),
    "exchange": (
        "data patch and an in-line cardinal neighbor slot (data or empty, not an MSC site)",
        "both slots and one auxiliary bus tile (the junction, else a tile beside either)",
        "slot contents exchanged (into an empty slot: a move); logical mapping updated",
    ),
}


def tile(value: Any) -> Tile:
    if len(value) != 2 or any(type(x) is not int for x in value):
        raise ValueError(f"invalid integer tile coordinate: {value}")
    return tuple(value)


@dataclass(frozen=True, eq=False)
class Geometry:
    """Explicit rectangular tile grid; every provisioned tile is listed by kind.

    Identity equality keeps contact-cache keys cheap; compare `rows` for content.
    """

    rows: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.rows or len({len(r) for r in self.rows}) != 1 or not self.rows[0]:
            raise ValueError("geometry tiles must be a nonempty rectangle of equal-length rows")
        unknown = {c for r in self.rows for c in r} - set(TILE_KINDS)
        if unknown:
            raise ValueError(f"unknown tile kinds {sorted(unknown)}; use {sorted(TILE_KINDS)}")
        if not self.slots:
            raise ValueError("geometry needs at least one patch slot")

    def kind(self, where: Tile) -> str:
        r, c = where
        if 0 <= r < len(self.rows) and 0 <= c < len(self.rows[0]):
            return self.rows[r][c]
        return BLOCKED

    def _tiles(self, *kinds: str) -> frozenset[Tile]:
        return frozenset(
            (r, c) for r, row in enumerate(self.rows) for c, k in enumerate(row) if k in kinds
        )

    @cached_property
    def slots(self) -> frozenset[Tile]:
        return self._tiles(SLOT, CULTIVATION)

    @cached_property
    def cultivation_sites(self) -> frozenset[Tile]:
        return self._tiles(CULTIVATION)

    @cached_property
    def bus(self) -> frozenset[Tile]:
        return self._tiles(BUS)

    @cached_property
    def area(self) -> dict[str, int]:
        """Provisioned tiles by kind; blocked tiles are not provisioned."""
        slots = len(self.slots) - len(self.cultivation_sites)
        cultivation, bus = len(self.cultivation_sites), len(self.bus)
        return {
            "slot": slots,
            "cultivation": cultivation,
            "bus": bus,
            "total": slots + cultivation + bus,
        }

    def neighbor(self, where: Tile, side: str) -> Tile:
        dr, dc = SIDE_OFFSETS[side]
        return where[0] + dr, where[1] + dc


@dataclass(frozen=True)
class Contact:
    """Local lattice-surgery contact between an operation's first and second slot.

    `sides[i]` is the side of endpoint `i` that takes part. `ancilla` is empty for
    direct contact across facing boundaries, or the one bus tile touching both sides.
    """

    sides: tuple[str, str]
    ancilla: tuple[Tile, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"sides": list(self.sides), "ancilla": [list(t) for t in self.ancilla]}


@dataclass(frozen=True)
class ContactRule:
    """Boundary types (first, second) and whether an ancilla tile is mandatory.

    An `exchange` rule (SWAP) instead requires in-line cardinal neighbors plus one
    auxiliary bus tile; boundary types do not matter for an exchange.
    """

    boundaries: tuple[str, str]
    needs_ancilla: bool
    exchange: bool = False


# One-qubit Cliffords charged `phase_gate` instead of `single`.
PHASE_GATES = frozenset({"S", "SDG"})


@dataclass(frozen=True)
class OperationCosts:
    """Integer durations in logical cycles (lcycles); not calibrated values.

    Gate defaults follow PureMagic's lcycle accounting (CX 2, S/SX 3, T 1). H, X, Z,
    CZ, cultivation, exchange (SWAP), and relocation have no PureMagic counterpart there
    (its tableau absorbs H/CZ/Paulis and it routes instead of swapping); their
    defaults are modeling assumptions.
    """

    single: int = 1
    phase_gate: int = 3
    two: int = 2
    consume: int = 1
    initialize: int = 1
    cultivate: int = 3
    exchange: int = 3
    relocate: int = 1

    def __post_init__(self) -> None:
        for spec in fields(self):
            value = getattr(self, spec.name)
            if type(value) is not int or value < 1:
                raise ValueError(f"costs.{spec.name} must be an integer >= 1")

    def gate_duration(self, gate: Gate) -> int:
        if gate.is_magic:
            return self.consume
        if len(gate.qubits) == 2:
            return self.two
        return self.phase_gate if gate.kind in PHASE_GATES else self.single

    def duration(self, action: "Action", gate: Gate | None = None) -> int:
        """Duration of `action`; gate actions need their circuit `gate`."""
        if action.kind == "assign":
            return 0
        if action.kind in GATE_KINDS:
            if gate is None or gate.id != action.gate:
                raise ValueError(f"{action.kind} duration needs its gate {action.gate}")
            return self.gate_duration(gate)
        return getattr(self, action.kind)


@dataclass(frozen=True)
class TimeScale:
    """Conversion of lcycles to wall time; metadata only, durations stay in lcycles.

    A logical cycle takes at least `code_distance` physical cycles; the model uses that
    minimum (modeling choice), so a 1 us physical cycle at the reference distance 17
    gives 17 us per lcycle.
    """

    code_distance: int = 17
    physical_cycle_us: float = 1.0

    def __post_init__(self) -> None:
        if type(self.code_distance) is not int or self.code_distance < 1:
            raise ValueError("timing.code_distance must be an integer >= 1")
        value = self.physical_cycle_us
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ValueError("timing.physical_cycle_us must be a positive number")

    @property
    def lcycle_us(self) -> float:
        return self.code_distance * self.physical_cycle_us


@cache
def contact_rule(kind: str, gate_kind: str | None = None) -> ContactRule | None:
    """Local arrangement required by each two-patch operation (None: no contact)."""
    if kind == "two":
        # CX: an L-shaped ancilla tile (control Z side, target X side); CZ: Z-Z in line.
        return ContactRule(("Z", "X") if gate_kind == "CX" else ("Z", "Z"), True)
    if kind == "consume":
        return ContactRule(("Z", "Z"), False)
    if kind == "exchange":
        return ContactRule(("any", "any"), True, exchange=True)
    return None


@cache
def contacts(
    geometry: Geometry, first: Tile, second: Tile, rule: ContactRule
) -> tuple[Contact, ...]:
    """Every legal local contact between two slots, in stable side order.

    The ancilla region is at most one bus tile; nothing connects distant patches.
    """
    if rule.exchange:
        return _exchange_contacts(geometry, first, second)
    found = []
    first_sides = BOUNDARY_SIDES[rule.boundaries[0]]
    second_sides = BOUNDARY_SIDES[rule.boundaries[1]]
    for side in first_sides:
        near = geometry.neighbor(first, side)
        if not rule.needs_ancilla and near == second and OPPOSITE[side] in second_sides:
            found.append(Contact((side, OPPOSITE[side])))
        if geometry.kind(near) != BUS:
            continue
        # One ancilla tile touching both patches: in line (facing sides) or L-shaped.
        found.extend(
            Contact((side, other), (near,))
            for other in second_sides
            if geometry.neighbor(second, other) == near
        )
    return tuple(found)


def _exchange_contacts(geometry: Geometry, first: Tile, second: Tile) -> tuple[Contact, ...]:
    """In-line cardinal neighbors: across one junction bus tile (the auxiliary), or
    directly adjacent with a bus tile beside either patch as the auxiliary."""
    for side in SIDE_OFFSETS:
        near, facing = geometry.neighbor(first, side), (side, OPPOSITE[side])
        if near == second:
            beside = (geometry.neighbor(t, s) for t in (first, second) for s in SIDE_OFFSETS)
            return tuple(Contact(facing, (t,)) for t in beside if geometry.kind(t) == BUS)
        if geometry.kind(near) == BUS and geometry.neighbor(near, side) == second:
            return (Contact(facing, (near,)),)
    return ()


SUPPLY_DISTRIBUTIONS = ("bernoulli", "exponential")
# PureMagic's default cultivation rate (states per MSC per lcycle): its paper fits
# lambda = 0.00227 per physical cycle and divides cultivation samples by d = 17
# (arXiv:2512.06484, Sec. V-B); the code default `--magic-state-lambda` is 0.0387396.
PUREMAGIC_RATE = 0.0387396


@dataclass(frozen=True)
class SupplyModel:
    """Stochastic cultivation supply; realized outcomes derive from (seed, sample, MSC,
    generation, attempt), so they do not depend on event order.

    `bernoulli`: each initialize + cultivate attempt succeeds with `success_probability`;
    a failure repeats both. Planning assumes success on attempt `planning_attempts`.

    `exponential` (PureMagic, arXiv:2512.06484 Sec. III-C; `src/cultivation.rs`): the
    production time of each state is an exponential sample with `rate` lambda per lcycle,
    rounded to the nearest integer (0 means ready at once). Injection, cultivation, and
    escape collapse into this one sample, so `initialize` takes 0 lcycles, every attempt
    succeeds, and `costs.initialize`/`costs.cultivate` are unused. Planning copies assume
    `planning_cultivation` lcycles and never see the realized sample.
    """

    distribution: str = "bernoulli"
    success_probability: float = 1.0
    rate: float = PUREMAGIC_RATE
    seed: int = 0
    sample: int = 0

    def __post_init__(self) -> None:
        if self.distribution not in SUPPLY_DISTRIBUTIONS:
            raise ValueError(f"supply.distribution must be one of {list(SUPPLY_DISTRIBUTIONS)}")
        for name, upper in (("success_probability", 1), ("rate", None)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"supply.{name} must be a positive number")
            if upper is not None and value > upper:
                raise ValueError(f"supply.{name} must be in (0, {upper}]")
        if self.exponential and self.success_probability != 1:
            raise ValueError("exponential supply always succeeds; omit success_probability")
        if type(self.seed) is not int or type(self.sample) is not int or self.sample < 0:
            raise ValueError("supply seed and sample must be integers (sample >= 0)")

    @property
    def exponential(self) -> bool:
        return self.distribution == "exponential"

    @property
    def deterministic(self) -> bool:
        return not self.exponential and self.success_probability == 1

    @property
    def planning_attempts(self) -> int:
        return ceil(1 / self.success_probability - 1e-12)

    @property
    def planning_cultivation(self) -> int:
        """Exponential planning estimate: the mean 1/rate, rounded, at least 1 lcycle."""
        return max(1, floor(1 / self.rate + 0.5))

    def _draw(self, msc: str, generation: int, attempt: int) -> Random:
        return Random(f"{self.seed}:{self.sample}:{msc}:{generation}:{attempt}")

    def initialize_duration(self, costs: "OperationCosts") -> int:
        return 0 if self.exponential else costs.initialize

    def cultivate_duration(
        self, costs: "OperationCosts", msc: str, generation: int, attempt: int, realized: bool
    ) -> int:
        if not self.exponential:
            return costs.cultivate
        if not realized:
            return self.planning_cultivation
        # Round half away from zero, as Rust's f64::round in PureMagic.
        return floor(self._draw(msc, generation, attempt).expovariate(self.rate) + 0.5)

    def succeeds(self, msc: str, generation: int, attempt: int, realized: bool) -> bool:
        if self.exponential:
            return True
        if not realized:
            return attempt + 1 >= self.planning_attempts
        return self._draw(msc, generation, attempt).random() < self.success_probability


@dataclass(frozen=True)
class Objective:
    """Weighted execution cost shared by planning and evaluation.

    Volumes are tile-lcycles: `movement_volume` (exchange and relocation footprints),
    `operation_volume` (local gates and T consumption, including ancilla tiles), and
    `cultivation_volume` (initialize + cultivate, retries included). `request_wait` is
    the summed T wait from readiness to consumption start; `execution_lcycles` is the
    number of lcycles from time 0 until the last operation finishes.
    """

    movement_volume: float = 1.0
    operation_volume: float = 1.0
    cultivation_volume: float = 1.0
    request_wait: float = 1.0
    execution_lcycles: float = 1.0

    def __post_init__(self) -> None:
        values = [getattr(self, f.name) for f in fields(self)]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 for v in values):
            raise ValueError("objective weights must be nonnegative numbers")
        if not any(values):
            raise ValueError("at least one objective weight must be positive")


class Phase(str, Enum):
    IDLE = "idle"
    ASSIGNED = "assigned"
    INITIALIZING = "initializing"
    INITIALIZED = "initialized"
    CULTIVATING = "cultivating"
    OUTPUT = "output"
    CONSUMING = "consuming"


RELOCATABLE_PHASES = frozenset({Phase.IDLE, Phase.ASSIGNED})


@dataclass(frozen=True)
class Action:
    kind: str
    tiles: tuple[Tile, ...] = ()
    contact: Contact | None = None
    msc: str | None = None
    gate: str | None = None

    @property
    def footprint(self) -> frozenset[Tile]:
        ancilla = self.contact.ancilla if self.contact is not None else ()
        return frozenset(self.tiles) | frozenset(ancilla)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "tiles": [list(t) for t in self.tiles],
            "contact": self.contact.to_dict() if self.contact is not None else None,
            "msc": self.msc,
            "gate": self.gate,
        }


@dataclass
class MSC:
    site: Tile
    phase: Phase = Phase.IDLE
    request: str | None = None
    output: str | None = None
    generation: int = 0
    attempt: int = 0


@dataclass(frozen=True)
class ActiveOperation:
    id: int
    action: Action
    start: int
    end: int


@dataclass
class SystemState:
    placement: dict[str, Tile]
    mscs: dict[str, MSC]
    ready: set[str]
    waiting_predecessors: dict[str, int]
    unassigned: list[str]
    data_at: dict[Tile, str] = field(default_factory=dict)
    reserved: dict[Tile, int] = field(default_factory=dict)
    time: int = 0
    completed: dict[str, int] = field(default_factory=dict)
    active: list[ActiveOperation] = field(default_factory=list)
    next_operation: int = 0

    def __post_init__(self) -> None:
        self.data_at = self.data_at or {t: q for q, t in self.placement.items()}

    def msc_at(self, where: Tile) -> str | None:
        return next((m for m, msc in self.mscs.items() if msc.site == where), None)

    def assignment(self, request: str) -> str | None:
        return next((m for m, msc in sorted(self.mscs.items()) if msc.request == request), None)

    def snapshot(self) -> "SystemState":
        return deepcopy(self)


@dataclass(frozen=True)
class Problem:
    circuit: CircuitDAG
    geometry: Geometry
    placement: dict[str, Tile]
    sites: dict[str, Tile]
    costs: OperationCosts
    supply: SupplyModel
    objective: Objective
    time_scale: TimeScale = TimeScale()

    def __post_init__(self) -> None:
        if not self.sites or any(not isinstance(k, str) or not k for k in self.sites):
            raise ValueError("at least one named MSC is required")
        if set(self.placement) != set(self.circuit.qubits):
            raise ValueError("placement must cover exactly the circuit logical qubits")
        occupied = list(self.placement.values()) + list(self.sites.values())
        if len(set(occupied)) != len(occupied):
            raise ValueError("data patches and MSC resources need distinct slots")
        if not set(self.placement.values()) <= self.geometry.slots:
            raise ValueError("every data patch must be placed on an S or C slot")
        if not set(self.sites.values()) <= self.geometry.cultivation_sites:
            raise ValueError("every MSC resource must start on a C cultivation slot")

    @cached_property
    def gate_index(self) -> dict[str, int]:
        return {g.id: i for i, g in enumerate(self.circuit.gates)}

    @cached_property
    def gates(self) -> dict[str, Gate]:
        return self.circuit.by_id

    @cached_property
    def successors(self) -> dict[str, tuple[str, ...]]:
        return self.circuit.successors

    @cached_property
    def request_bits(self) -> dict[str, int]:
        requests = [g.id for g in self.circuit.gates if g.is_magic]
        return {r: 1 << i for i, r in enumerate(requests)}

    @cached_property
    def t_ancestors(self) -> dict[str, int]:
        """Bitmask (over `request_bits`) of each gate's transitive T-gate ancestors."""
        masks: dict[str, int] = {}
        for gate in self.circuit.gates:
            mask = 0
            for p in gate.predecessors:
                mask |= masks[p] | self.request_bits.get(p, 0)
            masks[gate.id] = mask
        return masks

    def gate_kind(self, gate: Gate) -> str:
        if gate.is_magic:
            return "consume"
        return "single" if len(gate.qubits) == 1 else "two"

    def initial_state(self) -> SystemState:
        durations = {g.id: self.costs.gate_duration(g) for g in self.circuit.gates}
        waiting = {g.id: len(g.predecessors) for g in self.circuit.gates}
        return SystemState(
            placement=dict(self.placement),
            mscs={m: MSC(site) for m, site in sorted(self.sites.items())},
            ready={g for g, count in waiting.items() if count == 0},
            waiting_predecessors=waiting,
            unassigned=list(request_order(self.circuit, durations)),
        )
