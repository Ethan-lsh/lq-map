"""Controlled workload cases using the common engine and repeated planning timings."""

import argparse
import copy
import random
from pathlib import Path

from mscmap.experiments import RawDataWriter, load_document, resolve, run, workload


def case_specs() -> list[dict]:
    """Predeclared one-factor cases followed by two combined cases; no result filtering."""
    base = {
        "family": "alternating", "requests": 6, "dependency": "independent",
        "cx_every": 0, "data_qubits": 4, "placement": "reference", "seed": 7,
    }
    changes = [
        ("reference", {}),
        ("localized", {"family": "localized"}),
        ("migrating", {"family": "migrating"}),
        ("distributed", {"family": "distributed"}),
        ("serial", {"dependency": "serial"}),
        ("fork_join", {"dependency": "fork_join"}),
        ("cx_sparse", {"cx_every": 3}),
        ("cx_dense", {"cx_every": 1}),
        ("placement_near", {"placement": "near"}),
        ("placement_far", {"placement": "far"}),
        ("placement_asymmetric", {"placement": "asymmetric"}),
        ("placement_random_7", {"placement": "random"}),
        ("placement_random_19", {"placement": "random", "seed": 19}),
        ("placement_random_31", {"placement": "random", "seed": 31}),
        ("more_requests", {"requests": 12}),
        ("more_qubits", {"data_qubits": 6}),
        ("combined_migrating", {
            "family": "migrating", "dependency": "fork_join", "cx_every": 2,
        }),
        ("combined_distributed", {
            "family": "distributed", "dependency": "serial", "cx_every": 1,
            "placement": "random", "seed": 19,
        }),
    ]
    return [{"case_id": name, **base, **change} for name, change in changes]


def case_config(common: dict, spec: dict) -> dict:
    config = copy.deepcopy(common)
    count = spec["data_qubits"]
    if type(count) is not int or count < 2 or count % 2:
        raise ValueError("case data_qubits must be an even integer at least two")
    # Exactly N data cells + 2 MSC cells, on a two-row grid.
    cells = [[x, y] for x in range(1, count // 2 + 1) for y in range(2)]
    reference = {f"q{i}": c for i, c in enumerate(cells)}
    config["layout"] = {"usable": [[0, 0], [0, 1], *cells], "blocked": []}
    config["mscs"] = {"m0": [0, 0], "m1": [0, 1]}
    config["mode"] = "serialized"
    config["seed"] = spec["seed"]
    config.pop("circuit", None)
    config.pop("workload", None)
    workload_spec = {k: spec[k] for k in ("family", "requests", "dependency", "cx_every")}
    # Freeze gate identities BEFORE applying a placement treatment.
    config["circuit"] = workload(workload_spec, reference, spec["seed"])
    positions = list(cells)
    names = list(reference)
    treatment = spec["placement"]
    if treatment in {"near", "far"}:
        targets = list(dict.fromkeys(
            g["qubits"][0] for g in config["circuit"]["gates"] if g["kind"] == "T"
        ))
        names = targets + [q for q in names if q not in targets]
        positions.sort(key=lambda c: (-c[0] if treatment == "far" else c[0], c[1]))
    elif treatment == "asymmetric":
        positions[1], positions[-1] = positions[-1], positions[1]
    elif treatment == "random":
        random.Random(spec["seed"]).shuffle(positions)
    elif treatment != "reference":
        raise ValueError(f"unknown placement treatment: {treatment}")
    config["placement"] = dict(zip(names, positions))
    config["variants"] = [{"policy": p} for p in (
        "FixedSite", "NextRequestGreedy", "JointScheduler"
    )]
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
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
        "generator_version": "controlled-workloads-v1", "repeats": args.repeats,
        "planning_seconds": "median of completed repeat measurements, including partial outcomes",
        "order": "policy order rotated by repeat index", "cases": specs,
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
                    ordered, config_path.parent, args.output,
                    raw_data=archive, case_id=case_id, repeat=repeat + 1,
                )
                run_count += len(batch)
                print(f"{case_id}: repeat {repeat + 1}/{args.repeats} recorded", flush=True)
    print(f"Added {run_count} raw runs; updated {args.output / 'summary.csv'}")


if __name__ == "__main__":
    main()
