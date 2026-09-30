"""Controlled workload cases using the common engine and repeated planning timings."""

import argparse
import copy
from math import ceil
from pathlib import Path

from experiments import RawDataWriter, load_document, resolve, run, workload

VARIANTS = [
    {"policy": "FixedSite"},
    {"policy": "JointScheduler"},
    {"name": "JointFixedPool", "policy": "JointScheduler", "settings": {"relocation": False}},
    {"name": "JointDemandMap", "policy": "JointScheduler", "mapping": "demand_aware"},
]


def case_specs() -> list[dict]:
    """Predeclared one-factor cases plus pooling and scale cases; no result filtering."""
    base = {
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
    changes = [
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
    return [{"case_id": name, **base, **change} for name, change in changes]


def brick_geometry(data_qubits: int, msc_count: int, slot_rows: int, block_every: int):
    """Brick tile layout: in row r, column c is a slot unless (c + r) % 3 == 2. Slots are
    directly adjacent along rows, one junction tile apart elsewhere, and every bus tile
    borders slots on both axes (L-shaped CX contacts). The top and bottom rows hold the
    C slots; `slot_rows` rows between them hold data. `block_every` k > 0 blocks every
    k-th interior bus tile (row-major) to reduce ancilla space."""
    width = 3 * ceil((ceil(data_qubits / slot_rows) + 1) / 2)  # >= one spare slot per row
    height = slot_rows + 2
    tiles, bus_index = [], 0
    for r in range(height):
        row = []
        for c in range(width):
            if (c + r) % 3 != 2:
                row.append("C" if r in (0, height - 1) else "S")
            elif 0 < r < height - 1 and block_every and bus_index % block_every == 0:
                row.append(".")
                bus_index += 1
            else:
                row.append("B")
                bus_index += 0 < r < height - 1
        tiles.append("".join(row))
    slots = [(r, c) for r in range(1, height - 1) for c in range(width) if tiles[r][c] == "S"]
    top = [(0, c) for c in range(width) if tiles[0][c] == "C"]
    bottom = [(height - 1, c) for c in reversed(range(width)) if tiles[-1][c] == "C"]
    if msc_count > len(top) + len(bottom):
        raise ValueError("more MSC resources than cultivation slots")
    # Spread the pool from opposite corners so fixed capacity is distributed.
    counts = ((msc_count + 1) // 2, msc_count // 2)
    chosen = [row[j * len(row) // k] for row, k in zip((top, bottom), counts) for j in range(k)]
    per_row = ceil(data_qubits / slot_rows)
    rows = [[t for t in slots if t[0] == r] for r in range(1, height - 1)]
    placement_tiles = [t for row in rows for t in row[:per_row]][:data_qubits]
    placement = {f"q{i}": list(t) for i, t in enumerate(placement_tiles)}
    return tiles, placement, {f"m{i}": list(t) for i, t in enumerate(chosen)}


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
    keys = ("family", "requests", "burst", "cx_per_burst", "cx_span", "dependency")
    # Freeze gate identities against the supplied placement before any mapping.
    config["circuit"] = workload({k: spec[k] for k in keys}, placement, spec["seed"])
    config["variants"] = VARIANTS
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--case", action="append", dest="cases", help="select a case; repeatable")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    specs = case_specs()
    if args.cases:
        unknown = set(args.cases) - {s["case_id"] for s in specs}
        if unknown:
            parser.error(f"unknown cases: {sorted(unknown)}")
        specs = [s for s in specs if s["case_id"] in args.cases]
    config_path = Path(__file__).resolve().parents[1] / "configs/experiment.yaml"
    common = load_document(config_path)
    prepared = [(s, case_config(common, s)) for s in specs]
    for _, config in prepared:
        resolve(config, config_path.parent)
    manifest = {
        "generator_version": "controlled-workloads-v3",
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
            for repeat in range(args.repeats):
                variants = config["variants"]
                offset = repeat % len(variants)
                ordered = {**config, "variants": variants[offset:] + variants[:offset]}
                batch = run(
                    ordered,
                    config_path.parent,
                    args.output,
                    raw_data=archive,
                    case_id=case_id,
                    repeat=repeat + 1,
                    samples=spec["samples"],
                )
                run_count += len(batch)
                print(f"{case_id}: repeat {repeat + 1}/{args.repeats} recorded", flush=True)
    print(f"Added {run_count} raw runs; updated {args.output / 'summary.csv'}")


if __name__ == "__main__":
    main()
