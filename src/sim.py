"""Policy-neutral deterministic events; reservations occupy half-open intervals."""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from ir import Gate
from metrics import RunMetrics
from model import (
    GATE_KINDS,
    MSC,
    RELOCATABLE_PHASES,
    Action,
    ActiveOperation,
    Phase,
    Problem,
    Tile,
    contact_rule,
    contacts,
)
from mscmap import SCHEMA_VERSION

PHASE_ON_START = {
    "initialize": Phase.INITIALIZING,
    "cultivate": Phase.CULTIVATING,
    "consume": Phase.CONSUMING,
}


class IllegalAction(ValueError):
    """An operation violates the shared execution model."""


@dataclass
class ExecutionTrace:
    events: list[dict[str, Any]] = field(default_factory=list)


class Simulator:
    """Executes actions on one state.

    `realized=True` is the live run: cultivation outcomes are sampled from the supply
    model. Snapshots are planning copies that only see the declared expectation.
    """

    def __init__(self, problem: Problem, *, realized: bool = True):
        self.problem = problem
        self.realized = realized
        self.state = problem.initial_state()
        self.metrics = RunMetrics()
        self.trace: ExecutionTrace | None = ExecutionTrace() if realized else None
        for request in sorted(self.state.ready):
            if problem.gates[request].is_magic:
                self.metrics.request_ready(request, 0)

    def snapshot(self) -> "Simulator":
        """Planning copy: isolated state and metrics, no trace, no realized outcomes."""
        other = object.__new__(Simulator)
        other.problem, other.realized, other.trace = self.problem, False, None
        other.state = self.state.snapshot()
        other.metrics = deepcopy(self.metrics)
        return other

    def emit(self, event: str, **fields: Any) -> None:
        if self.trace is not None:
            self.trace.events.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "time": self.state.time,
                    "event": event,
                    **fields,
                }
            )

    @property
    def complete(self) -> bool:
        state = self.state
        return (
            len(state.completed) == len(self.problem.circuit.gates)
            and not state.active
            and all(m.phase == Phase.IDLE for m in state.mscs.values())
        )

    # Legality -----------------------------------------------------------------

    def check(self, action: Action) -> None:
        """Raise IllegalAction unless the shared model permits starting now."""
        checker = getattr(self, f"_check_{action.kind}", None)
        self._require(checker is not None, f"unknown operation kind {action.kind!r}")
        self._require(action.msc is None or action.msc in self.state.mscs, "unknown MSC")
        checker(action)
        self._check_contact(action)
        self._require(
            not any(t in self.state.reserved for t in action.footprint),
            "tile reserved by an active operation",
        )

    def can_start(self, action: Action) -> bool:
        try:
            self.check(action)
            return True
        except IllegalAction:
            return False

    @staticmethod
    def _require(condition: bool, message: str) -> None:
        if not condition:
            raise IllegalAction(message)

    def _owner(self, action: Action) -> MSC:
        self._require(action.msc is not None, f"{action.kind} requires an MSC")
        return self.state.mscs[action.msc]

    def _ready_gate(self, action: Action) -> Gate:
        gate = self.problem.gates.get(action.gate)
        self._require(gate is not None, "unknown circuit gate")
        self._require(action.gate in self.state.ready, "gate is not dependency-ready or started")
        self._require(self.problem.gate_kind(gate) == action.kind, "wrong operation for gate")
        return gate

    def _operands(self, gate: Gate) -> tuple[Tile, ...]:
        return tuple(self.state.placement[q] for q in gate.qubits)

    def _check_assign(self, action: Action) -> None:
        owner = self._owner(action)
        self._require(not action.tiles and action.contact is None, "assign uses no tiles")
        self._require(owner.phase == Phase.IDLE, "MSC is not idle")
        self._require(action.gate in self.state.unassigned, "request is not an unassigned T")

    def _check_relocate(self, action: Action) -> None:
        owner, state = self._owner(action), self.state
        source, destination = action.tiles
        self._require(owner.phase in RELOCATABLE_PHASES, "only idle MSCs before init relocate")
        self._require(source == owner.site and destination != source, "invalid relocation")
        self._require(
            destination in self.problem.geometry.cultivation_sites, "destination is not a C slot"
        )
        self._require(
            state.msc_at(destination) is None and destination not in state.data_at,
            "relocation destination must be an empty C slot",
        )

    def _check_initialize(self, action: Action) -> None:
        owner = self._owner(action)
        self._require(owner.phase == Phase.ASSIGNED, "initialization requires an assigned MSC")
        self._require(action.tiles == (owner.site,), "initialize uses only the MSC site")

    def _check_cultivate(self, action: Action) -> None:
        owner = self._owner(action)
        self._require(owner.phase == Phase.INITIALIZED, "cultivation requires initialization")
        self._require(action.tiles == (owner.site,), "cultivate uses only the MSC site")

    def _check_consume(self, action: Action) -> None:
        gate, owner = self._ready_gate(action), self._owner(action)
        self._require(
            owner.phase == Phase.OUTPUT and owner.request == gate.id, "no owned output for request"
        )
        self._require(action.tiles == (*self._operands(gate), owner.site), "wrong consume tiles")

    def _check_single(self, action: Action) -> None:
        gate = self._ready_gate(action)
        self._require(action.msc is None and action.tiles == self._operands(gate), "wrong tiles")

    def _check_two(self, action: Action) -> None:
        self._check_single(action)

    def _check_swap(self, action: Action) -> None:
        self._require(action.msc is None and action.gate is None, "SWAP is data-only")
        self._require(
            len(action.tiles) == 2 and all(t in self.state.data_at for t in action.tiles),
            "SWAP requires two data slots",
        )

    def _check_move(self, action: Action) -> None:
        state = self.state
        self._require(action.msc is None and action.gate is None, "move is data-only")
        self._require(len(action.tiles) == 2, "move needs source and destination")
        source, destination = action.tiles
        self._require(source in state.data_at, "move source holds no data")
        self._require(
            destination in self.problem.geometry.slots
            and destination not in state.data_at
            and state.msc_at(destination) is None,
            "move destination must be an empty slot",
        )

    def _check_contact(self, action: Action) -> None:
        """Two-patch operations need one of the local contacts the shared rule allows."""
        gate = self.problem.gates.get(action.gate) if action.gate else None
        rule = contact_rule(action.kind, gate.kind if gate else None)
        if rule is None:
            self._require(action.contact is None, f"{action.kind} takes no contact")
            return
        legal = contacts(self.problem.geometry, *action.tiles[:2], rule)
        self._require(
            action.contact in legal,
            f"{action.kind} needs a local {rule.boundaries} contact between its patches",
        )

    # Execution ----------------------------------------------------------------

    def start(self, action: Action) -> None:
        self.check(action)
        if action.kind == "assign":
            self._assign(action)
            return
        state = self.state
        duration = self.problem.costs.duration(action)
        operation = ActiveOperation(state.next_operation, action, state.time, state.time + duration)
        state.next_operation += 1
        state.active.append(operation)
        state.reserved.update({t: operation.id for t in action.footprint})
        state.ready.discard(action.gate)
        if action.kind in PHASE_ON_START:
            state.mscs[action.msc].phase = PHASE_ON_START[action.kind]
        self.metrics.start(operation)
        self.emit("start", operation=operation.id, end=operation.end, action=action.to_dict())

    def _assign(self, action: Action) -> None:
        """Zero-duration ownership of a request; reserves no tiles."""
        owner = self.state.mscs[action.msc]
        owner.phase, owner.request = Phase.ASSIGNED, action.gate
        self.state.unassigned.remove(action.gate)
        self.emit("assign", action=action.to_dict())

    def advance(self) -> bool:
        """Apply the next completion time; False when nothing is active."""
        state = self.state
        if not state.active:
            return False
        state.time = min(op.end for op in state.active)
        # Same-time completions apply in operation-ID order, before any new start.
        finishing = sorted((op for op in state.active if op.end == state.time), key=lambda o: o.id)
        for operation in finishing:
            state.active.remove(operation)
            for t in operation.action.footprint:
                del state.reserved[t]
            self._finish(operation)
        return True

    def _finish(self, operation: ActiveOperation) -> None:
        action, state = operation.action, self.state
        details: dict[str, Any] = {}
        if action.kind in GATE_KINDS:
            self._complete_gate(action.gate)
        if action.kind == "consume":
            owner = state.mscs[action.msc]
            owner.phase, owner.request, owner.output = Phase.IDLE, None, None
        elif action.kind == "initialize":
            state.mscs[action.msc].phase = Phase.INITIALIZED
        elif action.kind == "cultivate":
            details["success"] = self._finish_cultivation(action.msc, operation)
        elif action.kind in {"swap", "move"}:
            details["moved"] = self._exchange(*action.tiles)
        elif action.kind == "relocate":
            state.mscs[action.msc].site = action.tiles[1]
        self.emit("finish", operation=operation.id, **details)

    def _complete_gate(self, gate_id: str) -> None:
        state = self.state
        state.completed[gate_id] = state.time
        for successor in self.problem.successors[gate_id]:
            state.waiting_predecessors[successor] -= 1
            if state.waiting_predecessors[successor] == 0:
                state.ready.add(successor)
                if self.problem.gates[successor].is_magic:
                    self.metrics.request_ready(successor, state.time)

    def _finish_cultivation(self, msc_id: str, operation: ActiveOperation) -> bool:
        owner = self.state.mscs[msc_id]
        success = self.problem.supply.succeeds(
            msc_id, owner.generation, owner.attempt, self.realized
        )
        spent = self.problem.costs.initialize + operation.end - operation.start
        self.metrics.cultivation_finished(owner.request, self.state.time, success, spent)
        if success:
            owner.phase, owner.output = Phase.OUTPUT, f"{msc_id}:state:{owner.generation}"
            owner.generation, owner.attempt = owner.generation + 1, 0
        else:
            owner.phase, owner.attempt = Phase.ASSIGNED, owner.attempt + 1
        return success

    def _exchange(self, source: Tile, destination: Tile) -> dict[str, list[int]]:
        """Move data at `source` to `destination` and any data there back to `source`."""
        state = self.state
        arrivals = (
            (state.data_at.get(source), destination),
            (state.data_at.get(destination), source),
        )
        moving = {q: t for q, t in arrivals if q}
        for q in moving:
            del state.data_at[state.placement[q]]
        for q, t in moving.items():
            state.placement[q], state.data_at[t] = t, q
        return {q: list(t) for q, t in sorted(moving.items())}
