"""Compare mscmap with the external PureMagic scheduler under matched constraints.

Command `compare` of `main.py`.

PureMagic (https://github.com/BQSKit/PureMagic) is an external comparator: this script
runs its released `transpile` and `puremagic` binaries from a local build and never
reimplements its lattice-surgery routing. Results are labeled as outputs of that code
at the recorded commit, not as a reproduction of its paper.

For every circuit, both models get:

- the same Clifford+T gate list (converted once by Qiskit, then mscmap's resolved
  list, Pauli frame included, is written as OpenQASM 2 for PureMagic; its own
  Clifford+T compiler is skipped, so the T count matches);
- the same grid and area N: PureMagic's default bus-routing topology for the qubit
  count with magic nodes reduced to `--msc-count` (extra magic nodes become bus), and
  an mscmap geometry on that grid (`matched_geometry`): magic nodes are the C slots
  holding the MSCs, data nodes are S slots holding the same logical qubits in
  PureMagic's column-major numbering (`mapping: supplied`), and every other node is a
  B tile where (row + column) % 3 == 2, else an empty S slot. PureMagic routes through
  that space; mscmap uses it for exchanges (SWAPs) and local ancillas;
- the same number of fixed magic-state resources (no other C slot, so no relocation);
- the same supply: exponential cultivation time with rate lambda per lcycle, rounded to
  an integer, restarted after each consumption, never interrupted (PureMagic bus
  routing does not route through magic nodes); mscmap `supply.distribution:
  exponential`. `--samples N` runs mscmap samples 0..N-1 and PureMagic seeds 1..N
  (`-r`; without `-R` the seed does not reorder data). Same distribution, different
  random streams. PureMagic's `rand_simple` xorshift puts the seed only in the last of
  five state words, so its first cultivation draws barely depend on the seed and its
  samples are correlated for circuits with few T gates;
- the same lcycle accounting for executed gates (CX 2, S/SDG 3, T 1): PureMagic
  transpiles with weight limit `--width` (default 1, so Cliffords that do not commute
  through are executed as explicit products, as in mscmap); `--width 0` lets its
  tableau absorb every Clifford;
- no T-injection failures (`-F`; mscmap has none).

Remaining model differences (compared, not normalized): PureMagic's transpiler appends
a measurement product per qubit and absorbs every Clifford after the last T on its
qubits into those products, at any `--width` (a T-free circuit executes no Clifford);
it measures multi-patch Pauli products over routed bus paths in one lcycle, uses
double-qubit data patches, and replaces H by its own Clifford products. mscmap removes
final measurements, executes every Clifford through a local contact after SWAPs, and
charges H `costs.single`.

Volume is N x L in both models. N: mscmap S + C + B tiles; PureMagic data qubits + bus
+ magic nodes; both equal the grid size here. L = execution_lcycles: lcycles from time
0 until the last operation finishes (PureMagic: scheduled logical cycles).

Active cells per lcycle (`active_series.csv`, one row per model, sample, and lcycle) use
the same layers in both models: data (patches engaged by an operation; PureMagic: the
qubits of every product in progress, counted from `circuit.schedule`, which repeats
multi-lcycle products), aux (mscmap ancilla/junction tiles; PureMagic routed bus nodes,
plus magic nodes when routing through them), cultivating (magic resources not ready),
holding (ready and unconsumed) and consume (consumed this lcycle). Idle data patches are
not active in either model. The PureMagic bus and magic layers need its per-lcycle
counters, which the binary only prints as averages: apply
`examples/puremagic_stats_csv.patch` (an output hook on `ScheduleStats::update`, no
scheduling change) and rebuild; the hook writes a CSV when `PUREMAGIC_STATS_CSV` is set.
Without the hook, PureMagic rows carry the data layer only and the manifest says so.
PureMagic's own counters skip the data node of a single-qubit Clifford after its first
lcycle, so they are not used for the data layer.
"""

