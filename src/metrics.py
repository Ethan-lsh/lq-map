"""Measurements accumulated directly from simulator events, without trace replay.

Times are lcycles and volumes are tile-lcycles; `*_us` fields convert lcycles with the
problem's `TimeScale`. `spacetime_volume` is N x L as in PureMagic's volume estimate:
N provisioned tiles (S + C + B) times L = `execution_lcycles`, the lcycles from time 0
until the last operation finishes. Operation counts and volumes are charged when an operation
starts (its duration is fixed then); cultivation outcomes are charged on completion.
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from model import (
    CULTIVATION_KINDS,
    GATE_KINDS,
    MOVEMENT_KINDS,
    ActiveOperation,
    Objective,
    Problem,
    SystemState,
    Tile,
)

METRICS_VERSION = "online-v6"


@dataclass
class RunMetrics:
    counts: Counter = field(default_factory=Counter)
    cycles: Counter = field(default_factory=Counter)
    volume: Counter = field(default_factory=Counter)
    ancilla_cycles: int = 0
    direct_contacts: int = 0
    failed_attempts: int = 0
    retry_cycles: int = 0
    exchange_depth: int = 0
    layers: dict[Tile, int] = field(default_factory=dict)  # exchange layer per slot
    ready_at: dict[str, int] = field(default_factory=dict)
    output_at: dict[str, int] = field(default_factory=dict)
    consumed_at: dict[str, int] = field(default_factory=dict)

    def request_ready(self, request: str, time: int) -> None:
        self.ready_at[request] = time

    def start(self, operation: ActiveOperation) -> None:
        action = operation.action
        duration = operation.end - operation.start
        ancilla = len(action.contact.ancilla) if action.contact is not None else 0
        self.counts[action.kind] += 1
        self.cycles[action.kind] += duration
        self.ancilla_cycles += ancilla * duration
        if action.kind in GATE_KINDS:
            self.volume["gate"] += len(action.tiles) * duration
            self.volume["ancilla"] += ancilla * duration
            self.direct_contacts += action.contact is not None and not ancilla
        elif action.kind in MOVEMENT_KINDS:
            self.volume["movement"] += len(action.footprint) * duration
        elif action.kind in CULTIVATION_KINDS:
            self.volume["cultivation"] += duration
        if action.kind == "consume":
            self.consumed_at[action.gate] = operation.start
        if action.kind == "exchange":
            layer = 1 + max(self.layers.get(t, 0) for t in action.tiles)
            self.layers.update({t: layer for t in action.tiles})
            self.exchange_depth = max(self.exchange_depth, layer)

    def retime_cultivation(self, realized: ActiveOperation, planned: ActiveOperation) -> None:
        """Replace an active cultivation's sampled duration by the planning estimate."""
        change = (planned.end - planned.start) - (realized.end - realized.start)
        self.cycles["cultivate"] += change
        self.volume["cultivation"] += change

    def cultivation_finished(self, request: str, time: int, success: bool, spent: int) -> None:
        if success:
            self.output_at[request] = time
        else:
            self.failed_attempts += 1
            self.retry_cycles += spent

    def request_wait(self, time: int) -> int:
        """Summed readiness-to-consumption-start wait; unconsumed requests count to `time`."""
        return sum(self.consumed_at.get(r, time) - ready for r, ready in self.ready_at.items())

    def objective(self, weights: Objective, time: int) -> float:
        return float(
            weights.movement_volume * self.volume["movement"]
            + weights.operation_volume * (self.volume["gate"] + self.volume["ancilla"])
            + weights.cultivation_volume * self.volume["cultivation"]
            + weights.request_wait * self.request_wait(time)
            + weights.execution_lcycles * time
        )

    def summary(self, problem: Problem, state: SystemState, complete: bool) -> dict[str, Any]:
        horizon = state.time
        waits = self._request_waits(horizon)
        holding = sum(
            self.consumed_at.get(r, horizon) - produced for r, produced in self.output_at.items()
        )
        area = problem.geometry.area
        pool = len(problem.sites)
        pool_cycles = pool * horizon
        cultivation = self.cycles["initialize"] + self.cycles["cultivate"]
        busy_msc = cultivation + holding + self.cycles["consume"] + self.cycles["relocate"]
        return {
            "metrics_version": METRICS_VERSION,
            "circuit_completed": complete,
            "completed_gates": len(state.completed),
            "execution_lcycles": horizon,
            "time_unit": "lcycle",
            "code_distance": problem.time_scale.code_distance,
            "lcycle_us": problem.time_scale.lcycle_us,
            "execution_us": horizon * problem.time_scale.lcycle_us,
            "objective": self.objective(problem.objective, horizon),
            "operation_counts": dict(sorted(self.counts.items())),
            "operation_cycles": dict(sorted(self.cycles.items())),
            "exchange_count": self.counts["exchange"],
            "relocation_count": self.counts["relocate"],
            "exchange_depth": self.exchange_depth,
            "movement_cycles": self.cycles["exchange"],
            "movement_volume": self.volume["movement"],
            "gate_volume": self.volume["gate"] + self.volume["ancilla"],
            "cultivation_volume": self.volume["cultivation"],
            "operation_volume": {**dict(sorted(self.volume.items())), "holding": holding},
            "ancilla_cycles": self.ancilla_cycles,
            "direct_contacts": self.direct_contacts,
            "provisioned_area": area["total"],
            "area_by_kind": area,
            "msc_pool_size": pool,
            "spacetime_volume": area["total"] * horizon,
            "spacetime_volume_us": area["total"] * horizon * problem.time_scale.lcycle_us,
            "data_resident_volume": len(problem.placement) * horizon,
            "idle_msc_cycles": pool_cycles - busy_msc,
            "msc_utilization": cultivation / pool_cycles if pool_cycles else 0.0,
            "msc_busy_fraction": busy_msc / pool_cycles if pool_cycles else 0.0,
            "cultivation_cycles": cultivation,
            "relocation_cycles": self.cycles["relocate"],
            "output_holding_cycles": holding,
            "failed_attempts": self.failed_attempts,
            "retry_cycles": self.retry_cycles,
            "request_waits": waits,
            "total_request_wait": sum(w["total"] for w in waits.values()),
            "supply_wait": sum(w["supply"] for w in waits.values()),
        }

    def _request_waits(self, horizon: int) -> dict[str, dict[str, int]]:
        """Readiness to consumption start, split at output availability (no overlap)."""
        waits = {}
        for request, ready in sorted(self.ready_at.items()):
            end = self.consumed_at.get(request, horizon)
            available = min(max(self.output_at.get(request, horizon), ready), end)
            waits[request] = {
                "total": end - ready,
                "supply": available - ready,
                "access": end - available,
            }
        return waits
