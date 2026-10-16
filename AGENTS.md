# AGENTS.md

Repository instructions for Codex and other coding agents working on `mscmap`.

## Purpose and instruction scope

Build a reproducible research framework for **demand-aware logical-patch remapping
through runtime SWAPs on a fixed-orientation tiled architecture**. Patch locations
change step by step to establish the local arrangements needed for Clifford gates
and T-state extraction from a magic-state patch; the execution model has no
lattice-surgery routing paths. Future Clifford operations and T-state demand jointly
determine initial placement, idle MSC relocation, request assignment, and runtime
data remapping. Experiments evaluate the effects of initial placement and runtime
remapping on execution time (`execution_lcycles`), area, and spacetime volume (including SWAP and scheduling
overhead), and when a limited reusable MSC pool improves utilization without
excessive supply delays.

Apply these instructions throughout this repository. Follow higher-priority
instructions and explicit current user requirements. Read any more specific
`AGENTS.md` files before editing their directories. These instructions define
ongoing project constraints; they do not authorize unrelated work or require
reimplementing completed work on every task.

`Description.md` is the current research brief. The essential constraints are
restated below so this file is independently usable. If sources materially
disagree, identify the disagreement rather than silently changing the scientific
model.

## Current execution scope

There is one public CLI, `python3 src/main.py` (`mscmap` after `pip install -e .`),
with the commands `run` (configured variants), `benchmark`, `compare`, and `plot`,
and one output layout for single and multiple variants. Experiment drivers
are commands of that entry point, implemented as `src/` modules, never scripts under
`examples/` (which holds data files only). Do not add demo/validate commands,
duplicate example configurations, a standalone code-test suite, independent trace
replay, or an exact-optimization backend unless explicitly requested. This does
not remove input validation, operation preconditions, reservations, dependency
checks, or termination checks: those conditions define the execution model.

The implementation has no required external quantum framework. Keep the
framework-neutral circuit and execution contracts; add SDK integrations only for an
explicitly requested task. Qiskit is an optional circuit front end
(`config.circuits`, extra `mscmap[qiskit]`): it parses OpenQASM and decomposes
circuits into Clifford+T only; it never performs layout, routing, or scheduling.
Prefer focused modules and reuse existing configuration and execution paths rather
than introducing unnecessary files or directories. Configurations from retired
models or policies are rejected as unknown fields or names; there is no
migration layer.

## Working procedure

1. Inspect the repository, applicable instructions, current implementation, and relevant experiment configurations. Check existing changes and preserve unrelated work.
2. State the intended change briefly. For substantial tasks, maintain a short plan with independently verifiable milestones.
3. Implement the smallest coherent change that fully satisfies the requested scope. Continue through implementation and verification; do not stop at a proposal or placeholders.
4. Use existing repository conventions and tools. Make routine implementation choices autonomously. Ask only when unresolved ambiguity materially changes the research question, modeled feasible schedules, or requested scope.
5. For behavior changes, execute a small relevant experiment and inspect its status, metrics, and summary. Fix material failures and update affected documentation and examples. For documentation-only changes, check consistency with the actual CLI and files without rerunning unrelated experiments.
6. Report what changed, checks actually executed, outcomes, and concrete limitations. Clearly distinguish implemented, verified, and deferred work.

Default to English code, identifiers, docstrings, configuration comments, and
technical documentation. Use Korean for user-facing progress and final reports
unless requested otherwise. Keep reports concise. Code must be clean, reusable,
and easy to read.

Do not overwrite user work, weaken execution preconditions to conceal failures,
fabricate results, or present an unexecuted command as successful. Do not add
unrelated services, interfaces, dependencies, or external integrations.

## Scientific invariants

- No routing corridors, remote paths, or remote-operation fallbacks. Every two-patch
  operation uses a local contact (direct, or one shared ancilla tile); distant
  operands are brought into contact only by explicit exchange (SWAP) sequences.
- Evaluate candidates by a documented execution cost with configurable weights
  (`objective`: movement volume, local-operation volume, cultivation volume, request
  wait, `execution_lcycles`). SWAP count is one metric, never the sole objective. There is no
  circuit completion deadline.
- MSC relocation happens only while the resource is idle, before initialization or
  cultivation. Never move, cancel, or restart active cultivation. Retaining the
  current site and waiting are always candidates.
