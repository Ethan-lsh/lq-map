"""Configuration, seeded workloads, fair comparisons, and schema-versioned exports."""

import csv
import hashlib
import json
import platform
import random
import subprocess
import sys
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from math import ceil
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from typing import Any, Self
from zipfile import ZIP_DEFLATED, ZipFile

from ir import CircuitDAG
from mapping import initial_placement
from metrics import METRICS_VERSION
from model import (
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
from mscmap import MODEL_VERSION, SCHEMA_VERSION, __version__
from policies import PolicySettings, drive, make_policy
from qiskit_io import load_qasm, qiskit_version
from sim import Simulator

RAW_SCHEMA_VERSION = 4
EVALUATION_VERSION = "performance-v5"

ASSUMPTIONS = {
    "model": MODEL_VERSION,
    "tiles": TILE_KINDS,
    "orientation": {b: list(s) for b, s in BOUNDARY_SIDES.items() if b != "any"},
    "operations": {
        k: dict(zip(("requires", "reserves", "effect"), v)) for k, v in OPERATION_CONTRACTS.items()
    },
    "execution": "no routing paths or remote operations: two-patch operations need a local "
    "contact (direct or one shared ancilla tile); distant operands are brought into contact "
    "by SWAP/move sequences",
    "swap": "abstract exchange of in-line cardinal neighbors across one junction bus tile, "
    "occupying both slots and the junction; the physical SWAP protocol is not specified",
    "msc_operation": "an MSC operates at any C slot; M limits concurrent resources; "
    "relocation needs an empty C slot and transfers no state",
    "supply": "Bernoulli attempts; failure repeats initialize+cultivate; planning uses "
    "ceil(1/p) attempts and never reads realized outcomes",
    "timing": "integer logical cycles (lcycles); gate defaults follow PureMagic's lcycle "
    "accounting (CX 2, S/SDG 3, T consume 1); H/X/Z/CZ, cultivation, SWAP, move, and "
    "relocation durations are modeling assumptions; half-open reservations; finishes by "
    "operation ID before starts",
    "time_scale": "physical cycle 1 us by default; a logical cycle takes at least "
    "code_distance physical cycles and the model uses that minimum (modeling choice), so "
    "code_distance 17 gives 17 us per lcycle; durations are not rescaled by code_distance",
    "objective": "weighted movement_volume + operation_volume + cultivation_volume "
    "+ request_wait + makespan",
    "magic_gates": "T and T-dagger each consume one magic state; the Clifford difference "
    "is folded into consumption",
    "circuit_import": "OpenQASM files are parsed and decomposed into Clifford+T by Qiskit "
    "(optimization level 1, final measurements removed, barriers ignored); rotation "
    "synthesis precision sets the T count; the resolved config stores the converted gates",
    "spacetime_volume": "N x L: provisioned tiles (S + C + B, blocked excluded) times the "
    "realized makespan in lcycles, including SWAP, supply, and resource waits; "
    "spacetime_volume_us multiplies by lcycle_us",
    "waits": "T dependency readiness to consumption start; supply then access",
    "claim": "abstract scheduling model, not a physically verified protocol",
}

REMOVED_KEYS = {
    "layout": "replace layout with geometry.tiles (rows of S/C/B/. characters)",
    "mode": "remove mode; interval reservations are the only execution model",
    "budget": "remove budget; experiments have no completion deadline",
    "long_route_threshold": "remove it; tile-swap-v1 has no routing paths",
}
REMOVED_FIELDS = {
    "costs": {
        "route_tile": "remove it; operations use local contacts, not routes",
        "movement_reach": "remove it; a SWAP/move spans one junction tile",
    },
    "objective": {"routing_volume": "remove it; use movement_volume and operation_volume"},
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


WORKLOAD_FAMILIES = ("localized", "migrating", "alternating", "distributed", "random")
WORKLOAD_DEFAULTS = {
    "family": "alternating",
    "requests": 4,
    "burst": 1,
    "cx_per_burst": 0,
    "cx_span": "global",
    "dependency": "independent",
}


def workload(spec: dict, placement: dict[str, Tile], seed: int) -> dict:
    """Seeded synthetic circuit; T targets are fixed relative to the supplied placement.

    Each burst issues `cx_per_burst` CX gates, then `burst` T requests each followed
    by H. `dependency` adds control edges: `serial` chains T requests, `burst` makes
    every burst wait for the previous one. Per-qubit order is added on import.
    """
    spec = _workload_spec(spec, len(placement))
    count, burst, cx_count = spec["requests"], spec["burst"], spec["cx_per_burst"]
    names = sorted(placement, key=lambda q: (placement[q], q))
    rng = random.Random(seed)
    gates: list[dict] = []
    previous_burst: list[str] = []
    last_t: list[str] = []
    for b in range(ceil(count / burst)):
        barrier = previous_burst if spec["dependency"] == "burst" else []
        current: list[str] = []
        for k in range(cx_count):
            first, second = _cx_pair(names, rng, spec["cx_span"])
            gate_id = f"cx{b:04d}_{k}"
            gates.append(
                {"id": gate_id, "kind": "CX", "qubits": [first, second], "predecessors": barrier}
            )
            current.append(gate_id)
        for i in range(b * burst, min(count, (b + 1) * burst)):
            target = _t_target(spec["family"], names, i, count, rng)
            predecessors = barrier + (last_t if spec["dependency"] == "serial" else [])
            gates.append(
                {"id": f"t{i:04d}", "kind": "T", "qubits": [target], "predecessors": predecessors}
            )
            gates.append({"id": f"h{i:04d}", "kind": "H", "qubits": [target]})
            last_t = [f"t{i:04d}"]
            current.append(f"h{i:04d}")
        previous_burst = current
    return {"qubits": sorted(placement), "gates": gates}


def _workload_spec(spec: dict, data_qubits: int) -> dict:
    """Workload options merged over the defaults, with actionable validation errors."""
    unknown = set(spec) - set(WORKLOAD_DEFAULTS)
    if unknown:
        raise ValueError(f"unsupported workload options: {sorted(unknown)}")
    spec = {**WORKLOAD_DEFAULTS, **spec}
    count, burst, cx_count = spec["requests"], spec["burst"], spec["cx_per_burst"]
    if any(type(v) is not int for v in (count, burst, cx_count)) or min(count, burst) < 1:
        raise ValueError("workload requests/burst must be positive and cx_per_burst >= 0")
    if cx_count < 0 or (cx_count and data_qubits < 2):
        raise ValueError("CX workloads need cx_per_burst >= 0 and at least two data qubits")
    if spec["family"] not in WORKLOAD_FAMILIES:
        raise ValueError(f"workload family must be one of {', '.join(WORKLOAD_FAMILIES)}")
    if spec["cx_span"] not in {"local", "global"}:
        raise ValueError("workload cx_span must be local or global")
    if spec["dependency"] not in {"independent", "serial", "burst"}:
        raise ValueError("workload dependency must be independent, serial, or burst")
    return spec


def _t_target(family: str, names: list[str], i: int, count: int, rng: random.Random) -> str:
    if family == "localized":
        return names[0]
    if family == "migrating":
        return names[min(len(names) - 1, i * len(names) // count)]
    if family == "alternating":
        return names[0 if i % 2 == 0 else -1]
    if family == "distributed":
        return names[i % len(names)]
    return rng.choice(names)


def _cx_pair(names: list[str], rng: random.Random, span: str) -> tuple[str, str]:
    """`local` partners are within two positions in initial-placement order."""
    index = rng.randrange(len(names))
    if span == "local":
        others = [j for j in range(index - 2, index + 3) if 0 <= j < len(names) and j != index]
    else:
        others = [j for j in range(len(names)) if j != index]
    return names[index], names[rng.choice(others)]


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
    circuit = _circuit(raw, base, supplied, seed)
    costs = OperationCosts(**raw.get("costs", {}))
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
        Objective(**raw.get("objective", {})),
        time_scale,
    )
    settings = PolicySettings(**raw.get("settings", {}))
    policy = raw.get("policy", "JointScheduler")
    make_policy(policy, settings)
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
        "costs": asdict(costs),
        "timing": {**asdict(time_scale), "unit": "lcycle", "lcycle_us": time_scale.lcycle_us},
        "supply": {
            "success_probability": supply.success_probability,
            "planning_attempts": supply.planning_attempts,
        },
        "objective": asdict(problem.objective),
        "policy": policy,
        "settings": asdict(settings),
        "assumptions": ASSUMPTIONS,
    }
    return problem, resolved


def _check_config_fields(raw: dict) -> None:
    """Reject removed, unknown, or version-mismatched configuration fields."""
    for key, message in REMOVED_KEYS.items():
        if key in raw:
            raise ValueError(f"{key} is not supported: {message}")
    for section, removed in REMOVED_FIELDS.items():
        for key, message in removed.items():
            if key in raw.get(section, {}):
                raise ValueError(f"{section}.{key} is not supported: {message}")
    if set(raw) - TOP_LEVEL_KEYS:
        raise ValueError(f"unsupported configuration fields: {sorted(set(raw) - TOP_LEVEL_KEYS)}")
    if raw.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise ValueError(f"configuration schema_version must be {SCHEMA_VERSION}")
    if raw.get("model_version", MODEL_VERSION) != MODEL_VERSION:
        raise ValueError(f"model_version must be {MODEL_VERSION}")


def _time_scale(raw: dict) -> TimeScale:
    """`code_distance` and `physical_cycle_us`; derived fields of a resolved config are
    accepted only when they match."""
    if not isinstance(raw, dict):
        raise ValueError("timing must be an object")
    fields = {k: v for k, v in raw.items() if k not in {"unit", "lcycle_us"}}
    try:
        scale = TimeScale(**fields)
    except TypeError as exc:
        raise ValueError(f"timing accepts code_distance and physical_cycle_us: {exc}") from exc
    if raw.get("unit", "lcycle") != "lcycle" or raw.get("lcycle_us", scale.lcycle_us) != (
        scale.lcycle_us
    ):
        raise ValueError("timing.unit must be lcycle and lcycle_us must match the distance")
    return scale


def _geometry(raw: dict) -> Geometry:
    if set(raw.get("geometry", {})) != {"tiles"}:
        raise ValueError("geometry must contain exactly `tiles`: rows of S/C/B/. characters")
    return Geometry(tuple(raw["geometry"]["tiles"]))


def _circuit(raw: dict, base: Path, supplied: dict[str, Tile], seed: int) -> CircuitDAG:
    """An inline circuit, a JSON or OpenQASM (via Qiskit) file, or a workload generated
    against the supplied placement. The resolved config stores the converted circuit."""
    if ("circuit" in raw) == ("workload" in raw):
        raise ValueError("provide exactly one of circuit or workload")
    circuit_raw = raw.get("circuit")
    if isinstance(circuit_raw, str):
        path = base / circuit_raw
        circuit_raw = load_qasm(path) if path.suffix == ".qasm" else load_document(path)
    if circuit_raw is None:
        circuit_raw = workload(raw["workload"], supplied, seed)
    return CircuitDAG.from_dict(circuit_raw)


def _supply(raw: dict, seed: int, sample: int) -> SupplyModel:
    """`planning_attempts` is derived; it is accepted only when rerunning a resolved config."""
    if set(raw) - {"success_probability", "planning_attempts"}:
        raise ValueError("supply accepts success_probability; seeds come from seed and sample")
    supply = SupplyModel(raw.get("success_probability", 1.0), seed, sample)
    if raw.get("planning_attempts", supply.planning_attempts) != supply.planning_attempts:
        raise ValueError("supply.planning_attempts is derived from success_probability")
    return supply


def _tiles_dict(items: dict) -> dict[str, list[int]]:
    return {k: list(v) for k, v in sorted(items.items())}


def environment() -> dict:
    revision, dirty = _git_state()
    return {
        "source_sha256": _source_digest(),
        "mscmap": __version__,
        "python": sys.version.split()[0],
        "qiskit": qiskit_version(),
        "platform": platform.platform(),
        "git_revision": revision,
        "git_dirty": dirty,
    }


def _git_state() -> tuple[str | None, bool | None]:
    """(HEAD revision, uncommitted changes); both None outside a git checkout."""
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
        revision = head.stdout.strip() if head.returncode == 0 else ""
        if not revision:
            return None, None
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    except OSError:
        return None, None
    return revision, bool(status.stdout.strip())


def _source_digest() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


@dataclass
class RunResult:
    status: str
    reason: str
    metrics: dict[str, Any]
    config: dict[str, Any]
    trace: list[dict[str, Any]] = field(repr=False)
    output: str | None = None


def _execute(problem: Problem, config: dict) -> RunResult:
    sim = Simulator(problem)
    settings = PolicySettings(**config["settings"])
    policy = make_policy(config["policy"], settings)
    outcome = drive(sim, policy, settings)
    metrics = {
        **sim.metrics.summary(problem, sim.state, sim.complete),
        "planning_seconds": policy.planning_seconds,
        **policy.diagnostics(),
    }
    resolved = {**config, "environment": environment()}
    return RunResult(outcome.status, outcome.reason, metrics, resolved, sim.trace.events)


SUMMARY_COLUMNS = (
    "name",
    "status",
    "objective",
    "makespan",
    "makespan_us",
    "swap_count",
    "move_count",
    "swap_depth",
    "movement_volume",
    "relocation_count",
    "total_request_wait",
    "msc_utilization",
    "provisioned_area",
    "spacetime_volume",
    "spacetime_volume_us",
    "msc_pool_size",
    "planning_seconds",
)


def _summary_fields(status: str, metrics: dict[str, Any]) -> dict[str, Any]:
    """Summary columns after `name`: the run status, then the selected metrics."""
    return {"status": status, **{k: metrics[k] for k in SUMMARY_COLUMNS[2:]}}


def _case_label(case_id: str | None) -> str:
    return case_id or "default"


class RawDataWriter:
    """Append runs to one output root and derive its final summary from raw records."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "raw_data.zip"
        self.keys: set[tuple] = set()
        self.groups: dict[tuple, dict] = {}

    def __enter__(self) -> Self:
        self.directory.mkdir(parents=True, exist_ok=True)
        entries = {p.name for p in self.directory.iterdir()}
        if entries not in (set(), {"summary.csv", "raw_data.zip"}):
            raise ValueError("output must be empty or contain only summary.csv and raw_data.zip")
        # Work in temporary files; rejected additions cannot erase existing results.
        self.temporary = TemporaryDirectory(prefix=".pending-", dir=self.directory)
        self.pending = Path(self.temporary.name)
        self.archive = ZipFile(self.pending / "raw_data.zip", "x", compression=ZIP_DEFLATED)
        self.stream = self.archive.open("raw_data.jsonl", "w", force_zip64=True)
        try:
            self._copy_previous()
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise
        return self

    def _copy_previous(self) -> None:
        """Re-validate and carry over records from an existing archive."""
        if not self.path.exists():
            return
        with ZipFile(self.path) as previous:
            if previous.namelist() != ["raw_data.jsonl"]:
                raise ValueError("unsupported raw archive layout")
            with previous.open("raw_data.jsonl") as records:
                for line in records:
                    self.write(json.loads(line))

    def check_new(self, case_id: str | None, sample: int, repeat: int, names: list[str]) -> None:
        case_id = _case_label(case_id)
        for name in names:
            if (case_id, sample, repeat, name) in self.keys:
                raise ValueError(
                    f"duplicate result: case={case_id}, sample={sample}, "
                    f"repeat={repeat}, policy={name}; choose another case ID or repeat"
                )

    def write(self, record: dict) -> None:
        data = {"raw_schema_version": RAW_SCHEMA_VERSION, **record}
        if data["raw_schema_version"] != RAW_SCHEMA_VERSION:
            raise ValueError(
                f"existing archive uses raw schema {data['raw_schema_version']}; "
                f"write {MODEL_VERSION} results to a new output directory"
            )
        if data["type"] == "run":
            self._add_run(data)
        self.stream.write((json.dumps(data, sort_keys=True) + "\n").encode())

    def _add_run(self, data: dict) -> None:
        case_id, name = _case_label(data.get("case_id")), data["name"]
        sample, repeat, metrics = data["sample"], data["repeat"], data["metrics"]
        self.check_new(case_id, sample, repeat, [name])
        row = {
            "case_id": case_id,
            "sample": sample,
            "name": name,
            **_summary_fields(metrics["status"], metrics),
        }
        config = {k: v for k, v in data["config"].items() if k != "environment"}
        stable = {k: v for k, v in row.items() if k != "planning_seconds"}
        group = self.groups.setdefault(
            (case_id, sample, name), {"config": config, "stable": stable, "row": row, "timings": []}
        )
        if config != group["config"] or stable != group["stable"]:
            raise ValueError(f"changed input or outcome for {case_id}/{name}; use a new case ID")
        group["timings"].append(metrics["planning_seconds"])
        self.keys.add((case_id, sample, repeat, name))

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        try:
            self.stream.close()
            self.archive.close()
            if exc_type is None:
                rows = [
                    {**g["row"], "planning_seconds": median(g["timings"])}
                    for g in self.groups.values()
                ]
                write_summary(rows, self.pending / "summary.csv")
                (self.pending / "raw_data.zip").replace(self.path)
                (self.pending / "summary.csv").replace(self.directory / "summary.csv")
        finally:
            self.archive.close()
            self.temporary.cleanup()


def export(
    result: RunResult,
    archive: RawDataWriter,
    name: str,
    case_id: str | None,
    sample: int,
    repeat: int,
) -> None:
    result.output = str(archive.path)
    archive.write(
        {
            "type": "run",
            "case_id": case_id,
            "sample": sample,
            "repeat": repeat,
            "name": name,
            "config": result.config,
            "metrics": {
                "schema_version": SCHEMA_VERSION,
                "model_version": MODEL_VERSION,
                "metrics_version": METRICS_VERSION,
                "evaluation_version": EVALUATION_VERSION,
                "status": result.status,
                "reason": result.reason,
                **result.metrics,
            },
            "trace": result.trace,
        }
    )


def run(
    raw: dict,
    base: Path,
    directory: Path,
    *,
    variant: str | None = None,
    raw_data: RawDataWriter | None = None,
    case_id: str | None = None,
    repeat: int = 1,
    samples: int = 1,
) -> list[dict]:
    """Execute common variants for each stochastic sample; a batch may share the archive."""
    if type(repeat) is not int or repeat < 1:
        raise ValueError("repeat must be a positive integer")
    if type(samples) is not int or samples < 1:
        raise ValueError("samples must be a positive integer")
    if case_id is not None and (not isinstance(case_id, str) or not case_id.strip()):
        raise ValueError("case_id must be a nonempty string")
    prepared = [
        (sample, *item)
        for sample in range(samples)
        for item in _prepare_variants(raw, base, variant, sample)
    ]
    rows = []
    with nullcontext(raw_data) if raw_data is not None else RawDataWriter(directory) as archive:
        for sample in range(samples):
            names = [name for s, name, _, _ in prepared if s == sample]
            archive.check_new(case_id, sample, repeat, names)
        for sample, name, problem, config in prepared:
            result = _execute(problem, config)
            export(result, archive, name, case_id, sample, repeat)
            rows.append(
                {
                    "sample": sample,
                    "name": name,
                    **_summary_fields(result.status, result.metrics),
                }
            )
    return rows


def _prepare_variants(
    raw: dict, base: Path, variant: str | None, sample: int
) -> list[tuple[str, Problem, dict]]:
    """Resolve the common workload once; variants change only policy/settings/mapping."""
    variants = raw.get("variants", [{}])
    if not isinstance(variants, list) or not variants:
        raise ValueError("variants must be a nonempty list")
    _, common = resolve({k: v for k, v in raw.items() if k != "variants"}, base, sample)
    prepared = []
    for item in variants:
        if not isinstance(item, dict):
            raise ValueError("each variant must be an object")
        if set(item) - VARIANT_KEYS:
            raise ValueError(f"variants may change only {', '.join(sorted(VARIANT_KEYS))}")
        policy = item.get("policy", common["policy"])
        name = item.get("name", policy)
        if not isinstance(name, str) or not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("variant names must be single safe names")
        changed = {
            **{k: v for k, v in common.items() if k != "sample"},
            "policy": policy,
            "mapping": item.get("mapping", common["mapping"]),
            "settings": {**common["settings"], **item.get("settings", {})},
        }
        problem, config = resolve(changed, base, sample)
        prepared.append((name, problem, config))
    names = [item[0] for item in prepared]
    if len(set(names)) != len(names):
        raise ValueError("duplicate variant name")
    if variant is not None:
        if variant not in names:
            raise ValueError(f"unknown variant {variant!r}; available: {', '.join(names)}")
        prepared = [item for item in prepared if item[0] == variant]
    return prepared


def write_summary(rows: list[dict], path: Path) -> None:
    """Write performance outcomes; detailed diagnostics stay in the raw archive."""
    columns = ["case_id", "sample", *SUMMARY_COLUMNS]
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
