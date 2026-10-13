"""Execute configured variants: resolve the workload once, run every policy variant on
each stochastic sample, and export runs to the shared archive.

`proposed_config` restricts a configuration to the proposed method, the single variant
that the experiment drivers (`benchmark`, `compare`) execute.
"""

import hashlib
import platform
import subprocess
import sys
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from algorithm.metrics import active_metrics
from algorithm.model import Problem
from algorithm.policies import PolicySettings, drive, make_policy
from algorithm.simulator import Simulator
from config.circuits import qiskit_version
from config.loader import VARIANT_KEYS, resolve
from execution.archive import RawDataWriter, summary_fields
from versions import (
    EVALUATION_VERSION,
    METRICS_VERSION,
    MODEL_VERSION,
    SCHEMA_VERSION,
    __version__,
)


# The proposed method: demand-aware initial placement of data and MSC sites, then
# JointScheduler with idle-only MSC relocation. Drivers run no other variant.
PROPOSED_METHOD = {"policy": "JointScheduler", "mapping": "demand_aware"}


def proposed_config(config: dict) -> dict:
    """`config` with the proposed method as its only variant (named `JointScheduler`)."""
    settings = {**config.get("settings", {}), "relocation": True}
    restricted = {**config, **PROPOSED_METHOD, "settings": settings}
    restricted.pop("variants", None)
    return restricted


@dataclass
class RunResult:
    status: str
    reason: str
    metrics: dict[str, Any]
    config: dict[str, Any]
    trace: list[dict[str, Any]] = field(repr=False)


def execute(problem: Problem, config: dict) -> RunResult:
    """One policy run of a resolved problem; metrics come from the live simulator."""
    sim = Simulator(problem)
    settings = PolicySettings(**config["settings"])
    policy = make_policy(config["policy"], settings)
    outcome = drive(sim, policy, settings)
    metrics = {
        **sim.metrics.summary(problem, sim.state, sim.complete),
        **active_metrics(sim.trace.events, sim.state.time, problem.geometry.area["total"]),
        "planning_seconds": policy.planning_seconds,
        **policy.diagnostics(),
    }
    resolved = {**config, "environment": environment()}
    return RunResult(outcome.status, outcome.reason, metrics, resolved, sim.trace.events)


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
        for item in prepare_variants(raw, base, variant, sample)
    ]
    rows = []
    with nullcontext(raw_data) if raw_data is not None else RawDataWriter(directory) as archive:
        for sample in range(samples):
            names = [name for s, name, _, _ in prepared if s == sample]
            archive.check_new(case_id, sample, repeat, names)
        for sample, name, problem, config in prepared:
            result = execute(problem, config)
            export(result, archive, name, case_id, sample, repeat)
            rows.append(
                {"sample": sample, "name": name, **summary_fields(result.status, result.metrics)}
            )
    return rows


def run_repeats(
    config: dict,
    base: Path,
    directory: Path,
    archive: RawDataWriter,
    *,
    case_id: str,
    repeats: int,
    samples: int,
    report=None,
) -> int:
    """Timing repeats of one case into a shared archive, rotating the variant order by
    repeat index so no variant always runs first. Returns the number of runs."""
    count = 0
    variants = config["variants"]
    for repeat in range(repeats):
        offset = repeat % len(variants)
        ordered = {**config, "variants": variants[offset:] + variants[:offset]}
        batch = run(
            ordered,
            base,
            directory,
            raw_data=archive,
            case_id=case_id,
            repeat=repeat + 1,
            samples=samples,
        )
        count += len(batch)
        if report is not None:
            report(repeat + 1, batch)
    return count


def prepare_variants(
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


def export(
    result: RunResult,
    archive: RawDataWriter,
    name: str,
    case_id: str | None,
    sample: int,
    repeat: int,
) -> None:
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
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        )
        revision = head.stdout.strip() if head.returncode == 0 else ""
        if not revision:
            return None, None
        status = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=False
        )
    except OSError:
        return None, None
    return revision, bool(status.stdout.strip())


def _source_digest() -> str:
    """SHA-256 over every source module under `src/`, in path order."""
    source = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*.py")):
        digest.update(str(path.relative_to(source)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()
