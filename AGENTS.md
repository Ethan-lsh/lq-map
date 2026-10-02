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
remapping on makespan, area, and spacetime volume (including SWAP and scheduling
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

There is one public CLI, `python3 -m mscmap run`, and one output layout for single
and multiple variants. Do not add separate demo/compare/validate commands,
duplicate example configurations, a standalone code-test suite, independent trace
replay, or an exact-optimization backend unless explicitly requested. This does
not remove input validation, operation preconditions, reservations, dependency
checks, or termination checks: those conditions define the execution model.

The implementation has no required external quantum framework. Keep the
framework-neutral circuit and execution contracts; add SDK integrations only for an
explicitly requested task. Qiskit is an optional circuit front end (`qiskit_io`,
extra `mscmap[qiskit]`): it parses OpenQASM and decomposes circuits into Clifford+T
only; it never performs layout, routing, or scheduling. Prefer focused modules and reuse existing configuration
and execution paths rather than introducing unnecessary files or directories.

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
  operands are brought into contact only by explicit SWAP/move sequences.
- Evaluate candidates by a documented execution cost with configurable weights
  (`objective`: movement volume, local-operation volume, cultivation volume, request
  wait, makespan). SWAP count is one metric, never the sole objective. There is no
  circuit completion deadline.
- MSC relocation happens only while the resource is idle, before initialization or
  cultivation. Never move, cancel, or restart active cultivation. Retaining the
  current site and waiting are always candidates.
- A produced state remains owned by one MSC and one request and occupies its patch
  until consumption; the data patch is brought into contact with it. The MSC becomes
  reusable afterward. No duplication or discard.
- Keep logical-qubit IDs, tile coordinates, MSC IDs, and produced-state IDs distinct.
- SWAP and move change data placement, never the logical operands of circuit gates.
  Track identities through every exchange; later operations use the updated mapping.
  No mandatory restoration.
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
- SWAP/move exchange in-line cardinal neighbors (up, down, left, right; never
  diagonal) and reserve one auxiliary bus tile; the physical protocol is unspecified
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

Modules live directly under `src/` (flat layout, absolute imports such as
`from model import ...`; no package directory). Run with `PYTHONPATH=src`:

| Module | Responsibility |
| --- | --- |
| `mscmap` | Version constants and the `run` CLI (`python3 -m mscmap`) |
| `ir` | Circuit input, stable logical operands, dependency DAG |
| `qiskit_io` | Optional OpenQASM/Qiskit import and Clifford+T decomposition |
| `analysis` | Dependency-only demand estimates and interaction counts |
| `model` | Geometry, local contacts and contact rules, costs, supply model, objective, state, operation contracts |
| `swaps` | SWAP planning: free contacts, exchange neighbors/trees, contact-pair targets, static SWAP distances |
| `mapping` | Supplied or demand-aware initial placement |
| `sim` | Event execution, legality, reservations, lifecycle, traces |
| `policies` | Shared SWAP-based base rule, candidates, rollouts, the two policies, driver |
| `metrics` | Online `RunMetrics`; no independent replay |
| `experiments` | Configuration, workloads, variants, samples, exports |

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

Stochastic cultivation: declare the supply model and retry cost. Realized outcomes
depend only on (seed, sample, MSC, generation, attempt); planning must not access
them. Keep stochastic samples separate from timing repetitions. Deterministic
reference runs (`success_probability: 1`) remain supported.

## Policies

| Policy | Role |
| --- | --- |
| `FixedSite` | Fixed MSC sites; data SWAPped into contact by the base rule |
| `JointScheduler` | Joint planning of MSC assignment, relocation, waiting, and SWAP pre-positioning using future demand |

All policies use SWAP-based remapping, the same local execution rules, and the same
measurements; they differ only in planning. The shared base rule executes a ready
gate in contact, otherwise starts the next exchange of its fewest-SWAP plan (either
or both operands may move), protecting operands of earlier ready gates. The base
rule is always a candidate and wins exact ties. Use bounded lookahead with
configurable limits (`window`, `site_limit`, `remap_limit`, `search_limit`,
`rollout_gates`, `event_limit`, `relocation`). Bounded search can miss schedules;
report stalled or limited runs without claiming infeasibility or introducing a
remote-operation fallback. Legacy names `LookaheadJoint`,
`ShortestPathRelocation`, and the removed `NextRequestGreedy` are rejected with a
migration message.

## Experiments and reporting

Keep these comparisons separate:

1. **Policy quality**: identical circuits, placements, geometry, MSC count, costs.
2. **Initial mapping**: supplied vs demand-aware placement under the same policy.
3. **Mobility**: fixed vs movable MSC pool at the same pool size.
4. **Resource pooling**: distributed fixed capacity vs a smaller movable pool;
   report area and capacity explicitly, using equal-area comparisons where feasible.

Vary T-demand locality and burstiness, Clifford interaction density, circuit size,
MSC pool size, cultivation latency and supply variability, relocation cost, and
ancillary space. Include large circuits, high T demand, and cases where extra
relocation worsens execution time or waiting. Define workload targets relative to the
supplied placement; never regenerate the circuit after movement.

Variants may override only `name`, `policy`, `settings`, and `mapping`; resolve the
workload once per run. Measure request waiting from DAG readiness to consumption
start. Retain failed and partial runs, but compare completed-run
performance separately. Never fabricate numbers, exclude inconvenient outcomes, or
mix model versions. Exclude wall-clock measurements from determinism claims. Small
runs establish observed software behavior, not general superiority.

## Outputs

Every run writes only `summary.csv` and `raw_data.zip` to its `--output` directory.
The archive stores resolved configurations (supplied and resolved placements),
seeds, samples, per-run metrics, and operation traces as typed JSONL records
(`raw_schema_version: 4`). The summary has one row per (case, sample, variant) with
median planning time over repeats. `spacetime_volume` is N × L (provisioned S + C + B
tiles × realized makespan in lcycles; `spacetime_volume_us` in tile·µs). A (case, sample, repeat, variant) key must be
unique; duplicates and archives from older schemas are rejected, never merged or
overwritten. Discard the pending batch on an exception. Do not run concurrent
writers. Keep large generated results out of source control.

Current versions: model `tile-swap-v1`, schema `3`, raw schema `4`, metrics `online-v4`,
evaluation `performance-v5`.

Representative invocations from the repository root:

```bash
export PYTHONPATH="$PWD/src"
python3 -m mscmap run --config configs/experiment.yaml --output results/comparison
python3 -m mscmap run --config configs/experiment.yaml --variant JointScheduler --repeat 2 --output results/comparison
python3 examples/workload_sweep.py --output results/sweep --case reference
```

Maintain `configs/experiment.yaml` (both policies) and `configs/tiny.yaml`
(hand-checkable case). Do not run broad sweeps for routine edits.

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
