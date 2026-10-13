"""Configuration documents: loading, validation, and resolution into a `Problem`.

`resolve` turns a raw configuration (JSON-compatible YAML) into the validated problem
and the resolved configuration that reproduces it, including the recorded modeling
assumptions. Variants are expanded by the execution engine.
"""

import json
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

from algorithm.circuit import DEPENDENCY_MODES, CircuitDAG, pauli_frame
from algorithm.mapping import initial_placement
from algorithm.model import (
    BOUNDARY_SIDES,
    OPERATION_CONTRACTS,
    TILE_KINDS,
    Geometry,
    Objective,
    OperationCosts,
    Problem,
    SupplyModel,
    Tile,
    TimeScale,
    tile,
)
from algorithm.policies import PolicySettings, make_policy
from config.circuits import load_qasm
from config.workload import workload
from versions import MODEL_VERSION, SCHEMA_VERSION

ASSUMPTIONS = {
    "model": MODEL_VERSION,
    "tiles": TILE_KINDS,
    "orientation": {b: list(s) for b, s in BOUNDARY_SIDES.items() if b != "any"},
    "operations": {
        k: dict(zip(("requires", "reserves", "effect"), v)) for k, v in OPERATION_CONTRACTS.items()
    },
    "execution": "no routing paths or remote operations: two-patch operations need a local "
    "contact (direct or one shared ancilla tile); distant operands are brought into contact "
    "by exchange (SWAP) sequences",
    "exchange": "one abstract operation for SWAP and for a move into an empty slot: in-line "
    "cardinal neighbors with one auxiliary bus tile, one duration (costs.exchange); the "
    "physical SWAP protocol is not specified",
    "msc_operation": "an MSC operates at any C slot; M limits concurrent resources; "
    "relocation needs an empty C slot and transfers no state",
    "supply": "supply.distribution=bernoulli (default): attempts succeed with p; failure "
    "repeats initialize+cultivate; planning uses ceil(1/p) attempts. exponential "
    "(PureMagic, arXiv:2512.06484 Sec. III-C): each state's production time is an "
    "exponential sample with rate lambda per lcycle rounded to the nearest integer (0: "
    "ready at once), initialize takes 0 lcycles and costs.initialize/cultivate are unused; "
    "planning assumes round(1/lambda) lcycles (heuristic). Unlike PureMagic, cultivation is "
    "owned by one request and is never interrupted. Planning never reads realized outcomes",
    "timing": "integer logical cycles (lcycles); gate defaults follow PureMagic's lcycle "
    "accounting (CX 2, S/SDG 3, T consume 1); H/X/Z/CZ, cultivation, exchange, and "
    "relocation durations are modeling assumptions; half-open reservations; finishes by "
    "operation ID before starts",
    "time_scale": "physical cycle 1 us by default; a logical cycle takes at least "
    "code_distance physical cycles and the model uses that minimum (modeling choice), so "
    "code_distance 17 gives 17 us per lcycle; durations are not rescaled by code_distance",
    "objective": "weighted movement_volume + operation_volume + cultivation_volume "
    "+ request_wait + execution_lcycles (lcycles from time 0 until the last operation "
    "finishes)",
    "dependencies": "circuit_model.dependencies=commutation (default): on a shared qubit, "
    "gates commute when both act as functions of Z (T, TDG, S, SDG, Z, CZ, CX control) or "
    "of X (X, CX target); H commutes with neither; program_order keeps input order per "
    "qubit",
    "pauli_frame": "circuit_model.pauli_frame=true (default, modeling choice): X and Z are "
    "tracked classically, not executed; the frame is conjugated through Cliffords, an X "
    "component at T/TDG swaps it for its inverse, and the residual frame corrects final "
    "measurement outcomes",
    "base_rule": "each ready gate (input order) executes in contact or starts the next "
    "exchange of its fewest-exchange plan whose two operand paths are slot-disjoint; both "
    "operands advance together; operands of earlier ready gates are protected and never "
    "move for later gates",
    "magic_gates": "T and T-dagger each consume one magic state; the Clifford difference "
    "is folded into consumption",
    "circuit_import": "OpenQASM files are parsed and decomposed into Clifford+T by Qiskit "
    "(optimization level 1, final measurements removed, barriers ignored); rotation "
    "synthesis precision sets the T count; the resolved config stores the converted gates",
    "spacetime_volume": "N x L: provisioned tiles (S + C + B, blocked excluded) times the "
    "realized execution_lcycles, including SWAP, supply, and resource waits; "
    "spacetime_volume_us multiplies by lcycle_us",
    "waits": "T dependency readiness to consumption start; supply then access",
    "active_series": "cells engaged by an operation at each lcycle, by layer: data "
    "(operand slots of gates and T consumption, both slots of an exchange), aux "
    "(ancilla/junction tiles, both C slots of a relocation), cultivating (MSC site under "
    "initialize/cultivate), holding (MSC site from a successful cultivation's end to the "
    "consumption start), consume (MSC site during consumption); idle data patches are not "
    "active; active_volume sums all layers over lcycles and active_utilization divides it "
    "by N x L",
    "claim": "abstract scheduling model, not a physically verified protocol",
}