- A produced state remains owned by one MSC and one request and occupies its patch
  until consumption; the data patch is brought into contact with it. The MSC becomes
  reusable afterward. No duplication or discard.
- Keep logical-qubit IDs, tile coordinates, MSC IDs, and produced-state IDs distinct.
- Exchanges (SWAPs) change data placement, never the logical operands of circuit gates.
  Track identities through every exchange; later operations use the updated mapping.
  No mandatory restoration.
- Dependencies come from commutation (`circuit_model.dependencies`, default
  `commutation`; `program_order` keeps per-qubit input order). Pauli-frame tracking
  of X/Z (`circuit_model.pauli_frame`, default on) is a labeled modeling choice and
  only swaps T/TDG, whose costs are equal; no other gate changes.
- Schedule from actual DAG readiness and resource availability. Future-demand
  preparation may begin early, but consumption requires both a ready request and an
  available state. Estimates never replace execution preconditions.
- Adjacency alone is insufficient: every Clifford operation, T consumption,
  cultivation, and SWAP has explicit boundary contacts, auxiliary regions, occupied
  resources, and duration. Two-qubit gates are never free or instantaneous.

Do not invent scientific formulas or attribute assumptions to unread references.
Cite scientific claims only when verified in an available source. Label heuristics,
abstract timing/area constants, and modeling choices as such. FlowRouter
(space-time routing, stochastic cultivation) and RushHour (tile geometry) are
design references only. RushHour is conceptual: the tile structure and the
logical-qubit placement need not follow it. Do not equate distributed capacity with
a dedicated patch beside every T gate, confuse backup injection space with
production capacity, or label a simplified comparator as an exact reproduction of
either paper.

## Resource model

- A rectangular tile grid (`geometry.tiles`) explicitly lists `S` data slots, `C`
  cultivation slots, `B` bus (local ancilla/junction) tiles, and `.` blocked tiles.
  Patch footprints, local ancillas, and junctions are part of space and time
  accounting. Legal cultivation sites do not create MSC resources; the pool size is
  the number of configured MSCs. Never introduce hidden ancilla cells or capacity.
- All patches share one fixed orientation (Z boundaries N/S, X boundaries E/W);
  there is no rotation. CX needs an L-shaped ancilla (control Z, target X); CZ and T
  consumption need Z-Z contact. Arbitrary directions are not executable.
- `model.contact_rule`, `model.contacts`, and `OperationCosts` are the single
  legality and cost model shared by SWAP planning, policies, and the simulator. Keep
  `OPERATION_CONTRACTS` (preconditions, reserved tiles, effects) in sync with
  `Simulator.check`.
- One `exchange` operation covers SWAP and a move into an empty slot: in-line
  cardinal neighbors (up, down, left, right; never diagonal), one auxiliary bus
  tile, and one duration (`costs.exchange`); the physical protocol is unspecified
  and must stay labeled as such. MSC sites and held outputs never move.
- An MSC can operate at any `C` slot; relocation needs an empty `C` slot and
  transfers no state. State this assumption wherever it matters.
- Every operation reserves its footprint over a half-open interval. Independent
  operations may overlap; conflicting ones wait. An occupied destination cannot be
  overwritten.

Record assumptions in resolved experiment metadata. When changing resource semantics, update execution, measurement,
SWAP planning, configurations, and documentation together, and bump `MODEL_VERSION`.
Durations are integer logical cycles (lcycles); gate defaults follow PureMagic's lcycle
accounting (CX 2, S/SDG 3, T 1) and the rest are labeled assumptions. `timing`
(`code_distance`, default 17; `physical_cycle_us`, default 1) converts lcycles to
microseconds as metadata only, using the minimum of `code_distance` physical cycles
per logical cycle. Keep uncalibrated durations and areas labeled as such. A runnable abstract model
is not evidence of physical feasibility.

## Architecture boundaries

Sources live under `src/` as one entry module, one version module, and four
packages, one per role; imports are absolute from `src/` (`from algorithm.model
import ...`). `python3 src/main.py` needs no `PYTHONPATH`; `PYTHONPATH=src python3
-m main` is equivalent. Dependencies point one way: `analysis` → `execution` →
`config` → `algorithm` → `versions`; `main` only registers commands.

