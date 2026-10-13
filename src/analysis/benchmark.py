"""Benchmark the proposed method on OpenQASM circuits stored as `<data>/<size class>/*.qasm`.

Command `benchmark` of `main.py`.

Each circuit is converted to Clifford+T once (Qiskit, see `config.circuits`) and placed
on a brick layout sized to its qubit count (`config.layouts.brick_geometry`: data
qubits in row-major supplied order, MSCs spread from opposite corners). Costs,
supply, objective, and base settings come from `configs/experiment.yaml`.

Every circuit runs the proposed method only (`execution.engine.proposed_config`):
demand-aware initial placement of data and MSC sites from the row-major supplied
order, then JointScheduler with idle-only MSC relocation.

Search bounds are benchmark choices, recorded in the archive: `rollout_gates` bounds
each JointScheduler rollout, and `event_limit` scales with the gate count so long
circuits are not truncated by the default limit. Circuits above `--max-gates` are
skipped before execution and listed in the manifest, never dropped silently.
"""

import argparse
import copy
import hashlib
from math import ceil, sqrt
from pathlib import Path

from config.circuits import SIZE_CLASSES, circuit_stats, discover, load_qasm, qiskit_version
from config.layouts import brick_geometry
from config.loader import load_document, resolve
from config.paths import DATA_ROOT, DEFAULT_CONFIG
from execution.archive import RawDataWriter
from execution.engine import proposed_config, run_repeats

GENERATOR_VERSION = "qasm-benchmark-v3"
EVENTS_PER_GATE = 50


def circuit_config(
    common: dict, circuit: dict, layout: tuple, args: argparse.Namespace, **overrides
) -> dict:
    """The common configuration with one converted circuit on `layout` (tiles,
    placement, MSC sites) and the benchmark search bounds, restricted to the proposed
    method."""
    tiles, placement, mscs = layout
    config = copy.deepcopy(common)
    config.pop("workload", None)
    config.update(geometry={"tiles": tiles}, placement=placement, mscs=mscs, circuit=circuit)
    config.update(overrides)
    settings = config.get("settings", {})
    event_limit = args.event_limit or max(
        settings.get("event_limit", 1), EVENTS_PER_GATE * len(circuit["gates"])
    )
    config["settings"] = {
        **settings,
        "rollout_gates": args.rollout_gates,
        "event_limit": event_limit,
    }
    return proposed_config(config)


def add_search_arguments(parser: argparse.ArgumentParser) -> None:
    """Options shared with `compare`: circuit selection, MSC count, and search bounds."""
    parser.add_argument("--data", type=Path, default=DATA_ROOT)
    parser.add_argument("--size", action="append", choices=SIZE_CLASSES, dest="sizes")
    parser.add_argument("--circuit", action="append", dest="stems", help="file stem; repeatable")
    parser.add_argument("--msc-count", type=int, default=2)
    parser.add_argument("--rollout-gates", type=int, default=32, help="0 = remaining circuit")
    parser.add_argument("--event-limit", type=int, help=f"default: {EVENTS_PER_GATE} x gates")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", type=Path, required=True)
    add_search_arguments(parser)
    parser.add_argument("--slot-rows", type=int, help="data rows (default: ceil(sqrt(qubits)))")
    parser.add_argument("--max-gates", type=int, help="skip larger converted circuits")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--samples", type=int, default=1)


def execute(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if min(args.repeats, args.samples, args.msc_count) < 1:
        parser.error("--repeats, --samples, and --msc-count must be positive")
    try:
        found = discover(args.data, args.sizes or list(SIZE_CLASSES), args.stems)
    except ValueError as exc:
        parser.error(str(exc))

    common = load_document(DEFAULT_CONFIG)
    prepared, skipped = [], []
    for size, path in found:
        case_id = f"{size}/{path.stem}"
        circuit = load_qasm(path)
        stats = circuit_stats(circuit)
        print(
            f"{case_id}: {stats['qubits']} qubits, {stats['gates']} gates, T={stats['t_count']}",
            flush=True,
        )
        if args.max_gates and stats["gates"] > args.max_gates:
            skipped.append({"case_id": case_id, "reason": f"gates > {args.max_gates}", **stats})
            continue
        slot_rows = args.slot_rows or ceil(sqrt(stats["qubits"]))  # near-square data region
        layout = brick_geometry(stats["qubits"], args.msc_count, slot_rows, 0)
        config = circuit_config(common, circuit, layout, args)
        resolve(config, DEFAULT_CONFIG.parent)
        source = {
            "path": str(path.relative_to(args.data)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_class": size,
        }
        prepared.append((case_id, config, {"source": source, "circuit_stats": stats}))

    manifest = {
        "generator_version": GENERATOR_VERSION,
        "qiskit": qiskit_version(),
        "repeats": args.repeats,
        "samples": args.samples,
        "msc_count": args.msc_count,
        "method": "proposed: demand_aware mapping, JointScheduler, relocation on",
        "rollout_gates": args.rollout_gates,
        "event_limit": args.event_limit or f"max(config, {EVENTS_PER_GATE} x gates)",
        "planning_seconds": "median over timing repeats, per case/sample",
        "cases": [case_id for case_id, _, _ in prepared],
        "skipped": skipped,
    }
    run_count = 0
    with RawDataWriter(args.output) as archive:
        archive.write({"type": "manifest", **manifest})
        for case_id, config, details in prepared:
            archive.write({"type": "case", "case_id": case_id, "input": config, **details})

            def report(repeat: int, batch: list[dict], case_id: str = case_id) -> None:
                statuses = ", ".join(f"{row['name']}={row['status']}" for row in batch)
                print(f"{case_id}: repeat {repeat}/{args.repeats}: {statuses}", flush=True)

            run_count += run_repeats(
                config,
                DEFAULT_CONFIG.parent,
                args.output,
                archive,
                case_id=case_id,
                repeats=args.repeats,
                samples=args.samples,
                report=report,
            )
    print(
        f"Added {run_count} raw runs; skipped {len(skipped)} circuits; "
        f"updated {args.output / 'summary.csv'}"
    )
