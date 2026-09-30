"""Benchmark the policies on OpenQASM circuits stored as `<data>/<size class>/*.qasm`.

Each circuit is converted to Clifford+T once (Qiskit, see `qiskit_io`) and placed on
a brick layout sized to its qubit count (`workload_sweep.brick_geometry`: data
qubits in row-major supplied order, MSCs spread from opposite corners). Costs,
supply, objective, and base settings come from `configs/experiment.yaml`.

Every circuit compares FixedSite with JointScheduler (idle-only MSC relocation) from
the same initial placement, `demand_aware` by default (`--mapping supplied` keeps the
row-major order). The demand-aware heuristic also chooses the initial MSC sites;
FixedSite keeps those sites for the whole run.

Search bounds are benchmark choices, recorded in the archive: `rollout_gates` bounds
each JointScheduler rollout, and `event_limit` scales with the gate count so long
circuits are not truncated by the default limit. Circuits above `--max-gates` are
skipped before execution and listed in the manifest, never dropped silently.
"""

import argparse
import copy
import hashlib
from collections import Counter
from math import ceil, sqrt
from pathlib import Path

from workload_sweep import brick_geometry

from experiments import RawDataWriter, load_document, resolve, run
from ir import CircuitDAG
from mapping import MAPPINGS
from qiskit_io import load_qasm, qiskit_version

ROOT = Path(__file__).resolve().parents[1]
SIZE_CLASSES = ("small", "medium", "large")
EVENTS_PER_GATE = 50
# Relocation is explicit; the simulator admits it only for idle MSCs before initialization.
VARIANTS = {
    "FixedSite": {"policy": "FixedSite"},
    "JointScheduler": {"policy": "JointScheduler", "settings": {"relocation": True}},
}


def discover(data: Path, sizes: list[str], stems: list[str] | None) -> list[tuple[str, Path]]:
    """(size class, path) in size order, then file name; `stems` selects circuits."""
    found = [(size, path) for size in sizes for path in sorted((data / size).glob("*.qasm"))]
    if stems:
        unknown = set(stems) - {path.stem for _, path in found}
        if unknown:
            raise ValueError(f"unknown circuits under {data}: {sorted(unknown)}")
        found = [(size, path) for size, path in found if path.stem in stems]
    if not found:
        raise ValueError(f"no .qasm files under {data} for sizes {', '.join(sizes)}")
    return found


def circuit_stats(circuit: dict) -> dict:
    kinds = Counter(gate["kind"] for gate in circuit["gates"])
    dag = CircuitDAG.from_dict(circuit)
    depth: dict[str, int] = {}
    for gate in dag.gates:
        depth[gate.id] = 1 + max((depth[p] for p in gate.predecessors), default=0)
    return {
        "qubits": len(circuit["qubits"]),
        "gates": len(circuit["gates"]),
        "t_count": kinds["T"] + kinds["TDG"],
        "two_qubit": kinds["CX"] + kinds["CZ"],
        "depth": max(depth.values(), default=0),
        "kinds": dict(sorted(kinds.items())),
    }


def case_config(common: dict, circuit: dict, args: argparse.Namespace) -> dict:
    qubits = len(circuit["qubits"])
    slot_rows = args.slot_rows or ceil(sqrt(qubits))  # near-square data region
    tiles, placement, mscs = brick_geometry(qubits, args.msc_count, slot_rows, 0)
    config = copy.deepcopy(common)
    config.pop("workload", None)
    config.update(geometry={"tiles": tiles}, placement=placement, mscs=mscs, circuit=circuit)
    config["mapping"] = args.mapping
    settings = config.get("settings", {})
    event_limit = args.event_limit or max(
        settings.get("event_limit", 1), EVENTS_PER_GATE * len(circuit["gates"])
    )
    config["settings"] = {
        **settings,
        "rollout_gates": args.rollout_gates,
        "event_limit": event_limit,
    }
    config["variants"] = [VARIANTS[name] for name in args.variants or VARIANTS]
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--size", action="append", choices=SIZE_CLASSES, dest="sizes")
    parser.add_argument("--circuit", action="append", dest="stems", help="file stem; repeatable")
    parser.add_argument("--variant", action="append", choices=VARIANTS, dest="variants")
    parser.add_argument("--mapping", choices=MAPPINGS, default="demand_aware")
    parser.add_argument("--msc-count", type=int, default=2)
    parser.add_argument("--slot-rows", type=int, help="data rows (default: ceil(sqrt(qubits)))")
    parser.add_argument("--rollout-gates", type=int, default=32, help="0 = remaining circuit")
    parser.add_argument("--event-limit", type=int, help=f"default: {EVENTS_PER_GATE} x gates")
    parser.add_argument("--max-gates", type=int, help="skip larger converted circuits")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--samples", type=int, default=1)
    args = parser.parse_args()
    if min(args.repeats, args.samples, args.msc_count) < 1:
        parser.error("--repeats, --samples, and --msc-count must be positive")
    try:
        found = discover(args.data, args.sizes or list(SIZE_CLASSES), args.stems)
    except ValueError as exc:
        parser.error(str(exc))

    config_path = ROOT / "configs/experiment.yaml"
    common = load_document(config_path)
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
        config = case_config(common, circuit, args)
        resolve(config, config_path.parent)
        source = {
            "path": str(path.relative_to(args.data)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_class": size,
        }
        prepared.append((case_id, config, {"source": source, "circuit_stats": stats}))

    manifest = {
        "generator_version": "qasm-benchmark-v2",
        "qiskit": qiskit_version(),
        "repeats": args.repeats,
        "samples": args.samples,
        "msc_count": args.msc_count,
        "mapping": args.mapping,
        "rollout_gates": args.rollout_gates,
        "event_limit": args.event_limit or f"max(config, {EVENTS_PER_GATE} x gates)",
        "planning_seconds": "median over timing repeats, per case/sample/variant",
        "order": "variant order rotated by repeat index",
        "cases": [case_id for case_id, _, _ in prepared],
        "skipped": skipped,
    }
    run_count = 0
    with RawDataWriter(args.output) as archive:
        archive.write({"type": "manifest", **manifest})
        for case_id, config, details in prepared:
            archive.write({"type": "case", "case_id": case_id, "input": config, **details})
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
                    samples=args.samples,
                )
                run_count += len(batch)
                statuses = ", ".join(f"{row['name']}={row['status']}" for row in batch)
                print(f"{case_id}: repeat {repeat + 1}/{args.repeats}: {statuses}", flush=True)
    print(
        f"Added {run_count} raw runs; skipped {len(skipped)} circuits; "
        f"updated {args.output / 'summary.csv'}"
    )


if __name__ == "__main__":
    main()
