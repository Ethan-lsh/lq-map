"""Policy-neutral deterministic events; reservations occupy half-open intervals."""

from copy import deepcopy
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from . import SCHEMA_VERSION
from .ir import Gate
from .metrics import RunMetrics
from .model import (
    MSC,
    Action,
    ActiveOperation,
    Cell,
    Phase,
    Problem,
    RelocationPlan,
    SystemState,
    adjacent,
    patch_resource,
    resources,
)

PRODUCTION_KINDS = {"initialize", "produce"}
MSC_PHASE_ON_START = {
    "initialize": Phase.INITIALIZING,
    "produce": Phase.PRODUCING,
    "consume": Phase.CONSUMING,
}


class IllegalAction(ValueError):
    """An operation violates the abstract execution model."""


@dataclass
class ExecutionTrace:
    events: list[dict[str, Any]] = field(default_factory=list)


class Simulator:
    def __init__(self, problem: Problem, state: SystemState | None = None):
        self.problem = problem
        self.state = state.snapshot() if state is not None else problem.initial_state()
        self.trace = ExecutionTrace()
        self.metrics = RunMetrics.for_problem(problem)

    def snapshot(self) -> "Simulator":
        other = Simulator(self.problem, self.state)
        other.trace.events = self.trace.events.copy()
        other.metrics = deepcopy(self.metrics)
        return other

    def emit(self, event: str, **fields: Any) -> None:
        self.trace.events.append(
            {"schema_version": SCHEMA_VERSION, "time": self.state.time, "event": event, **fields}
        )

    @property
    def complete(self) -> bool:
        state = self.state
        return (
            len(state.completed) == len(self.problem.circuit.gates)
            and not state.active
            and not state.transactions
            and all(m.phase == Phase.IDLE for m in state.mscs.values())
        )

    def check(self, action: Action) -> None:
        """Raise IllegalAction unless the abstract model permits starting now."""
        self._require(action.msc is None or action.msc in self.state.mscs, "unknown MSC")
        if action.kind == "begin":
            self._check_begin(action)
            return
        self._check_operation_shape(action)
        if action.kind == "swap":
            self._check_swap(action)
        elif action.kind in PRODUCTION_KINDS:
            self._check_production(action)
        else:
            self._check_circuit_gate(action)

    def can_start(self, action: Action) -> bool:
        try:
            self.check(action)
            return True
        except IllegalAction:
            return False

    def _require(self, condition: bool, message: str) -> None:
        if not condition:
            raise IllegalAction(message)

    def _owner(self, action: Action) -> MSC | None:
        return self.state.mscs.get(action.msc)

    def _check_begin(self, action: Action) -> None:
        state, problem = self.state, self.problem
        owner = self._owner(action)
        self._require(not action.patches, "begin uses a path, not operation patches")
        self._require(owner is not None and owner.phase == Phase.IDLE, "MSC is not idle")
        gate = problem.circuit.by_id.get(action.gate)
        self._require(gate is not None and gate.kind == "T", "assignment requires a T request")
        self._require(
            action.gate not in state.completed and action.gate not in state.assignments,
            "request already completed or assigned",
        )
        self._check_relocation_path(action, owner)
        self._check_relocation_mode(action)

    def _check_relocation_path(self, action: Action, owner: MSC) -> None:
        state, path = self.state, action.path
        self._require(bool(path) and path[0] == owner.site, "path must start at the idle MSC")
        self._require(set(path) <= self.problem.layout.usable, "path leaves usable grid")
        self._require(
            all(adjacent(step, following) for step, following in pairwise(path)),
            "relocation path must use cardinal edges",
        )
        self._require(
            not any(m.site == path[-1] for other, m in state.mscs.items() if other != action.msc),
            "destination is owned by another MSC",
        )
        self._require(
            not any(t.destination == path[-1] for t in state.transactions.values()),
            "destination already claimed",
        )

    def _check_relocation_mode(self, action: Action) -> None:
        state, mode = self.state, self.problem.mode
        if mode == "serialized":
            self._require(not state.transactions, "global relocation mutex is held")
        if mode == "disjoint_paths":
            reserved = {patch for t in state.transactions.values() for patch in t.path}
            self._require(not set(action.path) & reserved, "complete relocation paths overlap")
            self._require(
                not {patch_resource(p) for p in action.path} & state.reservations,
                "path conflicts with active operation",
            )

    def _check_operation_shape(self, action: Action) -> None:
        """Requirements shared by every non-assignment operation."""
        state = self.state
        self._require(action.kind in self.problem.costs.specs, "unknown operation kind")
        self._require(not action.path, "only begin accepts a path")
        self._require(
            bool(action.patches) and set(action.patches) <= self.problem.layout.usable,
            "operation patches must be usable",
        )
        self._require(len(action.patches) == len(set(action.patches)), "repeated operation patch")
        self._require(not resources(action) & state.reservations, "active resource conflict")
        if self.problem.mode == "disjoint_paths":
            for msc_id, transaction in state.transactions.items():
                if msc_id != action.msc:
                    self._require(
                        not set(action.patches) & transaction.affected, "reserved complete path"
                    )

    def _check_swap(self, action: Action) -> None:
        self._require(action.gate is None, "SWAP cannot complete a circuit gate")
        self._require(
            len(action.patches) == 2 and adjacent(*action.patches), "SWAP must be cardinal"
        )
        owner = self._owner(action)
        if owner is None:
            self._require(
                all(p in self.state.reverse for p in action.patches),
                "data SWAP requires two data patches",
            )
        else:
            self._check_relocation_step(action, owner)

    def _check_relocation_step(self, action: Action, owner: MSC) -> None:
        state = self.state
        transaction = state.transactions.get(action.msc)
        self._require(
            owner.phase == Phase.PREPARING and transaction is not None,
            "no idle relocation transaction",
        )
        self._require(transaction.index + 1 < len(transaction.path), "relocation path exhausted")
        self._require(
            action.patches == transaction.path[transaction.index : transaction.index + 2],
            "SWAP is not next transaction step",
        )
        self._require(
            action.patches[0] == owner.site and action.patches[1] in state.reverse,
            "idle exchange requires owned MSC and data",
        )
        self._require(
            not any(
                t.destination == action.patches[1]
                for other, t in state.transactions.items()
                if other != action.msc
            ),
            "another transaction owns destination",
        )

    def _check_production(self, action: Action) -> None:
        owner = self._owner(action)
        self._require(
            action.gate is None and owner is not None and action.patches == (owner.site,),
            "production operation requires only the owned MSC site",
        )
        if action.kind == "initialize":
            transaction = self.state.transactions.get(action.msc)
            self._require(
                transaction is not None
                and owner.phase == Phase.PREPARING
                and transaction.index == len(transaction.path) - 1,
                "initialization requires completed evacuation",
            )
        else:
            self._require(owner.phase == Phase.INITIALIZED, "production requires initialization")

    def _check_circuit_gate(self, action: Action) -> None:
        state = self.state
        gate = self.problem.circuit.by_id.get(action.gate)
        self._require(gate is not None, "unknown circuit gate")
        self._require(
            gate.id not in state.completed
            and not any(op.action.gate == gate.id for op in state.active),
            "gate already completed or active",
        )
        self._require(
            gate.predecessors <= state.completed.keys(), "gate predecessors have not completed"
        )
        operands = tuple(state.placement[q] for q in gate.qubits)
        if action.kind == "consume":
            self._check_consumption(action, gate, operands)
        else:
            self._check_data_gate(action, gate, operands)

    def _check_consumption(self, action: Action, gate: Gate, operands: tuple[Cell, ...]) -> None:
        owner = self._owner(action)
        self._require(
            gate.kind == "T"
            and owner is not None
            and owner.phase == Phase.OUTPUT
            and owner.output is not None
            and owner.request == gate.id,
            "no owned output for request",
        )
        self._require(
            action.patches == (owner.site, operands[0]) and adjacent(*action.patches),
            "consumption requires output-data adjacency",
        )

    def _check_data_gate(self, action: Action, gate: Gate, operands: tuple[Cell, ...]) -> None:
        self._require(
            self._owner(action) is None and gate.kind != "T",
            "data gates cannot use MSC or replace T",
        )
        self._require(action.patches == operands, "operation patches do not match logical operands")
        self._require(
            (action.kind == "single" and len(operands) == 1)
            or (action.kind == "two" and len(operands) == 2 and adjacent(*operands)),
            "ordinary gate has incorrect arity or nonlocal operands",
        )

    def start(self, action: Action) -> None:
        self.check(action)
        if action.kind == "begin":
            self._begin_transaction(action)
            return
        self._start_operation(action)

    def _begin_transaction(self, action: Action) -> None:
        state = self.state
        owner = state.mscs[action.msc]
        owner.request, owner.phase = action.gate, Phase.PREPARING
        state.transactions[action.msc] = RelocationPlan(
            action.msc, action.gate, action.path, state.time
        )
        state.relocations += int(action.path[0] != action.path[-1])
        self.emit("begin", action=action.to_dict())

    def _start_operation(self, action: Action) -> None:
        state = self.state
        duration = self.problem.costs.specs[action.kind].duration
        operation = ActiveOperation(
            state.next_operation, action, state.time, state.time + duration, resources(action)
        )
        state.next_operation += 1
        state.active.append(operation)
        self.metrics.start(operation)
        if action.msc is not None and action.kind in MSC_PHASE_ON_START:
            state.mscs[action.msc].phase = MSC_PHASE_ON_START[action.kind]
        self.emit(
            "start",
            operation=operation.id,
            end=operation.end,
            action=action.to_dict(),
            resources=sorted(operation.resources),
        )

    def advance(self, until: int | None = None) -> bool:
        state = self.state
        if until is None:
            if not state.active:
                return False
            until = self._next_completion()
        if until < state.time:
            raise ValueError("cannot reverse simulation time")
        while state.active and self._next_completion() <= until:
            self._elapse_to(self._next_completion())
            for operation in self._finishing_operations():
                self._finish(operation)
                state.active.remove(operation)
        self._elapse_to(until)
        return True

    def _next_completion(self) -> int:
        return min(op.end for op in self.state.active)

    def _elapse_to(self, moment: int) -> None:
        self.metrics.elapse(self.problem, self.state, moment - self.state.time)
        self.state.time = moment

    def _finishing_operations(self) -> list[ActiveOperation]:
        """Same-time completions apply in operation-ID order, before any new start."""
        return sorted(
            (op for op in self.state.active if op.end == self.state.time), key=lambda op: op.id
        )

    def _finish(self, operation: ActiveOperation) -> None:
        action = operation.action
        if action.kind == "swap":
            self._apply_swap(action)
        elif action.kind in PRODUCTION_KINDS:
            self._apply_production(action)
        elif action.kind == "consume":
            self._apply_consumption(action)
        else:
            self.state.completed[action.gate] = self.state.time
        self.metrics.finish(operation)
        self.emit("finish", operation=operation.id, **self._visible_state())

    def _apply_swap(self, action: Action) -> None:
        state = self.state
        vacated, entered = action.patches
        occupant = state.reverse
        owner = state.mscs.get(action.msc)
        if owner is None:
            state.placement[occupant[vacated]] = entered
            state.placement[occupant[entered]] = vacated
        else:
            state.placement[occupant[entered]] = vacated
            owner.site = entered
            state.transactions[action.msc].index += 1
        state.swaps += 1

    def _apply_production(self, action: Action) -> None:
        state = self.state
        owner = state.mscs[action.msc]
        if action.kind == "initialize":
            owner.phase = Phase.INITIALIZED
            del state.transactions[action.msc]
        else:
            owner.phase = Phase.OUTPUT
            owner.generation += 1
            owner.output = f"{action.msc}:state:{owner.generation}"

    def _apply_consumption(self, action: Action) -> None:
        state = self.state
        state.completed[action.gate] = state.time
        owner = state.mscs[action.msc]
        owner.phase, owner.request, owner.output = Phase.IDLE, None, None

    def _visible_state(self) -> dict[str, Any]:
        state = self.state
        return {
            "placement": {q: list(p) for q, p in sorted(state.placement.items())},
            "mscs": {
                msc_id: {
                    "site": list(m.site),
                    "phase": m.phase.value,
                    "request": m.request,
                    "output": m.output,
                }
                for msc_id, m in sorted(state.mscs.items())
            },
        }

    def signature(self) -> tuple:
        """Future-relevant state; operation IDs and past trace are deliberately omitted."""
        state = self.state
        return (
            state.time,
            tuple(sorted(state.placement.items())),
            tuple(
                (msc_id, m.site, m.phase.value, m.request, m.output, m.generation)
                for msc_id, m in sorted(state.mscs.items())
            ),
            tuple(sorted(state.completed)),
            tuple(sorted((op.end, repr(op.action)) for op in state.active)),
            tuple((msc_id, t.path, t.index) for msc_id, t in sorted(state.transactions.items())),
        )
