"""Controlled workload cases using the common engine and repeated planning timings.

Command `sweep` of `main.py`: predeclared one-factor cases around a reference
workload, every variant from the same resolved placement. Costs, supply, objective,
and base settings come from `configs/experiment.yaml`.
"""

import argparse
import copy
from pathlib import Path

from config.layouts import brick_geometry
from config.loader import load_document, resolve
from config.paths import DEFAULT_CONFIG
from config.workload import WORKLOAD_DEFAULTS, workload
from execution.archive import RawDataWriter
from execution.engine import run_repeats

GENERATOR_VERSION = "controlled-workloads-v3"
VARIANTS = [
    {"policy": "FixedSite"},
    {"policy": "JointScheduler"},
    {"name": "JointFixedPool", "policy": "JointScheduler", "settings": {"relocation": False}},
    {"name": "JointDemandMap", "policy": "JointScheduler", "mapping": "demand_aware"},
]
REFERENCE = {
    "family": "migrating",
    "requests": 16,
    "burst": 2,
    "cx_per_burst": 2,
    "cx_span": "global",
    "dependency": "independent",
    "data_qubits": 8,
    "slot_rows": 3,
    "block_every": 0,
    "msc_count": 2,
    "cultivate": 3,
    "relocate": 1,
    "success_probability": 1.0,
    "samples": 1,
    "seed": 7,
}
# One-factor changes to the reference, plus pooling and scale cases; no result filtering.
CASES = [
    ("reference", {}),
    ("t_localized", {"family": "localized"}),
    ("t_alternating", {"family": "alternating"}),
    ("t_distributed", {"family": "distributed"}),
    ("t_bursty", {"burst": 4}),
    ("cx_none", {"cx_per_burst": 0}),
    ("cx_dense", {"cx_per_burst": 6}),
    ("cx_local", {"cx_per_burst": 6, "cx_span": "local"}),
    ("pool_1", {"msc_count": 1}),
    ("pool_4", {"msc_count": 4}),
    ("cultivation_slow", {"cultivate": 9}),
    ("supply_stochastic", {"success_probability": 0.5, "samples": 3}),
    ("relocation_costly", {"relocate": 6}),
    ("ancilla_poor", {"block_every": 4}),
    ("ancilla_scarce", {"block_every": 3}),
    # Equal geometry (area); capacity differs and is reported as msc_pool_size.
    ("pooling_distributed_4", {"msc_count": 4, "family": "distributed"}),
    ("pooling_movable_2", {"msc_count": 2, "family": "distributed"}),
    (
        "large_high_demand",
        {"data_qubits": 24, "slot_rows": 5, "requests": 96, "burst": 4, "cx_per_burst": 4},
    ),
]


def case_specs() -> list[dict]:
    return [{"case_id": name, **REFERENCE, **change} for name, change in CASES]


def case_config(common: dict, spec: dict) -> dict:
    config = copy.deepcopy(common)
    tiles, placement, mscs = brick_geometry(
        spec["data_qubits"], spec["msc_count"], spec["slot_rows"], spec["block_every"]
    )
    config.update(geometry={"tiles": tiles}, placement=placement, mscs=mscs, seed=spec["seed"])
    config["costs"] = {
        **config.get("costs", {}),
        "cultivate": spec["cultivate"],
        "relocate": spec["relocate"],
    }
    config["supply"] = {"success_probability": spec["success_probability"]}
    config.pop("workload", None)
    # Freeze gate identities against the supplied placement before any mapping.
    options = {k: spec[k] for k in WORKLOAD_DEFAULTS}
    config["circuit"] = workload(options, placement, spec["seed"])
    config["variants"] = VARIANTS
    return config


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--case", action="append", dest="cases", help="select a case; repeatable")


def execute(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    specs = case_specs()
    if args.cases:
        unknown = set(args.cases) - {s["case_id"] for s in specs}
        if unknown:
            parser.error(f"unknown cases: {sorted(unknown)}")
        specs = [s for s in specs if s["case_id"] in args.cases]
    common = load_document(DEFAULT_CONFIG)
    prepared = [(s, case_config(common, s)) for s in specs]
    for _, config in prepared:
        resolve(config, DEFAULT_CONFIG.parent)
    manifest = {
        "generator_version": GENERATOR_VERSION,
        "repeats": args.repeats,
        "planning_seconds": "median over timing repeats, per case/sample/variant",
        "order": "variant order rotated by repeat index",
        "cases": specs,
    }
    run_count = 0
    with RawDataWriter(args.output) as archive:
        archive.write({"type": "manifest", **manifest})
        for spec, config in prepared:
            case_id = spec["case_id"]
            archive.write({"type": "case", "case_id": case_id, "input": config, "spec": spec})

            def report(repeat: int, _: list[dict], case_id: str = case_id) -> None:
                print(f"{case_id}: repeat {repeat}/{args.repeats} recorded", flush=True)

            run_count += run_repeats(
                config,
                DEFAULT_CONFIG.parent,
                args.output,
                archive,
                case_id=case_id,
                repeats=args.repeats,
                samples=spec["samples"],
                report=report,
            )
    print(f"Added {run_count} raw runs; updated {args.output / 'summary.csv'}")