| Module | Role | Responsibility |
| --- | --- | --- |
| `main` | execution | The CLI; registers each command module, which exposes `add_arguments(parser)` and `execute(args, parser)` |
| `versions` | execution | Package, configuration schema, model, raw schema, metrics, and evaluation versions |
| `algorithm.circuit` | algorithm | Circuit input, stable logical operands, dependency DAG, Pauli frame |
| `algorithm.demand` | algorithm | Dependency-only demand estimates and interaction counts |
| `algorithm.model` | algorithm | Geometry, local contacts and contact rules, costs, supply model, objective, state, operation contracts |
| `algorithm.swaps` | algorithm | SWAP planning: free contacts, exchange neighbors/trees, contact-pair targets, static SWAP distances |
| `algorithm.mapping` | algorithm | Supplied or demand-aware initial placement |
| `algorithm.simulator` | algorithm | Event execution, legality, reservations, lifecycle, traces |
| `algorithm.policies` | algorithm | Shared SWAP-based base rule, candidates, rollouts, the two policies, driver |
| `algorithm.metrics` | algorithm | Online `RunMetrics` and `active_series`; no independent replay |
| `config.loader` | options | Configuration documents: loading, validation, resolution into a `Problem`, recorded assumptions |
| `config.workload` | options | Seeded synthetic workload families |
| `config.circuits` | options | Optional OpenQASM/Qiskit import, benchmark data discovery, circuit statistics |
| `config.layouts` | options | Generated brick tile layouts |
| `config.paths` | options | Repository locations (`configs/experiment.yaml`, `data/`) |
| `execution.engine` | execution | Variant resolution, `proposed_config` (the single evaluated method), policy execution, samples, timing repeats, environment record |
| `execution.archive` | execution | `summary.csv` and `raw_data.zip` writer and reader |
| `execution.run` | execution | `run`: configured variants of one file |
| `analysis.benchmark` | comparison | `benchmark`: OpenQASM circuits (Qiskit) on brick layouts |
| `analysis.compare` | comparison | `compare`: matched runs against the external PureMagic binaries |
| `analysis.plot` | comparison | `plot`: PNG figures of recorded `active_series` (optional matplotlib) |

The simulator is policy-neutral. SWAP planning proposes executable actions; it does
not bypass reservations. Analysis supplies estimates; it does not mutate execution
state. Policies implement `propose(simulator) -> Plan` and evaluate only on
snapshots. Snapshots isolate placement, reservations, lifecycle state, active
operations, and measurements, and never expose realized stochastic outcomes.
Prefer typed dataclasses, enums, small focused functions, and minimal
dependencies. Avoid mutable global simulation state and hidden random generators.
Validate inputs with actionable errors. Keep traces and configurations
schema-versioned.

## Simulation and scheduling

Use deterministic discrete-event execution: at each timestamp, completions apply in
operation-ID order before any start; a successor may start when its predecessor
finishes. Use stable IDs and deterministic tie breakers. Detect stalls without
infinite loops and expose diagnostic reasons. Commit legal actions and replan at
each decision point, preserving existing reservations and active cultivation.

Stochastic cultivation: declare the supply model and retry cost
(`supply.distribution`: `bernoulli` attempts, or PureMagic's `exponential` production
time with `rate` λ per lcycle, rounded, never interrupted). Realized outcomes, including
sampled durations, depend only on (seed, sample, MSC, generation, attempt); planning
must not access them (snapshots replace sampled cultivation ends by the estimate). Keep stochastic samples separate from timing repetitions. Deterministic
reference runs (`success_probability: 1`) remain supported.

## Policies

| Policy | Role |
| --- | --- |
| `JointScheduler` | The proposed method: chooses among the base rule, MSC relocation, one SWAP pre-positioning step, or waiting, using future demand |
| `FixedSite` | The base rule alone with fixed MSC sites; a `run` option, not part of any experiment command |

The evaluated configuration is the proposed method only: `demand_aware` mapping,
`JointScheduler`, `relocation: true` (`execution.engine.proposed_config`). Do not add
ablation variants (fixed-pool, supplied-mapping, or base-rule-only runs) to the
experiment commands; the project compares the proposed method against external
schedulers, not its own components against each other.

