"""Collect experiment measurements directly from execution events, without replay."""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from .model import ActiveOperation, Cell, Phase, Problem, SystemState, adjacent, patch_resource


@dataclass
class RunMetrics:
    waits: dict[str, dict[str, int]]
    layers: dict[Cell, int] = field(default_factory=dict)
    pending_layers: dict[int, int] = field(default_factory=dict)
    swap_depth: int = 0

    @classmethod
    def for_problem(cls, problem: Problem) -> "RunMetrics":
        return cls(
            {
                g.id: {"total": 0, "supply": 0, "adjacency": 0, "resource": 0}
                for g in problem.circuit.gates
                if g.kind == "T"
            }
        )

    def start(self, operation: ActiveOperation) -> None:
        if operation.action.kind == "swap":
            patches = operation.action.patches
            layer = 1 + max(self.layers.get(p, 0) for p in patches)
            self.pending_layers[operation.id] = layer
            self.layers.update({p: layer for p in patches})

    def finish(self, operation: ActiveOperation) -> None:
        if operation.action.kind == "swap":
            self.swap_depth = max(self.swap_depth, self.pending_layers.pop(operation.id))

    def elapse(self, problem: Problem, state: SystemState, duration: int) -> None:
        if duration == 0:
            return
        active_gates = {op.action.gate for op in state.active}
        supply = {m.request: m for m in state.mscs.values() if m.request is not None}
        busy = state.reservations
        for gate in problem.circuit.gates:
            if (
                gate.kind != "T"
                or gate.id in state.completed
                or gate.id in active_gates
                or not gate.predecessors <= state.completed.keys()
            ):
                continue
            wait = self.waits[gate.id]
            wait["total"] += duration
            msc = supply.get(gate.id)
            if msc is None or msc.phase != Phase.OUTPUT:
                wait["supply"] += duration
            target = state.placement[gate.qubits[0]]
            if msc is not None and not adjacent(msc.site, target):
                wait["adjacency"] += duration
            involved = {target} | ({msc.site} if msc else set())
            path_block = problem.mode == "disjoint_paths" and any(
                involved & transaction.affected for transaction in state.transactions.values()
            )
            if {patch_resource(p) for p in involved} & busy or path_block:
                wait["resource"] += duration

    def summary(self, problem: Problem, state: SystemState, complete: bool) -> dict[str, Any]:
        return {
            "metrics_version": "online-v1",
            "swap_count": state.swaps,
            "swap_depth": self.swap_depth,
            "makespan": state.time,
            "circuit_completed": complete,
            "relocation_count": state.relocations,
            "completed_gates": len(state.completed),
            "request_waits": deepcopy(self.waits),
            "total_request_wait": sum(w["total"] for w in self.waits.values()),
            "supply_wait": sum(w["supply"] for w in self.waits.values()),
        }
