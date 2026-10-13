"""Plot the per-lcycle active-cell series of every circuit as PNG figures.

Command `plot` of `main.py`: `plot INPUT --output DIR [--sample K] [--case ID]`.
INPUT is either a `run` output directory (`raw_data.zip`, series
decoded from each run record's `active_series`) or a `puremagic_compare.py` output
directory (`active_series.csv` + `comparison.csv`, mscmap and PureMagic side by side)
of `compare`.
Per circuit one figure stacks the layers (data, aux, cultivating, holding, consume)
as a fraction of N for each model, using the completed sample with the median
`execution_lcycles` unless `--sample` picks one; `overview_fraction.png` overlays the
total fraction of every model per circuit. Requires matplotlib (`pip install
'mscmap[plot]'`). Figures only visualize recorded series; nothing is recomputed.
"""

import argparse
import csv
import json
from pathlib import Path
from zipfile import ZipFile

from metrics import ACTIVE_LAYERS, decode_series

LAYER_COLORS = dict(zip(ACTIVE_LAYERS, ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4")))
MODEL_ORDER = (
    "PureMagic", "mscmap/FixedSite", "mscmap/JointScheduler", "FixedSite", "JointScheduler"
)
MODEL_COLORS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6")
INK, INK_SOFT, SURFACE, GRID = "#0b0b0b", "#52514e", "#fcfcfb", "#e6e5e1"
STYLE = {
    "font.size": 9,
    "axes.edgecolor": INK_SOFT,
    "axes.labelcolor": INK_SOFT,
    "xtick.color": INK_SOFT,
    "ytick.color": INK_SOFT,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "axes.grid": True,
    "grid.color": GRID,
    "grid.linewidth": 0.6,
    "axes.axisbelow": True,
}


def load_compare(folder: Path) -> tuple[dict, dict]:
    """(runs, series) from puremagic_compare.py output."""
    runs, series = {}, {}
    with (folder / "comparison.csv").open() as f:
        for row in csv.DictReader(f):
            runs[(row["case_id"], row["model"], int(row["sample"]))] = {
                "status": row["status"],
                "lcycles": int(row["execution_lcycles"] or 0),
                "area": int(row["n_total"]),
                "active_volume": row["active_volume"],
                "active_utilization": row["active_utilization"],
            }
    with (folder / "active_series.csv").open() as f:
        for row in csv.DictReader(f):
            key = (row["case_id"], row["model"], int(row["sample"]))
            series.setdefault(key, []).append({k: int(row[k]) for k in (*ACTIVE_LAYERS, "total")})
    return runs, series


def load_archive(folder: Path) -> tuple[dict, dict]:
    """(runs, series) from a `mscmap run` output directory."""
    runs, series = {}, {}
    with ZipFile(folder / "raw_data.zip") as data, data.open("raw_data.jsonl") as records:
        for line in records:
            record = json.loads(line)
            if record["type"] != "run":
                continue
            metrics = record["metrics"]
            key = (record["case_id"], record["name"], record["sample"])
            runs[key] = {
                "status": metrics["status"],
                "lcycles": metrics["execution_lcycles"],
                "area": metrics["provisioned_area"],
                "active_volume": metrics["active_volume"],
                "active_utilization": metrics["active_utilization"],
            }
            layers = {k: decode_series(v) for k, v in metrics["active_series"].items()}
            series[key] = []
            for t in range(metrics["execution_lcycles"]):
                row = {k: layers[k][t] for k in ACTIVE_LAYERS}
                series[key].append({**row, "total": sum(row.values())})
    return runs, series


def pick_sample(runs: dict, case: str, model: str, wanted: int | None) -> int | None:
    """`wanted` if it completed, else the completed sample with median execution_lcycles."""
    done = sorted(
        (r["lcycles"], s) for (c, m, s), r in runs.items()
        if c == case and m == model and r["status"] == "completed"
    )
    if wanted is not None:
        return wanted if any(s == wanted for _, s in done) else None
    return done[len(done) // 2][1] if done else None


def model_rank(model: str) -> tuple[int, str]:
    return (MODEL_ORDER.index(model) if model in MODEL_ORDER else len(MODEL_ORDER), model)


def circuit_figure(
    plt, case: str, models: list[str], runs: dict, series: dict, wanted, out: Path
):
    size = (9, 2.2 * len(models) + 0.8)
    fig, axes = plt.subplots(len(models), 1, figsize=size, sharex=True, sharey=True, squeeze=False)
    top = 0.0
    for ax, model in zip(axes[:, 0], models):
        sample = pick_sample(runs, case, model, wanted)
        if sample is None:
            ax.text(0.5, 0.5, "no completed sample", ha="center", transform=ax.transAxes, color=INK)
            ax.set_title(model, loc="left", fontsize=9, color=INK)
            continue
        run, rows = runs[(case, model, sample)], series[(case, model, sample)]
        t = list(range(len(rows)))
        fractions = [[row[k] / run["area"] for row in rows] for k in ACTIVE_LAYERS]
        ax.stackplot(t, *fractions, labels=ACTIVE_LAYERS, step="post", linewidth=0,
                     colors=[LAYER_COLORS[k] for k in ACTIVE_LAYERS], alpha=0.9)
        top = max(top, max(row["total"] for row in rows) / run["area"])
        ax.set_title(
            f"{model}  (sample {sample}: L={run['lcycles']}, N={run['area']}, "
            f"active_volume={run['active_volume']}, "
            f"active_utilization={float(run['active_utilization']):.3f})",
            loc="left", fontsize=9, color=INK,
        )
        ax.set_ylabel("active cells / N")
    axes[0, 0].set_ylim(0, min(1.0, top * 1.15 + 0.02))
    axes[0, 0].set_xlim(left=0)
    axes[-1, 0].set_xlabel("lcycle")
    for ax in axes[:, 0]:
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            fig.legend(handles, labels, loc="upper right", ncol=len(labels), frameon=False,
                       fontsize=8)
            break
    title = f"{case}: active cells per lcycle by layer"
    fig.suptitle(title, x=0.01, ha="left", fontsize=11, color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out, dpi=140)
    plt.close(fig)


def overview_figure(
    plt, cases: list[str], models: list[str], runs: dict, series: dict, wanted, out: Path
):
    columns = min(3, len(cases))
    lines = (len(cases) + columns - 1) // columns
    size = (max(9.0, 4.2 * columns), 2.6 * lines + 1.0)
    fig, axes = plt.subplots(lines, columns, figsize=size, squeeze=False)
    colors = dict(zip(models, MODEL_COLORS))
    for ax in axes.flat:
        ax.set_visible(False)
    for ax, case in zip(axes.flat, cases):
        ax.set_visible(True)
        for model in models:
            sample = pick_sample(runs, case, model, wanted)
            if sample is None:
                continue
            area, rows = runs[(case, model, sample)]["area"], series[(case, model, sample)]
            fraction = [row["total"] / area for row in rows]
            ax.plot(range(len(rows)), fraction, color=colors[model], linewidth=1.2, label=model,
                    drawstyle="steps-post")
        ax.set_title(case, loc="left", fontsize=9, color=INK)
        top = max((max(line.get_ydata()) for line in ax.lines), default=0.0)
        ax.set_ylim(0, min(1.0, top * 1.15 + 0.02))
        ax.set_xlim(left=0)
        ax.set_xlabel("lcycle")
        ax.set_ylabel("active / N")
    handles, labels = {}, []
    for ax in axes.flat:
        for handle, label in zip(*ax.get_legend_handles_labels()):
            handles.setdefault(label, handle)
    fig.legend(
        handles.values(), handles.keys(), loc="upper left", bbox_to_anchor=(0.01, 0.955),
        ncol=len(handles), frameon=False, fontsize=9,
    )
    fig.suptitle("Total active-cell fraction per lcycle", x=0.01, ha="left", fontsize=11, color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(out, dpi=140)
    plt.close(fig)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("input", type=Path, help="mscmap run output or puremagic_compare.py output")
    parser.add_argument("--output", type=Path, required=True, help="directory for the PNG files")
    parser.add_argument("--sample", type=int, help="plot this sample instead of the median-L one")
    parser.add_argument("--case", action="append", dest="cases", help="restrict to these case ids")


def execute(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    try:
        import matplotlib
        import matplotlib.pyplot as plt
    except ImportError:
        parser.error("matplotlib is required: pip install 'mscmap[plot]'")
    matplotlib.use("Agg")
    if (args.input / "active_series.csv").is_file():
        runs, series = load_compare(args.input)
    elif (args.input / "raw_data.zip").is_file():
        runs, series = load_archive(args.input)
    else:
        parser.error(f"{args.input} holds neither active_series.csv nor raw_data.zip")
    cases = sorted({c for c, _, _ in runs})
    if args.cases:
        unknown = set(args.cases) - set(cases)
        if unknown:
            parser.error(f"unknown cases {sorted(unknown)}; available: {cases}")
        cases = [c for c in cases if c in args.cases]
    models = sorted({m for _, m, _ in runs}, key=model_rank)
    args.output.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update(STYLE)
    for case in cases:
        png = args.output / f"{case.replace('/', '_')}.png"
        circuit_figure(plt, case, models, runs, series, args.sample, png)
    overview = args.output / "overview_fraction.png"
    overview_figure(plt, cases, models, runs, series, args.sample, overview)
    print(f"wrote {len(cases)} circuit figure(s) and overview_fraction.png to {args.output}")

