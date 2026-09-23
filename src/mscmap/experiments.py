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
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from typing import Any, Self
from zipfile import ZIP_DEFLATED, ZipFile

from . import MODEL_VERSION, SCHEMA_VERSION, __version__
from .ir import CircuitDAG
from .model import Layout, OperationModel, Problem, cell
from .policies import PolicySettings, drive, make_policy
from .sim import Simulator

ASSUMPTIONS = {
    "model": MODEL_VERSION,
    "footprint": "exactly N data + M reusable MSC cells; no extra buffers or ancillas",
    "adjacency": "cardinal edges only; two-qubit and consumption reserve both patches/contact",
    "exchange": "data/data and idle-MSC/data exchanges each count one logical SWAP",
    "lifecycle": "initialize -> produce -> one owned output -> consume -> idle",
    "relocation": "complete declared walk, then initialize; no cancellation or restart",
    "transaction": "includes initialization; releases before production",
    "serialized": "one complete relocation transaction at a time",
    "disjoint_paths": "complete path union excludes all incompatible operations",
    "space_time": "per-operation reservations; distinct claimed destination ownership",
    "timing": "positive illustrative abstract integer cycles; half-open reservations",
    "objective": "minimize logical SWAPs without a completion deadline",
    "time_unit": "abstract_cycle; not calibrated physical code cycles",
    "same_time": "all finishes by operation ID before any starts",
    "auxiliary_resources": "none supported; larger footprints are rejected",
    "waits": "predecessor finish to consumption start; allocation delay is supply wait",
    "wait_blockers": "supply, adjacency and resource durations may overlap; never sum as total",
    "swap_depth": "SWAP-only patch dependency layers in trace start order, ignoring non-SWAPs",
    "claim": "abstract scheduling model, not a physically verified cultivation protocol",
    "measurement": "online-v1; direct event accounting without independent trace replay",
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


def workload(spec: dict, placement: dict[str, tuple[int, int]], seed: int) -> dict:
    """Generate fixed logical operands and explicit optional control dependencies."""
    allowed = {"family", "requests", "dependency", "cx_every"}
    if set(spec) - allowed:
        raise ValueError(f"unsupported workload options: {sorted(set(spec) - allowed)}")
    family, count = spec.get("family", "alternating"), spec.get("requests", 4)
    if type(count) is not int or count <= 0:
        raise ValueError("workload requests must be a positive integer")
    if family not in {"localized", "migrating", "alternating", "distributed", "data_heavy"}:
        raise ValueError(f"unknown workload family: {family}")
    dependency = spec.get("dependency", "independent")
    if dependency not in {"independent", "serial", "fork_join"}:
        raise ValueError("workload dependency must be independent, serial, or fork_join")
    cx_every = spec.get("cx_every", 1 if family == "data_heavy" and len(placement) > 1 else 0)
    if type(cx_every) is not int or cx_every < 0:
        raise ValueError("workload cx_every must be a nonnegative integer (0 disables CX)")
    if cx_every and len(placement) < 2:
        raise ValueError("CX workloads require at least two data qubits")
    names = sorted(placement, key=lambda q: (placement[q], q))
    rng = random.Random(seed)
    gates = []
    group_ends = []
    barrier = []
    for i in range(count):
        if family == "localized":
            target = names[0]
        elif family == "migrating":
            target = names[min(len(names) - 1, i * len(names) // count)]
        elif family == "alternating":
            target = names[0 if i % 2 == 0 else -1]
        elif family == "distributed":
            target = names[i % len(names)]
        else:
            target = rng.choice(names)
        predecessors = []
        if dependency == "serial" and gates:
            predecessors = [gates[-1]["id"]]
        elif dependency == "fork_join":
            if i % 2 == 0 and group_ends:
                barrier, group_ends = group_ends, []
            predecessors = list(barrier)
        if cx_every and (i + 1) % cx_every == 0:
            other = rng.choice([q for q in names if q != target])
            gates.append(
                {
                    "id": f"cx{i:04d}",
                    "kind": "CX",
                    "qubits": [target, other],
                    "predecessors": predecessors,
                }
            )
            predecessors = [f"cx{i:04d}"]
        gates.append(
            {
                "id": f"t{i:04d}",
                "kind": "T",
                "qubits": [target],
                "predecessors": predecessors,
            }
        )
        gates.append({"id": f"h{i:04d}", "kind": "H", "qubits": [target]})
        group_ends.append(f"h{i:04d}")
    # Import adds the required per-qubit program order to the control edges above.
    return {"qubits": sorted(placement), "gates": gates}


def resolve(raw: dict, base: Path = Path(".")) -> tuple[Problem, dict]:
    allowed = {
        "schema_version",
        "model_version",
        "layout",
        "placement",
        "mscs",
        "costs",
        "oracle_horizon",
        "mode",
        "circuit",
        "workload",
        "seed",
        "policy",
        "settings",
        "variants",
        "oracle_state_limit",
        "assumptions",
        "environment",
        "backend",
    }
    if "budget" in raw:
        raise ValueError("budget was removed: delete it to run without a completion deadline")
    if set(raw) - allowed:
        raise ValueError(f"unsupported configuration fields: {sorted(set(raw) - allowed)}")
    if raw.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise ValueError("unsupported configuration schema")
    if raw.get("model_version", MODEL_VERSION) != MODEL_VERSION:
        raise ValueError("unsupported operation model version")
    if set(raw["layout"]) != {"usable", "blocked"}:
        raise ValueError("layout must explicitly enumerate usable and blocked cells")
    usable = [cell(v) for v in raw["layout"]["usable"]]
    blocked = [cell(v) for v in raw["layout"]["blocked"]]
    if len(set(usable)) != len(usable) or len(set(blocked)) != len(blocked):
        raise ValueError("duplicate layout cell")
    layout = Layout(frozenset(usable), frozenset(blocked))
    placement = {q: cell(v) for q, v in raw["placement"].items()}
    sites = {k: cell(v) for k, v in raw["mscs"].items()}
    seed = raw.get("seed", 0)
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    if ("circuit" in raw) == ("workload" in raw):
        raise ValueError("provide exactly one of circuit or workload")
    circuit_raw = raw.get("circuit")
    if isinstance(circuit_raw, str):
        circuit_raw = load_document(base / circuit_raw)
    if circuit_raw is None:
        circuit_raw = workload(raw["workload"], placement, seed)
    circuit = CircuitDAG.from_dict(circuit_raw)
    costs = OperationModel(**raw.get("costs", {}))
    problem = Problem(circuit, layout, placement, sites, costs, raw.get("mode", "serialized"))
    settings = PolicySettings(**raw.get("settings", {}))
    if raw.get("backend", "heuristic") not in {"heuristic", "oracle"}:
        raise ValueError("backend must be heuristic or oracle")
    policy = raw.get("policy", "JointScheduler")
    make_policy(policy, settings)
    resolved = {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "layout": {
            "usable": [list(v) for v in sorted(layout.usable)],
            "blocked": [list(v) for v in sorted(layout.blocked)],
        },
        "placement": {q: list(v) for q, v in sorted(placement.items())},
        "mscs": {k: list(v) for k, v in sorted(sites.items())},
        "circuit": circuit.to_dict(),
        "costs": asdict(costs),
        "mode": problem.mode,
        "seed": seed,
        "policy": policy,
        "settings": asdict(settings),
        "assumptions": ASSUMPTIONS,
        "backend": raw.get("backend", "heuristic"),
    }
    if "oracle_horizon" in raw or raw.get("backend", "heuristic") == "oracle":
        horizon = raw.get("oracle_horizon", 14)
        if type(horizon) is not int or not 1 <= horizon <= 14:
            raise ValueError("oracle_horizon must be an integer from 1 to 14")
        resolved["oracle_horizon"] = horizon
    if "oracle_state_limit" in raw:
        resolved["oracle_state_limit"] = raw["oracle_state_limit"]
    return problem, resolved


def environment() -> dict:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        )
        revision = result.stdout.strip() if result.returncode == 0 else None
        dirty = (
            subprocess.run(
                ["git", "status", "--porcelain"], capture_output=True, text=True, check=False
            ).stdout.strip()
            if revision
            else None
        )
    except OSError:
        revision = dirty = None
    source = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted(source.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return {
        "source_sha256": digest.hexdigest(),
        "mscmap": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "git_revision": revision,
        "git_dirty": bool(dirty) if revision else None,
    }


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
    oracle = config["backend"] == "oracle"
    if oracle:
        from .oracle import solve

        result = solve(
            sim,
            state_limit=config.get("oracle_state_limit", 100000),
            horizon=config.get("oracle_horizon", 14),
        )
        sim = result.simulator or sim
        status = "completed" if result.simulator else "feasible_plan_not_found"
        reason = ""
        if result.limit_reached:
            reason = f"oracle state limit reached after {result.explored} states"
        elif result.simulator is None:
            reason = "oracle found no completed schedule within its search horizon"
        planning = result.planning_seconds
        diagnostics = {
            "oracle_states": result.explored,
            "oracle_horizon": config.get("oracle_horizon", 14),
        }
    else:
        settings = PolicySettings(**config["settings"])
        policy = make_policy(config["policy"], settings)
        outcome = drive(sim, policy, settings)
        status, reason = outcome.status, outcome.reason
        planning = policy.planning_seconds + policy.dispatch_seconds
        diagnostics = {
            "rollouts": getattr(policy, "rollouts", 0),
            "fallback_uses": getattr(policy, "fallback_uses", 0),
        }
        if config["policy"] == "JointScheduler":
            diagnostics["integration"] = {
                "version": "baseline-improvement-v1",
                "completion_planner": "internal_immediate_swap",
                "baseline_status": policy.baseline_status,
                "baseline_cost": policy.baseline_cost,
                "accepted_costs": policy.accepted_costs,
            }
    measurements = sim.metrics.summary(problem, sim.state, sim.complete)
    metrics = {**measurements, "planning_seconds": planning, **diagnostics}
    resolved = {
        **config,
        "backend": "oracle" if oracle else "heuristic",
        "environment": environment(),
    }
    return RunResult(status, reason, metrics, resolved, sim.trace.events)


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
            if self.path.exists():
                with ZipFile(self.path) as previous:
                    if previous.namelist() != ["raw_data.jsonl"]:
                        raise ValueError("unsupported raw archive layout")
                    with previous.open("raw_data.jsonl") as records:
                        for line in records:
                            self.write(json.loads(line))
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise
        return self

    def check_new(self, case_id: str | None, repeat: int, names: list[str]) -> None:
        for name in names:
            if (case_id or "default", repeat, name) in self.keys:
                raise ValueError(
                    f"duplicate result: case={case_id or 'default'}, repeat={repeat}, "
                    f"policy={name}; choose another case ID or repeat"
                )

    def write(self, record: dict) -> None:
        data = {"raw_schema_version": 1, **record}
        if data["raw_schema_version"] != 1:
            raise ValueError("unsupported raw schema version")
        if data["type"] == "run":
            case_id, name = data.get("case_id") or "default", data["name"]
            repeat = data["repeat"]
            self.check_new(case_id, repeat, [name])
            metrics = data["metrics"]
            row = {"case_id": case_id, "name": name, **{k: metrics[k] for k in SUMMARY_COLUMNS[1:]}}
            config = {k: v for k, v in data["config"].items() if k != "environment"}
            key = (case_id, name)
            stable = {k: v for k, v in row.items() if k != "planning_seconds"}
            if key in self.groups:
                group = self.groups[key]
                if config != group["config"] or stable != group["stable"]:
                    raise ValueError(
                        f"changed input or outcome for {case_id}/{name}; use a new case ID"
                    )
                group["timings"].append(metrics["planning_seconds"])
            else:
                self.groups[key] = {
                    "config": config,
                    "stable": stable,
                    "row": row,
                    "timings": [metrics["planning_seconds"]],
                }
            self.keys.add((case_id, repeat, name))
        self.stream.write((json.dumps(data, sort_keys=True) + "\n").encode())

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        try:
            self.stream.close()
            self.archive.close()
            if exc_type is None:
                rows = [
                    {**g["row"], "planning_seconds": median(g["timings"])}
                    for g in self.groups.values()
                ]
                write_summary(rows, self.pending / "summary.csv", extra_columns=("case_id",))
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
    repeat: int,
) -> None:
    result.output = str(archive.path)
    archive.write(
        {
            "type": "run",
            "case_id": case_id,
            "repeat": repeat,
            "name": name,
            "config": result.config,
            "metrics": {
                "schema_version": SCHEMA_VERSION,
                "model_version": MODEL_VERSION,
                "evaluation_version": "performance-v2",
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
) -> list[dict]:
    """Execute common variants; an enclosing batch may share the raw archive."""
    _, common = resolve(raw, base)
    prepared = _prepare_variants(raw.get("variants", [{}]), common, variant)
    if type(repeat) is not int or repeat < 1:
        raise ValueError("repeat must be a positive integer")
    if case_id is not None and (not isinstance(case_id, str) or not case_id.strip()):
        raise ValueError("case_id must be a nonempty string")
    rows = []
    with nullcontext(raw_data) if raw_data is not None else RawDataWriter(directory) as archive:
        archive.check_new(case_id, repeat, [name for name, _, _ in prepared])
        for name, problem, config in prepared:
            result = _execute(problem, config)
            export(result, archive, name, case_id, repeat)
            rows.append(_summary_row(name, result))
    return rows


def _prepare_variants(
    variants: list[dict], common: dict, variant: str | None
) -> list[tuple[str, Problem, dict]]:
    if not isinstance(variants, list) or not variants:
        raise ValueError("variants must be a nonempty list")
    prepared = []
    for item in variants:
        if not isinstance(item, dict):
            raise ValueError("each variant must be an object")
        if set(item) - {"name", "policy", "mode", "settings", "backend"}:
            raise ValueError("variants may change only policy, mode, settings, or backend")
        backend = item.get("backend", common["backend"])
        policy = item.get("policy", common["policy"])
        name = item.get("name", "oracle" if backend == "oracle" else policy)
        if not isinstance(name, str) or not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("variant names must be single safe directory names")
        changed = {
            **common,
            "policy": policy,
            "backend": backend,
            "mode": item.get("mode", common["mode"]),
            "settings": {**common["settings"], **item.get("settings", {})},
        }
        problem, config = resolve(changed)
        prepared.append((name, problem, config))
    names = [item[0] for item in prepared]
    if len(set(names)) != len(names):
        raise ValueError("duplicate variant name")
    if variant is not None:
        if variant not in names:
            raise ValueError(f"unknown variant {variant!r}; available: {', '.join(names)}")
        prepared = [item for item in prepared if item[0] == variant]
    return prepared


SUMMARY_COLUMNS = (
    "name",
    "status",
    "swap_count",
    "swap_depth",
    "makespan",
    "total_request_wait",
    "relocation_count",
    "planning_seconds",
)


def _summary_row(name: str, result: RunResult) -> dict:
    return {
        "name": name,
        "status": result.status,
        **{key: result.metrics[key] for key in SUMMARY_COLUMNS[2:]},
    }


def write_summary(rows: list[dict], path: Path, *, extra_columns: tuple[str, ...] = ()) -> None:
    """Write performance outcomes; detailed diagnostics stay in the raw archive."""
    with path.open("w", newline="") as stream:
        columns = list(dict.fromkeys(key for row in rows for key in row)) or [
            *extra_columns,
            *SUMMARY_COLUMNS,
        ]
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