import argparse
import copy
import csv
import json
import os
import re
import statistics
import subprocess
from pathlib import Path
from zipfile import ZipFile

from qasm_benchmark import EVENTS_PER_GATE, ROOT, SIZE_CLASSES, VARIANTS, circuit_stats, discover

from experiments import RawDataWriter, load_document, resolve, run
from metrics import ACTIVE_LAYERS, decode_series
from model import PUREMAGIC_RATE
from qiskit_io import load_qasm, qiskit_version

GENERATOR_VERSION = "puremagic-compare-v3"
STATS_HOOK = "PUREMAGIC_STATS_CSV"  # see examples/puremagic_stats_csv.patch
QASM_NAMES = {k: k.lower() for k in ("H", "S", "SDG", "X", "Z", "T", "TDG", "CX", "CZ")}
COLUMNS = (
    "case_id",
    "model",
    "sample",
    "status",
    "qubits",
    "t_count",
    "magic_resources",
    "n_total",
    "n_breakdown",
    "execution_lcycles",
    "spacetime_volume",
    "spacetime_volume_us",
    "volume_lower_bound",
    "active_volume",
    "mean_active_cells",
    "peak_active_cells",
    "active_utilization",
)
SERIES_COLUMNS = ("case_id", "model", "sample", "lcycle", *ACTIVE_LAYERS, "total", "fraction_of_n")
MEDIAN_COLUMNS = (
    "case_id",
    "model",
    "samples",
    "completed",
    "qubits",
    "t_count",
    "n_total",
    "execution_lcycles_median",
    "execution_lcycles_min",
    "execution_lcycles_max",
    "spacetime_volume_median",
    "active_volume_median",
    "active_utilization_median",
)
SCHEDULE_LINE = re.compile(r"^\s*(\d+): (\S+) ")
SCHEDULED = re.compile(r"Scheduled (\d+) in (\d+) logical cycles, volume (\d+)")
ESTIMATE = re.compile(r"^Volume estimate: (\d+)", re.MULTILINE)
# Node counts ("  data:  8 (0.121)"); utilization lines ("  data:  0.220") do not match.
COUNT = re.compile(r"^\s+(data|bus|magic|total):\s+(\d+)\s*(?:\(|$)", re.MULTILINE)


def to_qasm(circuit: dict) -> str:
    """OpenQASM 2 in program order; qubits are indexed by `circuit["qubits"]`."""
    index = {q: i for i, q in enumerate(circuit["qubits"])}
    lines = ["OPENQASM 2.0;", 'include "qelib1.inc";', f"qreg q[{len(index)}];"]
    for gate in circuit["gates"]:
        operands = ",".join(f"q[{index[q]}]" for q in gate["qubits"])
        lines.append(f"{QASM_NAMES[gate['kind']]} {operands};")
    return "\n".join(lines) + "\n"


def run_binary(command: list[str], cwd: Path, log: Path, env: dict[str, str] | None = None) -> str:
    """Run an external binary; keep its combined output and fail with that log."""
    done = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, check=False, env=env
    )
    log.write_text(f"$ {' '.join(command)}\n{done.stdout}{done.stderr}")
    if done.returncode != 0:
        raise RuntimeError(f"{command[0]} failed ({done.returncode}); see {log}")
    return done.stdout


def default_topology(binaries: Path, qubits: int, work: Path) -> list[list[str]]:
    """PureMagic's own default topology for `qubits`, dumped from a one-product circuit."""
    stem = "topology_probe"
    # One Z measurement per qubit so PureMagic sizes the topology for every qubit.
    lines = ("+" + "_" * q + "Z" + "_" * (qubits - q - 1) + "<M>" for q in range(qubits))
    (work / f"{stem}.trans").write_text("\n".join(lines) + "\n")
    run_binary(
        [str(binaries / "puremagic"), "-c", f"{stem}.trans", "-r", "0", "-F", "-p", "topo"],
        work,
        work / f"{stem}.log",
    )
    return [line.split() for line in (work / f"{stem}.topo.txt").read_text().splitlines() if line]


