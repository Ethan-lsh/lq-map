# mscmap

A reproducible research framework for **demand-aware logical-patch remapping through
runtime SWAPs** on a fixed-orientation tiled architecture. There are no
lattice-surgery routing paths: patches are moved step by step with SWAPs until the
operands of a Clifford gate, or a data patch and a magic-state patch, are in the
local arrangement the operation needs. Future Clifford interactions and T-state
demand drive initial placement, idle magic-state-cultivation (MSC) relocation,
request assignment, and runtime data remapping. The framework measures the effect of
initial placement and runtime remapping on makespan, area, and spacetime volume
(including SWAP and scheduling overhead), and when a limited reusable MSC pool
improves utilization without excessive supply delay.

[Quick start](#quick-start) · [Policies](#policies) · [Configuration](#configuration) ·
[Results](#results) · [Documentation](#documentation)

> [!NOTE]
> This is an abstract scheduling model (`tile-swap-v1`). Durations and areas are
> uncalibrated abstract units, the SWAP and local-contact protocols are not
> specified, and results do not establish physical feasibility or general policy
> superiority.

## Quick start

Use **Python 3.11+**; no third-party packages are required. From the repository root:

```bash
export PYTHONPATH="$PWD/src"
python3 -m mscmap run --config configs/experiment.yaml --output results/comparison
```

This compares `FixedSite` and `JointScheduler` on one brick
layout (eight data qubits, two MSCs) and writes:

```text
results/comparison/
├── summary.csv
└── raw_data.zip
```

`python3 -m pip install -e .` replaces `PYTHONPATH`; `.[yaml]` adds general YAML
parsing (the supplied `.yaml` files are JSON); `.[qiskit]` adds OpenQASM circuit input.

> [!IMPORTANT]
> Exit code 0 means results were recorded. Check `status`: stalled or limited runs
> stay in the summary and must be compared separately from completed runs.

## Model in brief

- Tiles: `S` data slot, `C` cultivation slot, `B` bus (local ancilla/junction), `.`
  blocked. Each patch occupies one slot; all patches share one orientation (Z
  boundaries N/S, X boundaries E/W) and never rotate.
- Every two-patch operation is local: direct contact or one shared ancilla tile.
  CX needs an L-shaped ancilla (control Z side, target X side); CZ and T consumption
  need Z-Z contact (consumption may be direct). No path connects distant patches.
- SWAP/move exchange in-line cardinal neighbors (never diagonal) using one auxiliary
  bus tile, and update the logical-to-physical mapping. MSCs relocate only while
  idle, before initialization, to an empty `C` slot. Outputs never move and stay
  owned until consumed; data is SWAPped next to them.
- Cultivation may fail (`supply.success_probability`); retries repeat
  initialize + cultivate. Planning never sees realized outcomes.

## Policies

All policies share the simulator, the legality/cost model, the SWAP-based base
rule (execute in contact, otherwise start the next exchange of a fewest-SWAP plan),
and measurements. Candidate plans are scored by the configured objective (movement,
local-operation and cultivation volume + request wait + makespan by default), never
by SWAP count alone.

| Policy | Decision rule |
| --- | --- |
| `FixedSite` | Fixed MSC sites; data SWAPped into contact as gates become ready. |
| `JointScheduler` | Plans assignments, waiting, MSC relocation and SWAP pre-positioning for upcoming gates by rollout cost of the remaining (or bounded) circuit. |

The base rule is always a candidate and wins ties.

## Configuration

| File | Purpose |
| --- | --- |
| [configs/experiment.yaml](configs/experiment.yaml) | Both policies, identical inputs. |
| [configs/tiny.yaml](configs/tiny.yaml) | Hand-checkable two-qubit, one-T case. |

A configuration defines `geometry.tiles`, supplied `placement` and `mscs`, `costs`,
`supply`, `objective`, `mapping` (`supplied` or
`demand_aware`), and exactly one of `circuit` (inline or a file such as
[examples/tiny.json](examples/tiny.json); gates H, S, Sdg, X, Z, T, Tdg, CX, CZ) or a
seeded `workload`. `variants` may change only `name`, `policy`, `settings`, and `mapping`.

A `circuit` file ending in `.qasm` (OpenQASM 2; OpenQASM 3 also needs
`qiskit_qasm3_import`) is parsed and decomposed into that Clifford+T gate set by Qiskit,
e.g. [examples/adder4.qasm](examples/adder4.qasm). Logical qubits are named `q0..qN-1`
by circuit index, so `placement` must use those names. Rotations are approximated by
Qiskit's synthesis, which sets the T count; T and Tdg each consume one magic state.
Final measurements are dropped and barriers ignored; mid-circuit measurement, reset
and classical control are rejected. Qiskit only prepares the circuit: dependencies,
placement and scheduling are mscmap's own, and the resolved config stores the
converted gates.

```bash
# One variant, recorded as a second timing repeat in the same output
python3 -m mscmap run --config configs/experiment.yaml --variant JointScheduler \
  --repeat 2 --output results/comparison
# Controlled factor sweep (locality, burstiness, CX density, pool, supply, ancilla, scale)
python3 examples/workload_sweep.py --output results/sweep --case reference --case pool_1
```

`--repeat` is a timing-repeat identifier; `--samples N` runs stochastic samples
`0..N-1` (deterministic supply allows one). The CLI case ID defaults to the config
file stem (`--case-id` overrides).

## Results

| File | Contents |
| --- | --- |
| `summary.csv` | One row per (case, sample, variant): status, objective, makespan, SWAPs, moves, SWAP depth, movement volume, relocations, request wait, MSC utilization, provisioned area, pool size, median planning time. |
| `raw_data.zip` | `raw_data.jsonl`: resolved configs (supplied and resolved placement), seeds, all metrics, rollout diagnostics, and operation traces. |

Duplicate (case, sample, repeat, variant) keys and archives from older schemas are
rejected.

## Documentation

| Resource | Contents |
| --- | --- |
| [Research brief](Description.md) | Current research direction and constraints. |

The modules sit directly in [`src/`](src/): `mscmap` (versions, CLI), `ir`/`qiskit_io`/`analysis` (DAG, QASM import, demand),
`model`/`sim` (contracts, execution), `swaps`/`mapping`/`policies` (planning),
`metrics`/`experiments` (measurement, export).

Configurations for former models (`layout`, `mode`, `long_route_threshold`,
`costs.route_tile`, `costs.movement_reach`, `objective.routing_volume`, and other
removed keys) are rejected with explicit errors.
