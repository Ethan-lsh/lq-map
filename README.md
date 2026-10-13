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

- **One CLI, one output layout**: `python3 -m mscmap run` for single or multiple
  variants, writing `summary.csv` and `raw_data.zip`.
- **Local-only execution**: every two-patch operation uses direct contact or one
  shared ancilla tile, with explicit reservations and durations.
- **Two planning policies** sharing one simulator, legality/cost model, and metrics.
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
export PYTHONPATH="$PWD/src"
python3 -m mscmap run --config configs/experiment.yaml --output results/comparison
```

This compares `FixedSite` and `JointScheduler` on one brick layout (eight data
qubits, two MSCs) and writes:

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
python3 -m pip install -e .            # replaces PYTHONPATH; adds the `mscmap` command
python3 -m pip install -e '.[yaml]'    # general YAML parsing (bundled configs are JSON)
python3 -m pip install -e '.[qiskit]'  # OpenQASM circuit input
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
python3 -m mscmap run --config configs/experiment.yaml --variant JointScheduler \
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
| `FixedSite` | Fixed MSC sites; data SWAPped into contact as gates become ready. |
| `JointScheduler` | Picks the base rule, an MSC relocation, one SWAP pre-positioning step for an upcoming gate, or waiting, by rollout cost of the remaining (or bounded) circuit. |

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
| `variants` | Per-variant overrides of `name`, `policy`, `settings`, and `mapping` only. |

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

> [!WARNING]
> Configurations for former models (`layout`, `mode`, `budget`,
> `long_route_threshold`, `costs.route_tile`, `costs.movement_reach`,
> `objective.routing_volume`) are rejected with explicit migration errors, as are
> the legacy policy names `LookaheadJoint`, `ShortestPathRelocation`, and
> `NextRequestGreedy`.

## Experiments

Two scripts in [examples/](examples/) build on the same engine and output format.

### Controlled workload sweep

```bash
python3 examples/workload_sweep.py --output results/sweep --case reference --case pool_1
```

[examples/workload_sweep.py](examples/workload_sweep.py) runs predeclared
one-factor cases against four variants: `FixedSite`, `JointScheduler`,
`JointFixedPool` (relocation off), and `JointDemandMap` (`demand_aware` mapping).
Select cases with repeatable `--case` (all cases by default); `--repeats` sets
timing repeats.

| Factor | Cases |
| --- | --- |
| Reference | `reference` |
| T-demand locality and burstiness | `t_localized`, `t_alternating`, `t_distributed`, `t_bursty` |
| Clifford interaction density | `cx_none`, `cx_dense`, `cx_local` |
| MSC pool size | `pool_1`, `pool_4` |
| Supply latency and variability | `cultivation_slow`, `supply_stochastic` |
| Relocation cost | `relocation_costly` |
| Ancillary space | `ancilla_poor`, `ancilla_scarce` |
| Resource pooling (equal area) | `pooling_distributed_4`, `pooling_movable_2` |
| Scale | `large_high_demand` |

### QASM benchmark

```bash
python3 examples/qasm_benchmark.py --output results/qasm --size small --size medium --max-gates 2000
```

[examples/qasm_benchmark.py](examples/qasm_benchmark.py) (needs `.[qiskit]`) runs
the circuits in `data/<small|medium|large>/*.qasm`. It converts each circuit once,
sizes a brick layout to its qubit count, and compares `FixedSite` with
`JointScheduler` from the same initial placement, taking costs and settings from
`configs/experiment.yaml`. Case IDs are `<size>/<file stem>`.

| Option | Default | Description |
| --- | --- | --- |
| `--size`, `--circuit` | all | Select size classes or file stems (repeatable). |
| `--variant` | both | Restrict to one policy (repeatable). |
| `--mapping` | `demand_aware` | `supplied` keeps row-major order. |
| `--slot-rows` | `ceil(sqrt(qubits))` | Data rows of the brick layout. |
| `--msc-count` | 2 | MSC pool size. |
| `--rollout-gates` | 32 | JointScheduler rollout bound (0 = remaining circuit). |
| `--event-limit` | 50 × gate count | Simulator event bound. |
| `--max-gates` | none | Skip larger converted circuits; skipped ones are listed in the archive manifest. |
| `--repeats`, `--samples` | 1 | Timing repeats and stochastic samples. |

### PureMagic comparator

```bash
python3 examples/puremagic_compare.py --output results/pm --puremagic ../PureMagic \
  --qasm examples/adder4.qasm --size medium --circuit simon_n6 --samples 5
```

[examples/puremagic_compare.py](examples/puremagic_compare.py) compares mscmap with
the external [PureMagic](https://github.com/BQSKit/PureMagic) scheduler under matched
constraints. Build it first (`cargo build --release --bin transpile --bin puremagic`;
needs Rust, a C toolchain, `m4`, and `git submodule update --init`). Both models get
the same resolved Clifford+T gate list, the same grid and area N (PureMagic's default
bus-routing topology with `--msc-count` magic nodes; mscmap places C slots on the
magic nodes, S slots holding the same qubits on the data nodes, and B/empty S tiles in
the brick pattern elsewhere), the same fixed magic-state resources, the same
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
`comparison.csv` (one row per model and sample), `comparison_median.csv`, and
`manifest.json` (PureMagic commit and flags).

> [!TIP]
> Circuits with arbitrary rotations can grow to 10⁴–10⁵ Clifford+T gates under
> Qiskit's synthesis. Use `--max-gates` to keep benchmark runs short.

## Results

| File | Contents |
| --- | --- |
| `summary.csv` | One row per (case, sample, variant): `status`, `objective`, `execution_lcycles` (lcycles from time 0 until the last operation finishes), `execution_us`, `exchange_count`, `exchange_depth`, `movement_volume`, `relocation_count`, `total_request_wait`, `msc_utilization`, `provisioned_area`, `spacetime_volume` (N × L: provisioned tiles × `execution_lcycles`), `spacetime_volume_us` (tile·µs), `msc_pool_size`, and median `planning_seconds` over repeats. |
| `raw_data.zip` | `raw_data.jsonl` (`raw_schema_version: 7`): resolved configs (supplied and resolved placement), seeds, samples, all metrics (including policy diagnostics), and operation traces. |

Request wait is measured from DAG readiness to consumption start. Duplicate
(case, sample, repeat, variant) keys are rejected. An archive from an older raw schema
is never merged: a completed batch replaces it (with a warning), while a failed batch
leaves it untouched; an archive from a newer schema is rejected.

Current versions: model `tile-swap-v3`, schema `3`, raw schema `7`, metrics `online-v6`,
evaluation `performance-v8`.

## Project layout

Modules sit directly in [`src/`](src/) (flat layout, run with `PYTHONPATH=src`):

| Module | Responsibility |
| --- | --- |
| `mscmap` | Version constants and the `run` CLI |
| `ir` | Circuit input, stable logical operands, dependency DAG |
| `qiskit_io` | Optional OpenQASM import and Clifford+T decomposition |
| `analysis` | Dependency-only demand estimates and interaction counts |
| `model` | Geometry, contacts, costs, supply, objective, state, operation contracts |
| `swaps` | SWAP planning: free contacts, exchange trees, static SWAP distances |
| `mapping` | Supplied or demand-aware initial placement |
| `sim` | Event execution, legality, reservations, lifecycle, traces |
| `policies` | Shared base rule, candidates, rollouts, the two policies |
| `metrics` | Online `RunMetrics` |
| `experiments` | Configuration, workloads, variants, samples, exports |

Other resources:

- [Description.md](Description.md): current research brief and constraints.
- [AGENTS.md](AGENTS.md): full modeling invariants and repository conventions.
- `data/`: OpenQASM benchmark circuits by size class.
