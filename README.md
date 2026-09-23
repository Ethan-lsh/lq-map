# mscmap

A reproducible research framework for jointly choosing **magic-state cultivation
(MSC) sites, logical-qubit SWAPs, and T-request schedules** using circuit dependency
DAGs. The objective is to minimize total logical SWAPs without a circuit completion
deadline; makespan, request waits, and planning time are reported alongside it.

[Quick start](#quick-start) · [Policies](#policies) · [Configuration](#configuration) ·
[Results](#results) · [Documentation](#documentation)

> [!NOTE]
> This is an abstract scheduling model. Operation durations are illustrative integer
> cycles, not calibrated hardware timings. Results do not establish physical
> cultivation feasibility or general policy superiority.

## Quick start

Use **Python 3.11+**. The native execution path and supplied configurations require
no third-party packages or quantum SDKs. From the repository root:

```bash
export PYTHONPATH="$PWD/src"
python3 -m mscmap run --config configs/experiment.yaml --output results/comparison
```

This compares `FixedSite`, `NextRequestGreedy`, and `JointScheduler` on the same
four-data-qubit, two-MSC layout, with four T requests and serialized relocation.
It prints result rows and writes two files:

```text
results/comparison/
├── summary.csv
└── raw_data.zip
```

For an editable installation, use `python3 -m pip install -e .` instead of setting
`PYTHONPATH`. The supplied `.yaml` files use JSON syntax, a YAML subset. To use
general YAML, install the optional parser with `python3 -m pip install -e '.[yaml]'`.

> [!IMPORTANT]
> CLI exit code 0 means results were recorded. Inspect `status` in the summary and
> `circuit_completed` in the archived metrics before comparing SWAP counts. Partial
> runs remain visible and can have low counts because they did not finish.

## Policies

Every policy uses the same discrete-event simulator, operation contracts, ordinary
gate routing, and online measurements. The default experiment varies policy only.

| Policy | Decision rule |
| --- | --- |
| `FixedSite` | Assign the first demand to the first idle MSC, retain its site, and route data as needed. |
| `NextRequestGreedy` | Use the same request/MSC selection; rank legal site paths by immediate request completion time, then added SWAPs and stable action order. |
| `JointScheduler` | Preserve a completed internal baseline schedule and explore future requests, MSC assignments, and sites through bounded simulation. |

`JointScheduler` first runs its private immediate-SWAP planner to completion. That
planner also completes candidate tails. Only completed continuations can replace
the incumbent, and only for a strictly smaller `(total SWAPs, makespan)` pair.
Exact ties preserve the schedule, including waits. Execution commits a prefix and
retains the full continuation; divergence from the saved prefix raises an error.

If the initial baseline completes and its continuation is preserved, accepted
schedules cannot increase its SWAP count. Execution limits can still interrupt a
run. If the baseline fails, bounded candidate search may still find a completion.
The internal planner is not a fourth public policy or a guarantee of optimality.

## Configuration

Start with one of the supplied configurations:

| File | Purpose |
| --- | --- |
| [configs/experiment.yaml](configs/experiment.yaml) | Three basic policies with identical resources and serialized relocation. |
| [configs/tiny.yaml](configs/tiny.yaml) | Tiny policy comparison plus an explicitly selected oracle variant. |

A configuration defines explicit `layout.usable` and `layout.blocked` coordinates,
logical-qubit `placement`, named `mscs`, operation `costs`, and exactly one of:

- `circuit`: an inline circuit object or a path relative to the configuration file;
  see [examples/tiny.json](examples/tiny.json). Supported gates are H, S, X, Z, T,
  CX, and CZ. Per-qubit ordering is preserved; explicit predecessors add DAG edges.
- `workload`: a seeded synthetic workload. Families include localized, migrating,
  alternating, distributed, and data-heavy demand. Logical targets are fixed from
  initial placement, not regenerated after movement.

The `variants` list can override only `name`, `policy`, `mode`, `settings`, and
`backend`. Circuit, placement, geometry, MSC count, and costs remain shared.
Without `variants`, the top-level policy/backend runs once.

Search settings expose demand `window`, `lookahead_depth`, `beam_width`,
`candidate_limit`, `path_limit`, `max_hops`, `route_states`, `search_limit`, and
`event_limit`. Optional `no_site_changes` and `fixed_assignment` ablations are
available but excluded from the default comparison. See
[search settings](docs/architecture.md#search-settings-and-ablations) for definitions.

### Run selections and repeats

Select one variant and append a second timing measurement to the quick-start output:

```bash
python3 -m mscmap run --config configs/experiment.yaml --variant JointScheduler --repeat 2 --output results/comparison
```

`--repeat` is a **repeat identifier**, not a repetition count. It defaults to 1.
CLI case IDs default to the configuration filename stem; use `--case-id NAME`
when recording a different input. Each `(case_id, repeat, variant name)` must be
unique. Repeated commands with the same key are rejected.

For controlled workload comparisons, the helper uses the same experiment engine:

```bash
python3 examples/workload_sweep.py --output results/comparison --case migrating --case cx_dense --repeats 3
```

Here, `--repeats` is a count. Omitting `--case` selects all 18 predefined cases:
54 case/policy rows and 162 executions at the default three repeats. Cases vary
demand distribution, dependencies, CX frequency, placement, and circuit size.
See [workload cases](docs/experiments.md#controlled-workload-comparison) before
starting a full sweep.

### Optional tiny oracle

The exhaustive oracle backend supports exactly one MSC, at most three usable
cells, at most four gates, and an integer `oracle_horizon` from 1 to 14:

```bash
python3 -m mscmap run --config configs/tiny.yaml --variant oracle --output results/comparison
```

It runs only when `backend: oracle` is selected, including through a configured
variant. Omitting `--variant oracle` in this example runs all four tiny variants.
The horizon is an oracle search restriction, not a deadline for ordinary policies.
Search-limit termination is recorded in `reason`; results carry no optimality
labels or gaps to a reference optimum.

## Results

All invocations can append to the same output directory. Single and multiple
variants use the same layout, with no case, repeat, or policy subdirectories.

| File | Contents |
| --- | --- |
| `summary.csv` | One row per `(case_id, variant name)`, with median planning time across recorded repeats. |
| `raw_data.zip` | One `raw_data.jsonl` stream containing typed records, resolved inputs, individual measurements, diagnostics, and traces. |

The summary always has these nine columns:

| Column | Meaning |
| --- | --- |
| `case_id` | Input case label. |
| `name` | Variant name, defaulting to policy name or `oracle`. |
| `status` | Execution outcome, retained for partial runs. |
| `swap_count` | Completed data/data and permitted idle-MSC/data exchanges. |
| `swap_depth` | SWAP-only physical-patch dependency layers, not elapsed cycles. |
| `makespan` | Final simulation timestamp in abstract cycles. |
| `total_request_wait` | Summed T-request waiting from predecessor completion to consumption start, in abstract cycles. |
| `relocation_count` | Committed preparations whose destination differs from their source. |
| `planning_seconds` | Median host planning time, including routing and candidate rollouts; excludes file export. |

Raw `run` records embed the concrete circuit, seed, placement, costs, search limits,
model assumptions, available code/environment metadata, metrics, and trace.
Workload batches also include `manifest` and `case` records. Version tags include
`abstract-patch-v1`, `online-v1`, `performance-v2`, and `baseline-improvement-v1`.
Measurements come directly from simulator events, without independent trace replay.

Repeated inputs and non-timing summary values must agree for each case/variant;
otherwise use a new case ID. Planning wall time is not deterministic. Detailed
completion fields, failure reasons, search counters, and overlapping wait-blocker
diagnostics stay in the archive. See [metrics and raw data](docs/experiments.md).

Use sequential writers. The output directory must be empty or contain only the
two compact output files. Handled exceptions discard pending additions and preserve
previous results. Archive and summary publication is not atomic under forced process
termination; historical output layouts are not migrated automatically.

## Model and research scope

The layout contains exactly N data cells plus M reusable MSC cells, with no hidden
buffers or ancillas. Movement is cardinally adjacent. SWAPs change placement while
logical gate operands remain fixed; ordinary two-qubit gates require adjacency,
routing, time, and resources.

Relocation prepares the **next** cultivation site. Active production is never moved,
cancelled, or restarted. Each output belongs to one request and occupies its patch
until consumed. Reservations use `[start, end)` intervals; same-time completions
precede new starts, allowing a successor to start when its predecessor finishes.

| Relocation mode | Constraint |
| --- | --- |
| `serialized` | One preparation transaction at a time, from assignment through initialization completion. |
| `disjoint_paths` | Reserve the full preparation path against incompatible operations throughout the transaction. |
| `space_time` | Reserve resources per operation interval while preserving destination ownership. |

Independent production may overlap in every mode: preparation locks are released
before cultivation. Site retention remains a candidate whenever legal.

The heuristic router commits one data-routing SWAP at a time and does not pre-route
unready gates or unsupplied T requests. Idle-MSC/data exchanges require an assigned
preparation transaction. Bounded site and beam searches do not enumerate all joint
schedules; a stalled or not-found result does not prove infeasibility. Compare
completed runs under identical inputs, and distinguish resource changes from policy
improvements. Production failures, retries, and physical error correction are outside
this model.

## Documentation

| Resource | Contents |
| --- | --- |
| [Model assumptions](docs/model-assumptions.md) | Geometry, lifecycle, operation contracts, reservations, and timing. |
| [Architecture and policies](docs/architecture.md) | Shared dispatch, policy tie breakers, search limits, and ablations. |
| [Experiments and metrics](docs/experiments.md) | Workloads, measurement definitions, oracle limits, and archive format. |
| [Implementation brief](mscmap-codex-implementation-prompt.md) | Current research and maintenance scope. |

The Python package lives in [`src/mscmap/`](src/mscmap/): `ir` and `analysis` handle
DAGs and demand estimates; `model` and `sim` enforce execution; `routing` and
`policies` plan actions; `metrics` and `experiments` measure and export runs;
`oracle` provides the optional tiny-instance search.

For older configurations, remove `budget`, replace `compare` with `variants`, and
use `backend: oracle` instead of `oracle: true`. Legacy public policy names
`LookaheadJoint` and `ShortestPathRelocation` are rejected; use `JointScheduler`.