TOP_LEVEL_KEYS = {
    "schema_version",
    "model_version",
    "seed",
    "sample",
    "geometry",
    "placement",
    "mscs",
    "costs",
    "timing",
    "circuit_model",
    "supply",
    "objective",
    "mapping",
    "circuit",
    "workload",
    "policy",
    "settings",
    "variants",
    "assumptions",
    "environment",
    "supplied_placement",
    "supplied_mscs",
}
VARIANT_KEYS = {"name", "policy", "settings", "mapping"}
CIRCUIT_MODEL_DEFAULTS = {"dependencies": "commutation", "pauli_frame": True}
SUPPLY_KEYS = {
    "bernoulli": ("success_probability", "planning_attempts"),
    "exponential": ("rate", "planning_cultivation"),
}


def load_document(path: Path) -> dict[str, Any]:
    text = path.read_text()
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml
        except ImportError as exc:
            raise ValueError(
                "use JSON-compatible YAML or install mscmap[yaml] for general YAML"
            ) from exc
        raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise ValueError("configuration must be an object")
    return raw


def resolve(raw: dict, base: Path = Path("."), sample: int = 0) -> tuple[Problem, dict]:
    """Validated problem and the resolved configuration that reproduces it."""
    _check_config_fields(raw)
    geometry = _geometry(raw)
    # Rerunning a resolved config starts again from its supplied placement.
    supplied = {q: tile(v) for q, v in raw.get("supplied_placement", raw["placement"]).items()}
    supplied_sites = {m: tile(v) for m, v in raw.get("supplied_mscs", raw["mscs"]).items()}
    seed = raw.get("seed", 0)
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    circuit_model = _circuit_model(raw.get("circuit_model", {}))
    circuit, frame = _circuit(raw, base, supplied, seed, circuit_model)
    costs = _section(OperationCosts, raw, "costs")
    time_scale = _time_scale(raw.get("timing", {}))
    supply = _supply(raw.get("supply", {}), seed, sample)
    if supply.deterministic and sample != 0:
        raise ValueError("deterministic supply has a single sample; use --samples 1")
    mapping = raw.get("mapping", "supplied")
    placement, sites = initial_placement(mapping, circuit, geometry, supplied, supplied_sites)
    problem = Problem(
        circuit,
        geometry,
        placement,
        sites,
        costs,
        supply,
        _section(Objective, raw, "objective"),
        time_scale,
    )
    settings = _section(PolicySettings, raw, "settings")
    policy = raw.get("policy", "JointScheduler")
    make_policy(policy, settings)  # validates the policy name
    resolved = {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "seed": seed,
        "sample": sample,
        "geometry": {"tiles": list(geometry.rows)},
        "mapping": mapping,
        "supplied_placement": _tiles_dict(supplied),
        "supplied_mscs": _tiles_dict(supplied_sites),
        "placement": _tiles_dict(placement),
        "mscs": _tiles_dict(sites),
        "circuit": circuit.to_dict(),
        "circuit_model": {**{k: circuit_model[k] for k in CIRCUIT_MODEL_DEFAULTS}, "frame": frame},
        "costs": asdict(costs),
        "timing": {**asdict(time_scale), "unit": "lcycle", "lcycle_us": time_scale.lcycle_us},
        "supply": _resolved_supply(supply),
        "objective": asdict(problem.objective),
        "policy": policy,
        "settings": asdict(settings),
        "assumptions": ASSUMPTIONS,
    }
    return problem, resolved


def _check_config_fields(raw: dict) -> None:
    """Reject unknown or version-mismatched configuration fields."""
    if set(raw) - TOP_LEVEL_KEYS:
        raise ValueError(f"unsupported configuration fields: {sorted(set(raw) - TOP_LEVEL_KEYS)}")
    if raw.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise ValueError(f"configuration schema_version must be {SCHEMA_VERSION}")
    if raw.get("model_version", MODEL_VERSION) != MODEL_VERSION:
        raise ValueError(f"model_version must be {MODEL_VERSION}")


