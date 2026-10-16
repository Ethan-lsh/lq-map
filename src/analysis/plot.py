"""Plot the per-lcycle active-cell series of every circuit as PNG figures.

Command `plot` of `main.py`: `plot INPUT --output DIR [--sample K] [--case ID]`.
INPUT is either a `run`/`benchmark` output directory (`raw_data.zip`, series
decoded from each run record's `active_series`) or a `compare` output directory
(`active_series.csv` + `comparison.csv`, mscmap and PureMagic side by side).
Per circuit one figure stacks the layers (data, aux, cultivating, holding, consume)
as a fraction of N for each model, using the completed sample with the median
`execution_lcycles` unless `--sample` picks one; `overview_fraction.png` overlays the
total fraction of every model per circuit. For a `compare` output three resource
figures are added from `comparison.csv`/`comparison_median.csv`: physical-qubit
footprint (grouped bars), active volume (dot-and-range on a log axis), and spacetime
volume (area-time plane with iso-volume guides beside the volume relative to PureMagic
split into active and idle cell-lcycles), and one column chart each for area, spacetime
volume relative to PureMagic, and active utilization. Requires matplotlib (`pip install
'mscmap[plot]'`). Figures only visualize recorded values; nothing is recomputed.
"""

import argparse
import csv
import math
import statistics
from pathlib import Path

from algorithm.metrics import ACTIVE_LAYERS, decode_series
from execution.archive import read_runs

