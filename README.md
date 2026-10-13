# mscmap

**Demand-aware logical-patch remapping through runtime SWAPs on a fixed-orientation
tiled architecture.**

[Overview](#overview) · [Getting started](#getting-started) · [Model](#model-in-brief) ·
[Policies](#policies) · [Configuration](#configuration) · [Experiments](#experiments) ·
[Results](#results) · [Project layout](#project-layout)

## Overview

mscmap is a reproducible research framework for scheduling fault-tolerant circuits
on a tile grid with **no lattice-surgery routing paths**. Patches move step by step
through explicit SWAPs until the operands of a Clifford gate, or a data patch and a
magic-state patch, are in the local arrangement the operation needs. Future Clifford
interactions and T-state demand drive:

- initial placement of logical qubits,
- relocation of idle magic-state-cultivation (MSC) resources,
- assignment of MSCs to T requests, and
- runtime data remapping.

Experiments measure how initial placement and runtime remapping affect execution time (`execution_lcycles`),
area, and spacetime volume (including SWAP and scheduling overhead), and when a
limited reusable MSC pool improves utilization without excessive supply delay.

> [!NOTE]
> This is an abstract scheduling model (`tile-swap-v3`). Durations and areas are
> uncalibrated abstract units, the SWAP and local-contact protocols are not
> specified, and results do not establish physical feasibility or general policy
> superiority.

### Features

- **One CLI, one output layout**: `python3 src/main.py` with the commands `run`,
  `benchmark`, `compare`, and `plot`; every experiment writes `summary.csv`
  and `raw_data.zip`.
- **Local-only execution**: every two-patch operation uses direct contact or one
  shared ancilla tile, with explicit reservations and durations.
- **One proposed method**: demand-aware initial placement followed by
  `JointScheduler` with idle-only MSC relocation; the experiment commands run it
  alone and compare it against external schedulers.
- **Stochastic cultivation** with seeded, planning-invisible outcomes.
- **Synthetic workloads or real circuits**: seeded workload families, JSON gate lists,
  or OpenQASM decomposed into Clifford+T by an optional Qiskit front end.
- **No required dependencies**: pure Python 3.11+.

## Getting started

### Prerequisites

- Python 3.11 or later
- Optional: `PyYAML` for general YAML configs, `qiskit>=2.1` for OpenQASM input

### Run the reference comparison

From the repository root:

```bash
python3 src/main.py run --config configs/experiment.yaml --output results/comparison
```

This runs the proposed method (`demand_aware` mapping, `JointScheduler`, relocation
on) on one brick layout (eight data qubits, two MSCs) and writes:

```text
results/comparison/
├── summary.csv
└── raw_data.zip
```

For a hand-checkable case (two qubits, one T gate), use
[configs/tiny.yaml](configs/tiny.yaml).

> [!IMPORTANT]
> Exit code 0 means results were recorded, not that every run completed. Check the
> `status` column: stalled or limited runs stay in the summary and must be compared
> separately from completed runs.

### Install as a package (optional)

```bash
python3 -m pip install -e .            # adds the `mscmap` command (= python3 src/main.py)
python3 -m pip install -e '.[yaml]'    # general YAML parsing (bundled configs are JSON)
python3 -m pip install -e '.[qiskit]'  # OpenQASM circuit input
python3 -m pip install -e '.[plot]'    # matplotlib for `plot`
```

### CLI options

| Option | Description |
| --- | --- |
| `--config PATH` | Experiment configuration (required). |
| `--output DIR` | Output directory for `summary.csv` and `raw_data.zip` (required). |
| `--variant NAME` | Run only this named variant from the config. |
| `--case-id ID` | Case label in the shared output (default: config file stem). |
| `--repeat N` | Positive timing-repeat identifier (default 1). |
| `--samples N` | Stochastic cultivation samples `0..N-1` (deterministic supply allows one). |

```bash
# Record a second timing repeat of one variant in the same output
python3 src/main.py run --config configs/experiment.yaml --variant JointScheduler \
  --repeat 2 --output results/comparison
```

## Model in brief

- **Tiles**: `S` data slot, `C` cultivation slot, `B` bus (local ancilla/junction),
  `.` blocked. Each patch occupies one slot; all patches share one orientation
  (Z boundaries N/S, X boundaries E/W) and never rotate.
- **Local operations**: CX needs an L-shaped ancilla (control Z side, target X side);
  CZ and T consumption need Z-Z contact (consumption may be direct). No path connects
  distant patches.
- **Exchange (SWAP)**: one operation swaps a data patch with an in-line cardinal
  neighbor slot (never diagonal), or moves it there if empty, using one auxiliary bus
  tile and one duration, and update the logical-to-physical mapping. There is no
  mandatory restoration.
- **MSCs**: relocate only while idle, before initialization, to an empty `C` slot.
  Produced states never move and stay owned by one request until consumed; the data
  patch is SWAPped next to them.
- **Supply**: `supply.distribution` is `bernoulli` (default; attempts fail with
  `1 - success_probability` and retries repeat initialize + cultivate) or
  `exponential`, PureMagic's stochastic cultivation: each state takes an exponential
  sample with `rate` λ per lcycle (default 0.0387396, mean ≈ 26 lcycles), rounded to
  the nearest lcycle, with injection folded in (`initialize` takes 0 lcycles). Unlike
  PureMagic, cultivation is never interrupted. Planning never sees realized outcomes;
  it assumes `round(1/λ)` lcycles (heuristic).

## Policies

All policies share the simulator, the legality/cost model, the SWAP-based base rule
(execute in contact, otherwise start the next exchange of a fewest-SWAP plan), and
measurements. Candidates are scored by the configured `objective` (movement,
local-operation and cultivation volume, request wait, and `execution_lcycles` by default),
never by SWAP count alone. The base rule is always a candidate and wins ties.

| Policy | Decision rule |
| --- | --- |
| `JointScheduler` | The proposed method. Picks the base rule, an MSC relocation, one SWAP pre-positioning step for an upcoming gate, or waiting, by rollout cost of the remaining (or bounded) circuit. |
| `FixedSite` | The base rule alone (fixed MSC sites; data SWAPped into contact as gates become ready). Available to `run` configurations; not used by the experiment commands. |

`JointScheduler` search is bounded by `settings`: `window`, `search_limit`,
`rollout_gates` (0 = remaining circuit), `event_limit`, and `relocation`. When both
operands of a plan move, they advance together on slot-disjoint paths.
Bounded search can miss schedules; a stalled or
limited run is reported as such, not as infeasible.

## Configuration

| File | Purpose |
| --- | --- |
| [configs/experiment.yaml](configs/experiment.yaml) | Both policies on identical inputs with a seeded `migrating` workload. |
| [configs/tiny.yaml](configs/tiny.yaml) | Hand-checkable two-qubit, one-T case using [examples/tiny.json](examples/tiny.json). |

A configuration defines:

| Key | Meaning |
| --- | --- |
| `geometry.tiles` | Rows of `S`/`C`/`B`/`.` characters. |
| `placement`, `mscs` | Supplied logical-qubit and MSC tile coordinates. |
| `costs` | Durations in logical cycles (lcycles): `single` (H/X/Z), `phase_gate` (S/Sdg), `two`, `consume`, `initialize`, `cultivate`, `exchange`, `relocate`. Gate defaults follow PureMagic's lcycle accounting (CX 2, S 3, T 1); the rest are modeling assumptions. |
| `circuit_model` | `dependencies`: `commutation` (default; gates that commute on every shared qubit keep no edge) or `program_order`; `pauli_frame` (default true): X/Z tracked classically instead of executed (T/TDG swapped where needed; the record is stored in the resolved config). |
| `timing` | `code_distance` (default 17) and `physical_cycle_us` (default 1 µs): metadata converting lcycles to microseconds. A logical cycle takes at least `code_distance` physical cycles; the model uses that minimum (17 µs by default). Durations are not rescaled. |
| `supply` | `distribution`: `bernoulli` (default) with `success_probability` per attempt, or `exponential` with `rate` λ per lcycle (PureMagic; `costs.initialize`/`cultivate` unused). |
| `objective` | Weights for `movement_volume`, `operation_volume`, `cultivation_volume`, `request_wait`, `execution_lcycles`. |
| `mapping` | `supplied` or `demand_aware` initial placement. |
| `circuit` **or** `workload` | Exactly one: a gate list (inline, `.json`, or `.qasm`) or a seeded synthetic workload. |
| `policy`, `settings` | Default policy and its search bounds. |
| `variants` | Optional per-variant overrides of `name`, `policy`, `settings`, and `mapping` only (for `run`; the experiment commands run the proposed method alone). |

Gate lists use H, S, Sdg, X, Z, T, Tdg, CX, and CZ. Synthetic workload families are
`localized`, `migrating`, `alternating`, `distributed`, and `random`; T targets are
fixed relative to the supplied placement.

### OpenQASM input

A `circuit` file ending in `.qasm` (OpenQASM 2; OpenQASM 3 also needs
`qiskit_qasm3_import`) is parsed and decomposed into the Clifford+T gate set by
Qiskit, e.g. [examples/adder4.qasm](examples/adder4.qasm).

- Logical qubits are named `q0..qN-1` by circuit index, so `placement` must use
  those names.
- Rotations are approximated by Qiskit's synthesis, which sets the T count; T and Tdg
  each consume one magic state.
- Final measurements are dropped and barriers ignored; mid-circuit measurement,
  reset, and classical control are rejected.

> [!NOTE]
> Qiskit only prepares the circuit. Dependencies, placement, and scheduling are
> mscmap's own, and the resolved config stores the converted gates.

Unknown configuration fields, section keys, and policy names are rejected with the
list of accepted values; there is no migration layer for configurations of former
model versions.

## Experiments

The experiment drivers are commands of the same entry point and use the same
engine and output format: `benchmark`
([src/analysis/benchmark.py](src/analysis/benchmark.py)), `compare`
([src/analysis/compare.py](src/analysis/compare.py)), and `plot`
([src/analysis/plot.py](src/analysis/plot.py)). Both experiment commands run the
proposed method only (`demand_aware` mapping, `JointScheduler`, relocation on;
`proposed_config` in [src/execution/engine.py](src/execution/engine.py)); there are
no ablation variants. `python3 src/main.py <command> --help` lists the options.

### QASM benchmark

```bash
python3 src/main.py benchmark --output results/qasm --size small --size medium --max-gates 2000
```

`benchmark` (needs `.[qiskit]`) runs
the circuits in `data/<small|medium|large>/*.qasm`. It converts each circuit once,
sizes a brick layout to its qubit count, places data and MSC sites with the
demand-aware mapping from the row-major supplied order, and runs `JointScheduler`,
taking costs and settings from `configs/experiment.yaml`. Case IDs are
`<size>/<file stem>`.

| Option | Default | Description |
| --- | --- | --- |
| `--size`, `--circuit` | all | Select size classes or file stems (repeatable). |
| `--slot-rows` | `ceil(sqrt(qubits))` | Data rows of the brick layout. |
| `--msc-count` | 2 | MSC pool size. |
| `--rollout-gates` | 32 | JointScheduler rollout bound (0 = remaining circuit). |
| `--event-limit` | 50 × gate count | Simulator event bound. |
| `--max-gates` | none | Skip larger converted circuits; skipped ones are listed in the archive manifest. |
| `--repeats`, `--samples` | 1 | Timing repeats and stochastic samples. |

### PureMagic comparator

```bash
python3 src/main.py compare --output results/pm --puremagic ../PureMagic \
  --qasm examples/adder4.qasm --size medium --circuit simon_n6 --samples 5
```

`compare` (needs `.[qiskit]`) compares mscmap with
the external [PureMagic](https://github.com/BQSKit/PureMagic) scheduler under matched
constraints. Build it first (`cargo build --release --bin transpile --bin puremagic`;
needs Rust, a C toolchain, `m4`, and `git submodule update --init`). Both models get
the same resolved Clifford+T gate list, the same grid and area N (PureMagic's default
bus-routing topology with `--msc-count` magic nodes; mscmap places C slots on the
magic nodes, S slots holding the same qubits on the data nodes, and B/empty S tiles in
the brick pattern elsewhere; mscmap's demand-aware mapping may re-place data among
the S slots), the same number of magic-state resources (no spare C slot, so MSC
sites stay fixed), the same
exponential cultivation (`--lambda`, default 0.0387396 per lcycle; `--samples N` runs
mscmap samples 0..N-1 and PureMagic seeds 1..N), explicit Cliffords at the same lcycle
costs (PureMagic transpile weight limit `--width`, default 1), and no T-injection
failures (`-F`). PureMagic still absorbs every Clifford after the last T into
final measurement products (a T-free circuit runs no Clifford) and routes multi-patch
Pauli products over bus paths, while mscmap executes every gate locally after SWAPs; results compare those models and
are not a reproduction of the PureMagic paper.

Both volumes are N × L: N = provisioned tiles (equal to PureMagic's node count here)
and L = `execution_lcycles`, the lcycles from time 0 until the last operation finishes
(PureMagic: scheduled logical cycles). The output holds `mscmap/` (`summary.csv`,
`raw_data.zip`), `puremagic/<case>/` (QASM, `.trans`, topology, one log per seed),
`comparison.csv` (one row per model and sample, with `active_volume`,
`mean_active_cells`, `peak_active_cells`, `active_utilization`), `comparison_median.csv`,
`active_series.csv` (one row per model, sample, and lcycle: active cells by layer, their
total, and `fraction_of_n`), and `manifest.json` (PureMagic commit, flags, and whether
the stats hook was present).

The active-cell layers are the same in both models (data, aux, cultivating, holding,
consume; see Results). PureMagic's data layer is counted from its `circuit.schedule`;
its bus and magic layers need per-lcycle counters that the binary prints only as
averages, so apply [examples/puremagic_stats_csv.patch](examples/puremagic_stats_csv.patch)
(an output hook, no scheduling change) with `git -C ../PureMagic apply ...` and rebuild;
the hook writes a CSV when `PUREMAGIC_STATS_CSV` is set. Without it, PureMagic rows carry
the data layer only and `manifest.json` records `puremagic_stats_hook: false`.

> [!TIP]
> Circuits with arbitrary rotations can grow to 10⁴–10⁵ Clifford+T gates under
> Qiskit's synthesis. Use `--max-gates` to keep benchmark runs short.

## Results

| File | Contents |
| --- | --- |
| `summary.csv` | One row per (case, sample, variant): `status`, `objective`, `execution_lcycles` (lcycles from time 0 until the last operation finishes), `execution_us`, `exchange_count`, `exchange_depth`, `movement_volume`, `relocation_count`, `total_request_wait`, `msc_utilization`, `provisioned_area`, `spacetime_volume` (N × L: provisioned tiles × `execution_lcycles`), `spacetime_volume_us` (tile·µs), `active_volume` (cell·lcycles engaged by operations), `peak_active_tiles`, `active_utilization` (`active_volume` / N × L), `msc_pool_size`, and median `planning_seconds` over repeats. |
| `raw_data.zip` | `raw_data.jsonl` (`raw_schema_version: 8`): resolved configs (supplied and resolved placement), seeds, samples, all metrics (including policy diagnostics and `active_series`), and operation traces. |

`active_series` is the per-lcycle count of cells engaged by an operation, run-length
encoded by layer: `data` (operand slots of gates and T consumption, both slots of an
exchange), `aux` (ancilla/junction tiles, both C slots of a relocation), `cultivating`
(MSC site under initialize/cultivate), `holding` (MSC site from a successful cultivation's
end to the consumption start), and `consume` (MSC site during consumption). Idle data
patches are not active (`data_resident_volume` counts them). It is counted from the
realized trace after the run, never from planning snapshots.

```bash
python3 src/main.py plot results/comparison --output results/comparison/plots
python3 src/main.py plot results/pm --output results/pm/plots --case small/toffoli_n3
```

`plot` (needs `.[plot]`, matplotlib) draws the recorded
series: per circuit one PNG stacking the five layers as a fraction of N for each
variant (a `run` or `benchmark` output) or each model (a `compare` output,
mscmap and PureMagic side by side), plus `overview_fraction.png` overlaying the total fraction of
every model per circuit. It plots the completed sample with the median
`execution_lcycles` unless `--sample K` selects one; `--case` restricts the circuits.

Request wait is measured from DAG readiness to consumption start. Duplicate
(case, sample, repeat, variant) keys are rejected. An archive from an older raw schema
is never merged: a completed batch replaces it (with a warning), while a failed batch
leaves it untouched; an archive from a newer schema is rejected.

Current versions: model `tile-swap-v3`, schema `3`, raw schema `8`, metrics `online-v7`,
evaluation `performance-v9`.

## Project layout

[`src/`](src/) holds the entry point, the version constants, and four packages, one
per role (`python3 src/main.py` needs no `PYTHONPATH`; `PYTHONPATH=src python3 -m
main` also works). Dependencies point one way: `analysis` → `execution` → `config` →
`algorithm` → `versions`.

```text
src/
├── main.py            CLI: registers the commands run, benchmark, compare, plot
├── versions.py        package, schema, model, raw schema, metrics, evaluation versions
├── algorithm/         the scheduling algorithm (no file or output formats)
│   ├── circuit.py     circuit input, dependency DAG, Pauli frame
│   ├── demand.py      dependency-only demand estimates and interaction counts
│   ├── model.py       geometry, local contacts, costs, supply, objective, state, contracts
│   ├── swaps.py       SWAP planning: free contacts, exchange trees, static SWAP distances
│   ├── mapping.py     supplied or demand-aware initial placement
│   ├── simulator.py   event execution, legality, reservations, lifecycle, traces
│   ├── policies.py    shared base rule, candidates, rollouts, FixedSite, JointScheduler
│   └── metrics.py     online RunMetrics and active_series
├── config/            option values and inputs
│   ├── loader.py      configuration loading, validation, resolution, recorded assumptions
│   ├── workload.py    seeded synthetic workload families
│   ├── circuits.py    optional OpenQASM/Qiskit import, data-set discovery, circuit stats
│   ├── layouts.py     generated brick tile layouts
│   └── paths.py       configs/experiment.yaml and data/ locations
├── execution/         running experiments
│   ├── engine.py      variants, proposed method, samples, timing repeats, environment record
│   ├── archive.py     summary.csv and raw_data.zip writer and reader
│   └── run.py         `run` command
└── analysis/          comparison and analysis commands
    ├── benchmark.py   `benchmark`: OpenQASM circuits on brick layouts
    ├── compare.py     `compare`: matched runs against the external PureMagic binaries
    └── plot.py        `plot`: PNG figures of the recorded active_series
```

Other resources:

- [Description.md](Description.md): current research brief and constraints.
- [AGENTS.md](AGENTS.md): full modeling invariants and repository conventions.
- `data/`: OpenQASM benchmark circuits by size class.
- `examples/`: input data only ([tiny.json](examples/tiny.json), [adder4.qasm](examples/adder4.qasm), the PureMagic stats patch).