def _section(cls: type, raw: dict, name: str):
    """Dataclass section `name` of the configuration; unknown fields are rejected."""
    values = raw.get(name, {})
    allowed = {f.name for f in fields(cls)}
    if not isinstance(values, dict) or set(values) - allowed:
        raise ValueError(f"{name} accepts only {', '.join(sorted(allowed))}")
    return cls(**values)


def _time_scale(raw: dict) -> TimeScale:
    """`code_distance` and `physical_cycle_us`; derived fields of a resolved config are
    accepted only when they match."""
    if not isinstance(raw, dict):
        raise ValueError("timing must be an object")
    given = {k: v for k, v in raw.items() if k not in {"unit", "lcycle_us"}}
    scale = _section(TimeScale, {"timing": given}, "timing")
    if raw.get("unit", "lcycle") != "lcycle" or raw.get("lcycle_us", scale.lcycle_us) != (
        scale.lcycle_us
    ):
        raise ValueError("timing.unit must be lcycle and lcycle_us must match the distance")
    return scale


def _geometry(raw: dict) -> Geometry:
    if set(raw.get("geometry", {})) != {"tiles"}:
        raise ValueError("geometry must contain exactly `tiles`: rows of S/C/B/. characters")
    return Geometry(tuple(raw["geometry"]["tiles"]))


def _circuit_model(raw: dict) -> dict:
    """Dependency rule and Pauli-frame tracking; `frame` is derived and accepted only
    when rerunning a resolved config."""
    if not isinstance(raw, dict) or set(raw) - {*CIRCUIT_MODEL_DEFAULTS, "frame"}:
        raise ValueError("circuit_model accepts dependencies and pauli_frame")
    model = {**CIRCUIT_MODEL_DEFAULTS, **{k: v for k, v in raw.items() if k != "frame"}}
    if model["dependencies"] not in DEPENDENCY_MODES:
        raise ValueError(f"circuit_model.dependencies must be one of {DEPENDENCY_MODES}")
    if type(model["pauli_frame"]) is not bool:
        raise ValueError("circuit_model.pauli_frame must be boolean")
    return {**model, "previous_frame": raw.get("frame")}


def _circuit(
    raw: dict, base: Path, supplied: dict[str, Tile], seed: int, model: dict
) -> tuple[CircuitDAG, dict | None]:
    """An inline circuit, a JSON or OpenQASM (via Qiskit) file, or a workload generated
    against the supplied placement. The resolved config stores the converted circuit,
    after Pauli-frame tracking, and the frame record."""
    if ("circuit" in raw) == ("workload" in raw):
        raise ValueError("provide exactly one of circuit or workload")
    circuit_raw = raw.get("circuit")
    if isinstance(circuit_raw, str):
        path = base / circuit_raw
        circuit_raw = load_qasm(path) if path.suffix == ".qasm" else load_document(path)
    if circuit_raw is None:
        circuit_raw = workload(raw["workload"], supplied, seed)
    frame = None
    if model["pauli_frame"]:
        circuit_raw, record = pauli_frame(circuit_raw)
        # A resolved circuit is already reduced; keep the record of the original import.
        frame = (
            record.to_dict()
            if record.removed or not model["previous_frame"]
            else (model["previous_frame"])
        )
    return CircuitDAG.from_dict(circuit_raw, model["dependencies"]), frame


def _supply(raw: dict, seed: int, sample: int) -> SupplyModel:
    """Planning estimates are derived; they are accepted only when rerunning a resolved
    config."""
    distribution = raw.get("distribution", "bernoulli")
    if distribution not in SUPPLY_KEYS:
        raise ValueError(f"supply.distribution must be one of {sorted(SUPPLY_KEYS)}")
    given, derived = SUPPLY_KEYS[distribution]
    if set(raw) - {"distribution", given, derived}:
        raise ValueError(
            f"{distribution} supply accepts distribution and {given}; "
            "seeds come from seed and sample"
        )
    params = {given: raw[given]} if given in raw else {}
    supply = SupplyModel(distribution, **params, seed=seed, sample=sample)
    if raw.get(derived, getattr(supply, derived)) != getattr(supply, derived):
        raise ValueError(f"supply.{derived} is derived from {given}")
    return supply


def _resolved_supply(supply: SupplyModel) -> dict:
    given, derived = SUPPLY_KEYS[supply.distribution]
    return {
        "distribution": supply.distribution,
        given: getattr(supply, given),
        derived: getattr(supply, derived),
    }


def _tiles_dict(items: dict) -> dict[str, list[int]]:
    return {k: list(v) for k, v in sorted(items.items())}
