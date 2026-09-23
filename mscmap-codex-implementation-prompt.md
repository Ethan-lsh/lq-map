# mscmap implementation brief

Maintain the existing Python experiment framework; do not rebuild completed features.
Follow [AGENTS.md](AGENTS.md). Current behavior is defined by `src/mscmap/` and the
supplied configurations. Python 3.11+ is required; general YAML needs optional PyYAML.

## 1. Research objective

Jointly choose the next magic-state cultivation (MSC) site, request assignment,
and logical-qubit movement to **reduce total logical SWAPs**. Compare completion
cycles, request waits, and planning time alongside SWAP count.

There is no circuit completion deadline; the old `budget` input is rejected.
Durations are illustrative positive integer cycles, not calibrated physical cycles.
Report observed performance, not optimality, proof status, or general superiority.
Development stages do not divide experiments or output folders.

## 2. Execution model

- Exactly N data patches + M reusable MSC patches; usable/blocked cells are explicit.
  No hidden routing buffers, ancillas, or extra production capacity.
- Movement uses cardinal adjacency. Data/data and permitted idle-MSC/data exchanges
  each count as one SWAP. Gate operands retain logical IDs as placement changes.
- Relocation prepares the **next** cultivation site. Never move, cancel, or restart
  active cultivation. Idle-MSC exchanges require an assigned preparation transaction.
- Lifecycle: `idle → preparing → initializing → initialized → producing → output
  → consuming → idle`. Outputs remain owned and occupy their patch until consumed.
- Actual DAG readiness, adjacency, ownership, and resource reservations govern every
  operation. Ordinary two-qubit gates also require routing and consume time/resources.
- Reservations use `[start, end)`; same-time completions precede new starts.
  Completion requires all gates finished, no active work, and all MSCs idle.

| Relocation mode | Constraint |
| --- | --- |
| `serialized` | One preparation transaction at a time; release mutex after initialization |
| `disjoint_paths` | Reserve the complete preparation path for the transaction |
| `space_time` | Reserve resources per operation interval |

Independent production may overlap. All policies share the same simulator,
resource rules, dispatcher, and online measurements. Rollouts use isolated snapshots.

## 3. Three public policies

| Policy | Decision rule |
| --- | --- |
| `FixedSite` | Retain production sites; route data as needed |
| `NextRequestGreedy` | First demand and first idle MSC; rank site candidates by immediate completion time, then SWAPs |
| `JointScheduler` | Preserve a full base schedule and improve it with bounded future-demand search |

`JointScheduler` is implemented by the existing `LookaheadJoint` class:

1. Its private `_ImmediateSwapPlanner` generates a full base schedule using immediate
   request SWAP cost. It also completes each candidate's remaining circuit.
2. Search request order, idle MSC assignment, and site paths, including site retention.
3. Accept only completed schedules. Replace the incumbent only for a smaller
   `(total SWAPs, makespan)` pair; keep it on ties.
4. Commit one assignment or preserve a wait, then replan. Keep the full continuation;
   divergence from its saved execution prefix is an error.

The internal planner is not a separate experimental policy. Old public names
`ShortestPathRelocation` and `LookaheadJoint` are rejected; use `JointScheduler`.
A completed base schedule can be preserved without increasing SWAPs, but execution
limits can still interrupt a run. Failed bounded search does not prove infeasibility.

Keep window/depth/beam/candidate/path/routing/search/event limits configurable.
The common router commits one data-routing SWAP at a time and does not pre-route
unready gates or unsupplied T requests. These restrictions limit the heuristic search.

## 4. Experiments and execution

- `configs/experiment.yaml`: the three policies under identical inputs; currently
  two MSCs, four data qubits, four T requests, and serialized relocation.
- `examples/workload_sweep.py`: 18 predefined conditions varying demand distribution,
  DAG dependencies, CX frequency, initial placement, and circuit size. Defaults to
  three timing repeats: 162 executions, summarized into 54 case/policy rows.
- Workload options: `family`, `requests`, `dependency`, `cx_every`. Placement cases
  keep the circuit fixed and change only initial data locations. No outcome filtering.
- Variants may override only name, policy, mode, settings, and backend. Within each
  case, circuit, placement, MSC count, geometry, and operation costs stay identical.
- `configs/tiny.yaml`: optional oracle comparison, selected with `backend: oracle`.
  Scope: one MSC, at most three usable cells/four gates, `oracle_horizon` 1..14.
  Its search horizon is not a policy deadline; report limits without optimality labels.

Use one shared output folder:

```bash
export PYTHONPATH="$PWD/src"
python3 -m mscmap run --config configs/experiment.yaml --output results/comparison
python3 examples/workload_sweep.py --output results/comparison --case migrating --case cx_dense --repeats 3
```

The single `run` CLI also accepts `--variant`, `--case-id`, and `--repeat`.
CLI case IDs default to the config filename stem. Omit `--case` in the helper to
run all 18 conditions. Repeated keys are rejected; select unrecorded cases or use a
new repeat number for another standalone measurement.

## 5. One output folder, two files

| File | Contents |
| --- | --- |
| `summary.csv` | One row per `(case_id, policy)`; median planning time across repeats |
| `raw_data.zip` | One `raw_data.jsonl` stream containing inputs, individual metrics, and traces |

Summary columns: `case_id`, `name`, `status`, `swap_count`, `swap_depth`, `makespan`,
`total_request_wait`, `relocation_count`, `planning_seconds`.

SWAP depth counts SWAP-only patch dependency layers. Makespan and waits use abstract
cycles; planning time uses host seconds. Waiting starts after predecessor completion.
Compare SWAPs among completed runs; retain partial outcomes and their status.

Raw records have `type` = `manifest`, `case`, or `run`. Run records identify case,
repeat, and policy and embed resolved configuration, metadata, diagnostics, and trace.
Version tags: `abstract-patch-v1`, `online-v1`, `performance-v2`, raw schema `1`,
and integrated planner `baseline-improvement-v1`.

Reuse the same folder to append runs. Reject duplicate `(case_id, repeat, policy)`
keys and inconsistent repeated inputs/non-timing summary values. Publish the merged
archive and summary on successful writer closure; exceptions discard pending additions.
No case/repeat/policy subfolders. Preserve historical outputs; do not migrate implicitly.
Use sequential writers; publishing the two files is not atomic under process termination.

## 6. Maintenance boundaries

| Modules | Responsibility |
| --- | --- |
| `ir`, `analysis` | Circuit DAG and demand estimates |
| `model`, `sim` | Operation contracts, lifecycle, events, and reservations |
| `routing`, `policies` | Legal candidates, shared dispatch, and planning |
| `metrics`, `experiments` | Online measurement, configuration, runs, and exports |
| `oracle` | Explicitly selected tiny-instance search |

Keep changes focused. Preserve execution preconditions and version changed model
semantics. Do not add separate demo/validate commands, independent trace replay,
a standalone test suite, or quantum SDK dependencies unless requested.
For behavior changes, run a small relevant experiment and inspect real outputs.
Keep additional checks temporary; documentation-only edits need consistency review,
not new benchmark runs. See [model assumptions](docs/model-assumptions.md),
[architecture](docs/architecture.md), and [experiment details](docs/experiments.md).