def limit_magic(grid: list[list[str]], count: int) -> list[list[str]]:
    """Keep `count` magic nodes spread clockwise around the border; others become bus.

    Interior magic nodes (unused data positions in PureMagic's generator) are kept only
    if the border has fewer than `count` magic nodes."""
    rows, cols = len(grid), len(grid[0])
    border = (
        [(0, c) for c in range(cols)]
        + [(r, cols - 1) for r in range(1, rows)]
        + [(rows - 1, c) for c in reversed(range(cols - 1))]
        + [(r, 0) for r in reversed(range(1, rows - 1))]
    )
    on_border = [t for t in border if grid[t[0]][t[1]] == "m"]
    interior = [
        (r, c)
        for r in range(rows)
        for c in range(cols)
        if grid[r][c] == "m" and (r, c) not in set(on_border)
    ]
    if count > len(on_border) + len(interior):
        raise ValueError(f"topology has only {len(on_border) + len(interior)} magic nodes")
    keep = {on_border[j * len(on_border) // count] for j in range(min(count, len(on_border)))}
    keep |= set(interior[: count - len(keep)])
    return [
        ["b" if token == "m" and (r, c) not in keep else token for c, token in enumerate(row)]
        for r, row in enumerate(grid)
    ]


def matched_geometry(grid: list[list[str]], qubits: list[str]) -> tuple[list, dict, dict]:
    """mscmap tiles, placement, and MSC sites on PureMagic's grid (see module docstring)."""
    tiles = [
        "".join(
            "C" if token == "m" else "S" if token[0] == "d" or (r + c) % 3 != 2 else "B"
            for c, token in enumerate(row)
        )
        for r, row in enumerate(grid)
    ]
    cells = [(r, c) for c in range(len(grid[0])) for r in range(len(grid))]  # column-major
    data = [t for t in cells if grid[t[0]][t[1]][0] == "d"]
    magic = [t for t in cells if grid[t[0]][t[1]] == "m"]
    if len(data) < len(qubits):
        raise ValueError(f"topology has {len(data)} data nodes for {len(qubits)} qubits")
    placement = {q: list(t) for q, t in zip(qubits, data)}
    return tiles, placement, {f"m{i}": list(t) for i, t in enumerate(magic)}


def case_config(common: dict, circuit: dict, grid: list, args: argparse.Namespace) -> dict:
    tiles, placement, mscs = matched_geometry(grid, circuit["qubits"])
    config = copy.deepcopy(common)
    config.pop("workload", None)
    config.update(geometry={"tiles": tiles}, placement=placement, mscs=mscs, circuit=circuit)
    config.update(mapping="supplied", supply={"distribution": "exponential", "rate": args.lam})
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


def run_puremagic(
    binaries: Path, circuit: dict, grid: list, args: argparse.Namespace, work: Path
) -> list[dict]:
    """Transpile once, then schedule once per seed 1..samples."""
    (work / "circuit.cliffordt.qasm").write_text(to_qasm(circuit))
    run_binary(
        [
            str(binaries / "transpile"),
            *("-i", "circuit.cliffordt.qasm", "-o", "circuit.trans", "-m", str(args.width)),
        ],
        work,
        work / "transpile.log",
    )
    (work / "topology.txt").write_text("\n".join("  ".join(row) for row in grid) + "\n")
    results = []
    for seed in range(1, args.samples + 1):
        stats = work / f"puremagic.r{seed}.stats.csv"
        out = run_binary(
            [
                str(binaries / "puremagic"),
                *("-c", "circuit.trans", "-t", "topology.txt", "-F"),
                *("-r", str(seed), "-m", repr(args.lam)),
            ],
            work,
            work / f"puremagic.r{seed}.log",
            {**os.environ, STATS_HOOK: stats.name},
        )
        scheduled = SCHEDULED.search(out)
        if scheduled is None:
            raise RuntimeError(f"no schedule summary in {work / f'puremagic.r{seed}.log'}")
        counts = {kind: int(n) for kind, n in COUNT.findall(out)}
        if counts.get("magic") != args.msc_count:
            raise RuntimeError(f"PureMagic reports {counts.get('magic')} magic nodes")
        estimate = ESTIMATE.search(out)
        results.append(
            {
                "lcycles": int(scheduled[2]),
                "volume": int(scheduled[3]),
                "counts": counts,
                "volume_lower_bound": int(estimate[1]) if estimate else None,
                # circuit.schedule is rewritten per seed: read it now.
                "series": puremagic_series(
                    work / "circuit.schedule", stats, int(scheduled[2]), args.msc_count
                ),
            }
        )
    return results


def puremagic_series(
    schedule: Path, stats: Path, lcycles: int, magic: int
) -> dict[str, list[int]]:
    """Active cells per lcycle 1..lcycles by layer (see module docstring); bus and magic
    layers are empty lists when the stats hook did not write `stats`."""
    data = [0] * lcycles
    for line in schedule.read_text().splitlines():
        found = SCHEDULE_LINE.match(line)
        if found and int(found[1]) <= lcycles:
            data[int(found[1]) - 1] = sum(c != "_" for c in found[2])
    series = {"data": data, **{layer: [] for layer in ACTIVE_LAYERS if layer != "data"}}
    if not stats.is_file():
        return series
    for layer in series:
        if layer != "data":
            series[layer] = [0] * lcycles
    with stats.open(newline="") as stream:
        for row in csv.DictReader(stream):
            t = int(row["lcycle"]) - 1
            if not 0 <= t < lcycles:
                continue
            ready, consumed = int(row["magic_ready"]), int(row["t_scheduled"])
            series["aux"][t] = int(row["bus_used"]) + int(row["magic_used"])
            series["cultivating"][t] = magic - ready
            series["holding"][t] = ready - consumed
            series["consume"][t] = consumed
    return series


def series_rows(case_id: str, model: str, sample: int, series: dict, n_total: int) -> list[dict]:
    """One `active_series.csv` row per lcycle; missing layers are blank."""
    rows = []
    for t in range(len(series["data"])):
        values = {layer: series[layer][t] if series[layer] else "" for layer in ACTIVE_LAYERS}
        total = sum(v for v in values.values() if v != "")
        rows.append(
            {
                "case_id": case_id,
                "model": model,
                "sample": sample,
                "lcycle": t,
                **values,
                "total": total,
                "fraction_of_n": round(total / n_total, 6),
            }
        )
    return rows


def active_totals(series: dict, lcycles: int, n_total: int) -> dict:
    """Totals of the layers present: cell-lcycles, mean and peak cells, utilization."""
    present = [series[layer] for layer in ACTIVE_LAYERS if series[layer]]
    total = [sum(layer[t] for layer in present) for t in range(lcycles)]
    volume = sum(total)
    return {
        "active_volume": volume,
        "mean_active_cells": round(volume / lcycles, 4) if lcycles else 0,
        "peak_active_cells": max(total, default=0),
        "active_utilization": round(volume / (n_total * lcycles), 6) if lcycles else 0,
    }


def mscmap_series_rows(archive: Path) -> list[dict]:
    """Rows for every mscmap run in the archive, decoded from `active_series`."""
    rows = []
    with ZipFile(archive) as data, data.open("raw_data.jsonl") as records:
        for line in records:
            record = json.loads(line)
            if record["type"] != "run":
                continue
            metrics = record["metrics"]
            series = {k: decode_series(v) for k, v in metrics["active_series"].items()}
            rows += series_rows(
                record["case_id"],
                f"mscmap/{record['name']}",
                record["sample"],
                series,
                metrics["provisioned_area"],
            )
    return rows


def medians(rows: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        groups.setdefault((row["case_id"], row["model"]), []).append(row)
    summary = []
    for (case_id, model), group in groups.items():
        done = [r for r in group if r["status"] == "completed"]
        lcycles = [r["execution_lcycles"] for r in done]
        summary.append(
            {
                "case_id": case_id,
                "model": model,
                "samples": len(group),
                "completed": len(done),
                "qubits": group[0].get("qubits", ""),
                "t_count": group[0].get("t_count", ""),
                "n_total": group[0].get("n_total", ""),
                "execution_lcycles_median": statistics.median(lcycles) if done else "",
                "execution_lcycles_min": min(lcycles, default=""),
                "execution_lcycles_max": max(lcycles, default=""),
                "spacetime_volume_median": (
                    statistics.median(r["spacetime_volume"] for r in done) if done else ""
                ),
                "active_volume_median": (
                    statistics.median(r["active_volume"] for r in done) if done else ""
                ),
                "active_utilization_median": (
                    round(statistics.median(r["active_utilization"] for r in done), 6)
                    if done
                    else ""
                ),
            }
        )
    return summary


def write_csv(path: Path, columns: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def git_revision(path: Path) -> str | None:
    done = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return done.stdout.strip() or None


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", type=Path, required=True, help="new or empty directory")
    parser.add_argument("--puremagic", type=Path, required=True, help="PureMagic checkout")
    parser.add_argument("--qasm", type=Path, action="append", default=[], help="repeatable")
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--size", action="append", choices=SIZE_CLASSES, dest="sizes")
    parser.add_argument("--circuit", action="append", dest="stems", help="data file stem")
    parser.add_argument("--variant", action="append", choices=VARIANTS, dest="variants")
    parser.add_argument("--msc-count", type=int, default=2)
    parser.add_argument("--samples", type=int, default=5, help="stochastic samples per model")
    parser.add_argument("--width", type=int, default=1, help="PureMagic transpile weight limit")
    parser.add_argument("--rollout-gates", type=int, default=32, help="0 = remaining circuit")
    parser.add_argument("--event-limit", type=int, help="default: 50 x gates")
    parser.add_argument(
        "--lambda", type=float, dest="lam", default=PUREMAGIC_RATE, help="per lcycle"
    )


def execute(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if min(args.msc_count, args.samples) < 1 or args.width < 0 or args.lam <= 0:
        parser.error("--msc-count and --samples must be positive, --width >= 0, --lambda > 0")
    binaries = args.puremagic.resolve() / "target/release"  # binaries run in case dirs
    if not all((binaries / name).is_file() for name in ("transpile", "puremagic")):
        parser.error(f"build PureMagic first: no transpile/puremagic in {binaries}")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"{args.output} must be new or empty")
    found = [("file", path) for path in args.qasm]
    if args.sizes or args.stems or not found:
        found += discover(args.data, args.sizes or list(SIZE_CLASSES), args.stems)

    config_path = ROOT / "configs/experiment.yaml"
    common = load_document(config_path)
    rows: list[dict] = []
    series: list[dict] = []
    hooked = True
    with RawDataWriter(args.output / "mscmap") as archive:
        for size, path in found:
            case_id = f"{size}/{path.stem}"
            circuit = load_qasm(path)
            stats = circuit_stats(circuit)
            work = args.output / "puremagic" / case_id.replace("/", "__")
            work.mkdir(parents=True)
            grid = limit_magic(default_topology(binaries, stats["qubits"], work), args.msc_count)
            config = case_config(common, circuit, grid, args)
            problem, resolved = resolve(config, config_path.parent)
            area = problem.geometry.area
            batch = run(
                config,
                config_path.parent,
                args.output / "mscmap",
                samples=args.samples,
                raw_data=archive,
                case_id=case_id,
            )
            for row in batch:
                rows.append(
                    {
                        "case_id": case_id,
                        "model": f"mscmap/{row['name']}",
                        "sample": row["sample"],
                        "status": row["status"],
                        "qubits": stats["qubits"],
                        "t_count": stats["t_count"],
                        "magic_resources": row["msc_pool_size"],
                        "n_total": area["total"],
                        "n_breakdown": f"S={area['slot']} C={area['cultivation']} "
                        f"B={area['bus']}",
                        "execution_lcycles": row["execution_lcycles"],
                        "spacetime_volume": row["spacetime_volume"],
                        "spacetime_volume_us": row["spacetime_volume_us"],
                        "volume_lower_bound": "",
                        "active_volume": row["active_volume"],
                        "mean_active_cells": round(
                            row["active_volume"] / row["execution_lcycles"], 4
                        ),
                        "peak_active_cells": row["peak_active_tiles"],
                        "active_utilization": round(row["active_utilization"], 6),
                    }
                )
            lcycle_us = resolved["timing"]["lcycle_us"]
            try:
                results = run_puremagic(binaries, resolved["circuit"], grid, args, work)
            except (RuntimeError, ValueError, OSError) as exc:
                print(f"{case_id}: PureMagic failed: {exc}", flush=True)
                rows.append({"case_id": case_id, "model": "PureMagic", "status": "failed"})
                continue
            for seed, result in enumerate(results, start=1):
                counts = result["counts"]
                hooked &= bool(result["series"]["aux"])
                series.extend(
                    series_rows(case_id, "PureMagic", seed, result["series"], counts["total"])
                )
                rows.append(
                    {
                        "case_id": case_id,
                        "model": "PureMagic",
                        "sample": seed,
                        "status": "completed",
                        "qubits": stats["qubits"],
                        "t_count": stats["t_count"],
                        "magic_resources": counts["magic"],
                        "n_total": counts["total"],
                        "n_breakdown": f"data={counts['data']} bus={counts['bus']} "
                        f"magic={counts['magic']}",
                        "execution_lcycles": result["lcycles"],
                        "spacetime_volume": result["volume"],
                        "spacetime_volume_us": result["volume"] * lcycle_us,
                        "volume_lower_bound": result["volume_lower_bound"],
                        **active_totals(result["series"], result["lcycles"], counts["total"]),
                    }
                )
            print(f"{case_id}: done", flush=True)

    write_csv(args.output / "comparison.csv", COLUMNS, rows)
    write_csv(args.output / "comparison_median.csv", MEDIAN_COLUMNS, medians(rows))
    series = mscmap_series_rows(args.output / "mscmap" / "raw_data.zip") + series
    write_csv(args.output / "active_series.csv", SERIES_COLUMNS, series)
    manifest = {
        "generator_version": GENERATOR_VERSION,
        "puremagic_commit": git_revision(args.puremagic),
        "transpile_flags": {"-m": args.width},
        "puremagic_flags": {"-F": "no T-injection failures", "-m": args.lam, "-r": "1..N"},
        "mscmap_supply": {"distribution": "exponential", "rate": args.lam},
        "samples": args.samples,
        "magic_nodes": args.msc_count,
        "topology": "PureMagic default bus-routing topology, extra magic nodes -> bus; mscmap "
        "geometry on the same grid (magic -> C, data -> S, other -> B if (r+c)%3==2 else S)",
        "mapping": "supplied (PureMagic column-major data order)",
        "qiskit": qiskit_version(),
        "cases": sorted({row["case_id"] for row in rows}),
        "volume_definitions": {
            "mscmap": "(S + C + B tiles) x execution_lcycles",
            "PureMagic": "(data qubits + bus + magic nodes) x scheduled lcycles; "
            "volume_lower_bound is its pre-scheduling estimate",
        },
        "active_series": {
            "layers": list(ACTIVE_LAYERS),
            "mscmap": "cells of active operations per lcycle from the realized trace",
            "PureMagic": "data from circuit.schedule; aux/cultivating/holding/consume from "
            f"the {STATS_HOOK} hook (examples/puremagic_stats_csv.patch)",
            "puremagic_stats_hook": hooked,
            "fraction_of_n": "total active cells / N of that model",
        },
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {args.output / 'comparison.csv'}")

