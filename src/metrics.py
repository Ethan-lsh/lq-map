"""Measurements accumulated directly from simulator events, without trace replay.

Times are lcycles and volumes are tile-lcycles; `*_us` fields convert lcycles with the
problem's `TimeScale`. `spacetime_volume` is N x L as in PureMagic's volume estimate:
N provisioned tiles (S + C + B) times L = `execution_lcycles`, the lcycles from time 0
until the last operation finishes. Operation counts and volumes are charged when an operation
starts (its duration is fixed then); cultivation outcomes are charged on completion.
`active_series` (cells engaged per lcycle, by layer) is counted from the realized event
log after the run; it never re-executes the schedule.
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

METRICS_VERSION = "online-v7"

# Active-cell layers of the per-lcycle series (`active_series`): cells engaged by an
# operation at lcycle t. Idle data patches are not active (see data_resident_volume).
ACTIVE_LAYERS = ("data", "aux", "cultivating", "holding", "consume")


def active_series(events: list[dict[str, Any]], horizon: int) -> dict[str, list[list[int]]]:
    """Per-lcycle active cells by layer, from the realized event log (interval counting
    over `start` events; no replay). Each layer is run-length encoded as [value, length]
    pairs covering lcycles 0..horizon-1.

    data: operand slots of gates and T consumption, and both slots of an exchange (the
    destination may be an empty slot; both are reserved). aux: ancilla/junction tiles of
    gates and exchanges, and both C slots of a relocation. cultivating: an MSC site under
    initialize/cultivate. holding: an MSC site from a successful cultivation's end to the
    consumption start. consume: the MSC site during consumption.
    """
    deltas = {layer: [0] * (horizon + 1) for layer in ACTIVE_LAYERS}

    def add(layer: str, start: int, end: int, count: int = 1) -> None:
        end = min(end, horizon)
        if count and start < end:
            deltas[layer][start] += count
            deltas[layer][end] -= count

    cultivated: dict[str, int] = {}
    for event in events:
        if event["event"] != "start":
            continue
        action, start, end = event["action"], event["time"], event["end"]
        kind = action["kind"]
        ancilla = len(action["contact"]["ancilla"]) if action["contact"] else 0
        add("aux", start, end, ancilla)
        if kind in ("single", "two", "exchange"):
            add("data", start, end, len(action["tiles"]))
        elif kind == "relocate":
            add("aux", start, end, len(action["tiles"]))
        elif kind in CULTIVATION_KINDS:
            add("cultivating", start, end)
            if kind == "cultivate":
                cultivated[action["msc"]] = end
        elif kind == "consume":
            add("data", start, end)
            add("consume", start, end)
            add("holding", cultivated[action["msc"]], start)
    series = {}
    for layer, delta in deltas.items():
        encoded: list[list[int]] = []
        level = 0
        for t in range(horizon):
            level += delta[t]
            if encoded and encoded[-1][0] == level:
                encoded[-1][1] += 1
            else:
                encoded.append([level, 1])
        series[layer] = encoded
    return series


def decode_series(encoded: list[list[int]]) -> list[int]:
    """Expand a run-length encoded layer to one value per lcycle."""
    return [value for value, length in encoded for _ in range(length)]


def active_metrics(events: list[dict[str, Any]], horizon: int, area: int) -> dict[str, Any]:
    """`active_series` plus its totals: volume per layer and overall (cell-lcycles),
    mean and peak active cells, and utilization = active volume / (N x L)."""
    series = active_series(events, horizon)
    layers = {layer: decode_series(encoded) for layer, encoded in series.items()}
    total = [sum(layers[layer][t] for layer in ACTIVE_LAYERS) for t in range(horizon)]
    volume = sum(total)
    return {
        "active_series": series,
        "active_layers": list(ACTIVE_LAYERS),
        "active_volume_by_layer": {layer: sum(values) for layer, values in layers.items()},
        "active_volume": volume,
        "mean_active_tiles": volume / horizon if horizon else 0.0,
        "peak_active_tiles": max(total, default=0),
        "active_utilization": volume / (area * horizon) if horizon else 0.0,
    }


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