LAYER_COLORS = dict(zip(ACTIVE_LAYERS, ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4")))
MODEL_ORDER = (
    "PureMagic",
    "mscmap/JointScheduler",
    "mscmap/JointScheduler@brick",
    "JointScheduler",
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
    """(runs, series) from a `compare` output directory."""
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
    """(runs, series) from a `run` or `benchmark` output directory."""
    runs, series = {}, {}
    for record in read_runs(folder / "raw_data.zip"):
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
    handles: dict[str, object] = {}
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
    parser.add_argument("input", type=Path, help="a run/benchmark or compare output")
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
    if (args.input / "comparison_median.csv").is_file():
        written = resource_figures(plt, args.input, args.cases, args.output)
        print(f"wrote {', '.join(written)} to {args.output}")


# Architecture resource figures (compare outputs) --------------------------------

RESOURCE_MODELS = ("PureMagic", "mscmap/JointScheduler", "mscmap/JointScheduler@brick")
SHORT_MODEL = {
    "PureMagic": "PureMagic",
    "mscmap/JointScheduler": "mscmap (matched grid)",
    "mscmap/JointScheduler@brick": "mscmap (own brick)",
}


def load_resources(folder: Path, cases: list[str] | None) -> tuple[list[dict], dict]:
    """(median rows, completed per-run rows by (case, model)) of a compare output."""
    with (folder / "comparison_median.csv").open() as f:
        medians = [r for r in csv.DictReader(f) if not cases or r["case_id"] in cases]
    runs: dict[tuple[str, str], list[dict]] = {}
    with (folder / "comparison.csv").open() as f:
        for r in csv.DictReader(f):
            if r["status"] == "completed" and (not cases or r["case_id"] in cases):
                runs.setdefault((r["case_id"], r["model"]), []).append(r)
    medians.sort(key=lambda r: (r["case_id"], model_rank(r["model"])))
    return medians, runs


def compact(value: float) -> str:
    if value >= 1e6:
        return f"{value / 1e6:.3g}M"
    if value >= 1e3:
        return f"{value / 1e3:.3g}K"
    return f"{value:.3g}"


def _legend(fig, colors: dict, loc=(0.01, 0.955)):
    from matplotlib.lines import Line2D

    handles = [
        Line2D([], [], marker="o", color=c, linestyle="", markersize=7, label=SHORT_MODEL.get(m, m))
        for m, c in colors.items()
    ]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=loc, ncol=len(handles),
               frameon=False, fontsize=9)


def physical_qubits_figure(plt, medians: list[dict], colors: dict, out: Path):
    """Grouped horizontal bars: physical-qubit footprint per circuit and model (a
    constant per layout, so no spread). Bars are thin with a surface gap."""
    cases = sorted({r["case_id"] for r in medians})
    models = [m for m in colors if any(r["model"] == m for r in medians)]
    fig, ax = plt.subplots(figsize=(9, 0.55 * len(cases) * len(models) / 2 + 1.6))
    width = 0.8 / len(models)
    for j, model in enumerate(models):
        for i, case in enumerate(cases):
            row = next((r for r in medians if r["case_id"] == case and r["model"] == model), None)
            if row is None or row["physical_qubits"] == "":
                continue
            value = float(row["physical_qubits"])
            y = i + (j - (len(models) - 1) / 2) * width
            ax.barh(y, value, height=width * 0.9, color=colors[model], edgecolor=SURFACE,
                    linewidth=1.5)
            ax.text(value, y, f" {compact(value)}  (N={row['n_total']})", va="center",
                    ha="left", fontsize=8, color=INK_SOFT)
    ax.set_yticks(range(len(cases)))
    ax.set_yticklabels(cases)
    ax.invert_yaxis()
    ax.set_xlim(0, max(float(r["physical_qubits"] or 0) for r in medians) * 1.35)
    ax.set_xlabel("physical qubits  (N tiles x (2d^2 - 1), d = code distance; first-order estimate)")
    ax.grid(axis="y", visible=False)
    _legend(fig, {m: colors[m] for m in models})
    fig.suptitle("Physical-qubit footprint per circuit", x=0.01, ha="left", fontsize=11,
                 color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(out, dpi=140)
    plt.close(fig)


def active_volume_figure(plt, medians: list[dict], runs: dict, colors: dict, out: Path):
    """Dot-and-range plot on a log axis: median active volume (cell-lcycles) per circuit
    and model, with the min-max range over completed samples. A log axis suits values
    that span orders of magnitude across circuits, which rules out bars."""
    cases = sorted({r["case_id"] for r in medians})
    models = [m for m in colors if any(r["model"] == m for r in medians)]
    fig, ax = plt.subplots(figsize=(9, 0.5 * len(cases) + 1.8))
    offset = 0.22
    for j, model in enumerate(models):
        for i, case in enumerate(cases):
            values = [float(r["active_volume"]) for r in runs.get((case, model), [])]
            if not values:
                continue
            y = i + (j - (len(models) - 1) / 2) * offset
            median = statistics.median(values)
            ax.plot([min(values), max(values)], [y, y], color=colors[model], linewidth=2,
                    solid_capstyle="round", alpha=0.6)
            ax.plot([median], [y], marker="o", markersize=8, color=colors[model],
                    markeredgecolor=SURFACE, markeredgewidth=1.5, linestyle="")
    ax.set_xscale("log")
    ax.set_yticks(range(len(cases)))
    ax.set_yticklabels(cases)
    ax.invert_yaxis()
    ax.set_xlabel("active volume, cell-lcycles (dot: median; bar: min-max over samples; log scale)")
    ax.grid(axis="y", visible=False)
    _legend(fig, {m: colors[m] for m in models})
    fig.suptitle("Active volume per circuit", x=0.01, ha="left", fontsize=11, color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(out, dpi=140)
    plt.close(fig)


def spacetime_figure(plt, medians: list[dict], colors: dict, out: Path):
    """Two views of spacetime volume N x L. Left: area N against median L on log-log
    axes with iso-volume guides (N x L constant), the three models of one circuit
    joined by a gray line, so a point's volume, its area-time trade-off, and the
    direction each architecture moves are visible at once. Right: volume relative to
    PureMagic per circuit, split into active and idle cell-lcycles (active utilization
    is the active share of the bar)."""
    cases = sorted({r["case_id"] for r in medians})
    models = [m for m in colors if any(r["model"] == m for r in medians)]
    by_case: dict[str, dict[str, dict]] = {}
    for r in medians:
        if r["execution_lcycles_median"] != "":
            by_case.setdefault(r["case_id"], {})[r["model"]] = r
    fig, (left, right) = plt.subplots(
        1, 2, figsize=(13, 0.45 * len(cases) + 3.4), gridspec_kw={"width_ratios": (5, 6)}
    )
    # Left: N vs L with iso-volume guides.
    ns = [float(r["n_total"]) for rows in by_case.values() for r in rows.values()]
    ls = [float(r["execution_lcycles_median"]) for rows in by_case.values() for r in rows.values()]
    n_lo, n_hi = min(ns) / 1.4, max(ns) * 1.4
    l_lo, l_hi = max(min(ls), 1) / 1.6, max(ls) * 1.6
    for exponent in range(int(math.floor(math.log10(n_lo * l_lo))), int(math.ceil(math.log10(n_hi * l_hi))) + 1):
        volume = 10 ** exponent
        xs = [n_lo, n_hi]
        left.plot(xs, [volume / x for x in xs], color=GRID, linewidth=0.8, zorder=1)
        y_at = volume / n_hi
        if l_lo <= y_at <= l_hi:
            left.text(n_hi, y_at, f" {compact(volume)}", fontsize=7, color=INK_SOFT, va="center")
    labels_at: list[tuple[float, float, str]] = []
    for case, rows in by_case.items():
        ordered = [rows[m] for m in models if m in rows]
        left.plot([float(r["n_total"]) for r in ordered],
                  [float(r["execution_lcycles_median"]) for r in ordered],
                  color="#b8b7b2", linewidth=1, zorder=2)
        for r in ordered:
            left.plot([float(r["n_total"])], [float(r["execution_lcycles_median"])], marker="o",
                      markersize=8, color=colors[r["model"]], markeredgecolor=SURFACE,
                      markeredgewidth=1.5, linestyle="", zorder=3)
        anchor = rows.get("PureMagic") or ordered[0]
        labels_at.append((float(anchor["n_total"]), float(anchor["execution_lcycles_median"]),
                          case.split("/")[-1]))
    # Direct labels at the PureMagic point; labels that would overlap are stepped up.
    placed: list[float] = []
    for x, y, text in sorted(labels_at, key=lambda item: (item[1], item[0])):
        while any(abs(math.log10(y) - math.log10(seen)) < 0.08 for seen in placed):
            y *= 1.25
        placed.append(y)
        left.text(x * 1.06, y, text, fontsize=7, color=INK_SOFT, va="center")
    left.set_xscale("log")
    left.set_yscale("log")
    left.set_xlim(n_lo, n_hi * 1.5)
    left.set_ylim(l_lo, l_hi)
    left.set_xlabel("area N (tiles, log)")
    left.set_ylabel("execution_lcycles L (median, log)")
    left.set_title("Area-time plane; gray guides: N x L constant", loc="left", fontsize=9,
                   color=INK)
    # Right: relative volume split into active / idle.
    labels, y = [], 0
    ticks = []
    for case in cases:
        rows = by_case.get(case, {})
        reference = rows.get("PureMagic")
        base = float(reference["spacetime_volume_median"]) if reference else None
        for model in models:
            r = rows.get(model)
            if r is None or not base:
                continue
            volume = float(r["spacetime_volume_median"])
            active = float(r["active_volume_median"] or 0)
            scale = volume / base
            active_part = scale * active / volume if volume else 0
            right.barh(y, active_part, height=0.7, color=colors[model], edgecolor=SURFACE,
                       linewidth=1.5)
            right.barh(y, scale - active_part, left=active_part, height=0.7,
                       color=colors[model], alpha=0.28, edgecolor=SURFACE, linewidth=1.5)
            right.text(scale, y, f" {scale:.2f}x  (active {active / volume:.0%})", va="center",
                       fontsize=7.5, color=INK_SOFT)
            ticks.append(y)
            labels.append(f"{case.split('/')[-1]}  {SHORT_MODEL.get(model, model)}")
            y += 1
        y += 0.6
    right.axvline(1.0, color=INK_SOFT, linewidth=1)
    right.set_yticks(ticks)
    right.set_yticklabels(labels, fontsize=7.5)
    right.invert_yaxis()
    max_scale = max(
        float(r["spacetime_volume_median"]) / float(rows["PureMagic"]["spacetime_volume_median"])
        for rows in by_case.values()
        if "PureMagic" in rows and float(rows["PureMagic"]["spacetime_volume_median"])
        for r in rows.values()
    )
    right.set_xlim(0, max(1.0, max_scale) * 1.6)
    right.set_xlabel("N x L relative to PureMagic (solid: active cell-lcycles, faint: idle)")
    right.grid(axis="y", visible=False)
    right.set_title("Volume vs PureMagic, split into active and idle", loc="left", fontsize=9,
                    color=INK)
    _legend(fig, {m: colors[m] for m in models})
    fig.suptitle("Spacetime volume: area-time trade-off and composition", x=0.01, ha="left",
                 fontsize=11, color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(out, dpi=140)
    plt.close(fig)


def _circuit_axis(medians: list[dict]) -> tuple[list[str], dict[str, int]]:
    """Circuits sorted by T count (descending), with their T counts."""
    t_count = {r["case_id"]: int(r["t_count"] or 0) for r in medians}
    return sorted(t_count, key=lambda c: (-t_count[c], c)), t_count


def _finish_columns(fig, ax, cases: list[str], t_count: dict, colors: dict, title: str,
                    ylabel: str, out: Path, plt, sublabel: dict | None = None):
    ax.set_xticks(range(len(cases)))
    ax.set_xticklabels(
        [f"{c.split('/')[-1]}\n(T={t_count[c]})" + (f"\n{sublabel[c]}" if sublabel and c in sublabel else "")
         for c in cases],
        fontsize=8,
    )
    ax.set_xlim(-0.6, len(cases) - 0.4)
    ax.set_ylabel(ylabel)
    ax.grid(axis="x", visible=False)
    _legend(fig, colors)
    fig.suptitle(title, x=0.01, ha="left", fontsize=11, color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(out, dpi=140)
    plt.close(fig)


def area_figure(plt, medians: list[dict], colors: dict, out: Path):
    """Grouped columns: area N per circuit and model. A constant per layout, so plain
    columns from a zero baseline with the value on each cap."""
    cases, t_count = _circuit_axis(medians)
    models = [m for m in colors if any(r["model"] == m for r in medians)]
    fig, ax = plt.subplots(figsize=(max(9.0, 1.2 * len(cases) + 2), 4.2))
    width = 0.8 / len(models)
    for j, model in enumerate(models):
        for i, case in enumerate(cases):
            row = next((r for r in medians if r["case_id"] == case and r["model"] == model), None)
            if row is None or row["n_total"] == "":
                continue
            value, x = float(row["n_total"]), i + (j - (len(models) - 1) / 2) * width
            ax.bar(x, value, width=width * 0.9, color=colors[model], edgecolor=SURFACE,
                   linewidth=1.5)
            ax.text(x, value, f"{value:.0f}", ha="center", va="bottom", fontsize=7,
                    color=INK_SOFT)
    ax.set_ylim(0, max(float(r["n_total"] or 0) for r in medians) * 1.15)
    _finish_columns(fig, ax, cases, t_count, {m: colors[m] for m in models},
                    "Area per circuit (provisioned tiles N)", "area N (tiles)", out, plt)


def volume_ratio_figure(plt, medians: list[dict], colors: dict, out: Path):
    """Columns of median spacetime volume relative to PureMagic (= 1, reference line),
    so circuits whose absolute volumes differ by orders of magnitude share one linear
    axis with a meaningful baseline; PureMagic's absolute N x L is printed under each
    circuit. Ratios use medians: samples are not paired across models."""
    cases, t_count = _circuit_axis(medians)
    models = [m for m in colors if any(r["model"] == m for r in medians)]
    by_case: dict[str, dict[str, dict]] = {}
    for r in medians:
        if r["spacetime_volume_median"] != "":
            by_case.setdefault(r["case_id"], {})[r["model"]] = r
    fig, ax = plt.subplots(figsize=(max(9.0, 1.2 * len(cases) + 2), 4.8))
    width = 0.8 / len(models)
    top, sublabel = 1.0, {}
    for i, case in enumerate(cases):
        rows = by_case.get(case, {})
        reference = rows.get("PureMagic")
        base = float(reference["spacetime_volume_median"]) if reference else 0.0
        for j, model in enumerate(models):
            r = rows.get(model)
            if r is None or not base:
                continue
            ratio, x = float(r["spacetime_volume_median"]) / base, i + (j - (len(models) - 1) / 2) * width
            top = max(top, ratio)
            ax.bar(x, ratio, width=width * 0.9, color=colors[model], edgecolor=SURFACE,
                   linewidth=1.5)
            if model != "PureMagic":
                ax.text(x, ratio, f"{ratio:.2f}", ha="center", va="bottom", fontsize=7,
                        color=INK_SOFT)
        if base:
            sublabel[case] = f"PM {compact(base)}"
    ax.axhline(1.0, color=INK_SOFT, linewidth=1)
    ax.set_ylim(0, top * 1.15)
    _finish_columns(fig, ax, cases, t_count, {m: colors[m] for m in models},
                    "Spacetime volume N x L relative to PureMagic (medians; PM absolute "
                    "cell-lcycles under each circuit)", "N x L / PureMagic N x L", out, plt,
                    sublabel)


def utilization_figure(plt, medians: list[dict], runs: dict, colors: dict, out: Path):
    """Columns of median active utilization (percent) with a min-max whisker over the
    completed samples: a bounded share from a zero baseline, with its spread."""
    cases, t_count = _circuit_axis(medians)
    models = [m for m in colors if any(r["model"] == m for r in medians)]
    fig, ax = plt.subplots(figsize=(max(9.0, 1.2 * len(cases) + 2), 4.4))
    width = 0.8 / len(models)
    top = 0.0
    for j, model in enumerate(models):
        for i, case in enumerate(cases):
            values = [100 * float(r["active_utilization"]) for r in runs.get((case, model), [])]
            if not values:
                continue
            median, x = statistics.median(values), i + (j - (len(models) - 1) / 2) * width
            top = max(top, max(values))
            ax.bar(x, median, width=width * 0.9, color=colors[model], edgecolor=SURFACE,
                   linewidth=1.5)
            ax.plot([x, x], [min(values), max(values)], color=INK_SOFT, linewidth=1,
                    solid_capstyle="round")
    ax.set_ylim(0, top * 1.12)
    _finish_columns(fig, ax, cases, t_count, {m: colors[m] for m in models},
                    "Active utilization per circuit (column: median; whisker: min-max over "
                    "samples)", "active / (N x L)  [%]", out, plt)


def resource_figures(plt, folder: Path, cases: list[str] | None, out_dir: Path) -> list[str]:
    """The three resource figures of a compare output; names of the files written."""
    medians, runs = load_resources(folder, cases)
    models = sorted({r["model"] for r in medians}, key=model_rank)
    colors = dict(zip(models, MODEL_COLORS))
    written = []
    for name, draw in (
        ("resources_physical_qubits.png", lambda p: physical_qubits_figure(plt, medians, colors, p)),
        ("resources_active_volume.png", lambda p: active_volume_figure(plt, medians, runs, colors, p)),
        ("resources_spacetime_volume.png", lambda p: spacetime_figure(plt, medians, colors, p)),
        ("resources_area.png", lambda p: area_figure(plt, medians, colors, p)),
        ("resources_spacetime_ratio.png", lambda p: volume_ratio_figure(plt, medians, colors, p)),
        ("resources_active_utilization.png",
         lambda p: utilization_figure(plt, medians, runs, colors, p)),
    ):
        draw(out_dir / name)
        written.append(name)
    return written