All policies use SWAP-based remapping, the same local execution rules, and the same
measurements; they differ only in planning. The shared base rule executes a ready
gate in contact, otherwise starts the next exchange of its fewest-SWAP plan (either
or both operands may move; both advance together, and the two operand paths must be
slot-disjoint), protecting operands of earlier ready gates, which
never move for later gates. The base
rule is always a candidate and wins exact ties. Use bounded lookahead with
configurable limits (`window`, `search_limit`, `rollout_gates`, `event_limit`,
`relocation`). Bounded search can miss schedules;
report stalled or limited runs without claiming infeasibility or introducing a
remote-operation fallback. Only these two policy names exist; do not reintroduce
retired policies (`LookaheadJoint`, `ShortestPathRelocation`, `NextRequestGreedy`).

## Experiments and reporting

The experiments compare the proposed method with external schedulers (currently
PureMagic through `compare`) on OpenQASM circuits under matched constraints: the same
Clifford+T gate list, grid and area N, number of magic-state resources, supply model,
and lcycle accounting. Report execution time (`execution_lcycles`), area, and
spacetime volume side by side, and state the remaining model differences instead of
normalizing them away. `compare --layouts brick` adds the architecture view: mscmap on
its own compact brick layout beside the equal-area run, with the resource metrics of
`comparison.csv` (physical qubits, execution time in µs, physical spacetime volume,
tiles per logical qubit, T throughput, depth overhead over each model's own
dependency bound, magic utilization and holding fraction, aux share, mean T wait)
computed identically for both models and labeled as first-order estimates. `benchmark` records the proposed method alone on brick
layouts; there is no internal ablation (no policy-quality, initial-mapping, mobility,
or resource-pooling comparison between variants).

Define workload targets relative to the supplied placement; never regenerate the
circuit after movement. Variants in `run` configurations may override only `name`,
`policy`, `settings`, and `mapping`; resolve the workload once per run. Measure request waiting from DAG readiness to consumption
start. Retain failed and partial runs, but compare completed-run
performance separately. Never fabricate numbers, exclude inconvenient outcomes, or
mix model versions. Exclude wall-clock measurements from determinism claims. Small
runs establish observed software behavior, not general superiority.

## Outputs

Every run writes only `summary.csv` and `raw_data.zip` to its `--output` directory.
The archive stores resolved configurations (supplied and resolved placements),
seeds, samples, per-run metrics, and operation traces as typed JSONL records
(`raw_schema_version: 8`). The summary has one row per (case, sample, variant) with
median planning time over repeats. `spacetime_volume` is N × L (provisioned S + C + B
tiles × realized `execution_lcycles`; `spacetime_volume_us` in tile·µs). `active_series`
(per-lcycle cells engaged by operations, by layer: data, aux, cultivating, holding,
consume) is counted from the realized trace; `active_volume` and `active_utilization`
(`active_volume` / N × L) summarize it; the `plot` command draws recorded series only. A (case, sample, repeat, variant) key must be
unique; duplicates are rejected. Archives from older raw schemas are never merged: a
completed batch replaces them with a warning (newer schemas are rejected). Discard the
pending batch on an exception, leaving the existing archive untouched. Do not run concurrent
writers. Keep large generated results out of source control.

Current versions: model `tile-swap-v3`, schema `3`, raw schema `8`, metrics `online-v7`,
evaluation `performance-v9`.

Representative invocations from the repository root:

```bash
python3 src/main.py run --config configs/experiment.yaml --output results/comparison
python3 src/main.py run --config configs/experiment.yaml --variant JointScheduler --repeat 2 --output results/comparison
python3 src/main.py compare --output results/pm --puremagic ../PureMagic --circuit toffoli_n3
python3 src/main.py plot results/pm --output results/pm/plots
```

Maintain `configs/experiment.yaml` (the proposed method) and `configs/tiny.yaml`
(hand-checkable case). Do not run broad benchmarks for routine edits.

## Completion

A requested behavior change is complete when implemented, its interfaces, examples,
and docs are consistent, and a small representative experiment has actually run or
its blocker is reported. Verify geometry/boundary and ancilla constraints,
SWAP-induced mapping changes, idle-only relocation, ownership, and resource
conflicts with small executable cases when they are affected, and check that
nonlocal operand arrangements are realized only through explicit SWAPs. A working simulator
alone does not validate the research hypothesis. A documentation-only change needs
a consistency review, not new benchmark output. Report only actions actually
performed and concrete limitations.
