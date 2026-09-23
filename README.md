# mscmap

A research experiment framework for DAG-aware selection of next magic-state
cultivation sites, logical SWAPs, MSC assignments, and T-request schedules.
The objective is minimum total logical SWAP count without a circuit
completion deadline. Operation durations are illustrative abstract time units;
this is not a physical error-correction or cultivation simulator.

## Run experiments

Python 3.11 or newer is required. The native execution path has no third-party
dependencies. From the repository root:

```bash
export PYTHONPATH="$PWD/src"
python3 -m mscmap run --config configs/experiment.yaml --output results/comparison
python3 examples/workload_sweep.py --output results/comparison
```

Use the same output directory across invocations; existing raw runs are preserved
and the final summary is regenerated from all records. The supplied `.yaml` files use JSON syntax, a YAML subset. General
YAML syntax requires the optional PyYAML package.

The default `configs/experiment.yaml` compares three basic policies: FixedSite,
NextRequestGreedy, and JointScheduler. All three share the
same two-MSC resources, circuit, costs, and serialized relocation mode. Ablations
and additional relocation modes are not included in the default comparison.
Single-MSC inputs and custom variants remain supported by the same engine.

Every run writes only `summary.csv` and `raw_data.zip` at the output root.
The archive contains one `raw_data.jsonl` stream with configurations, metrics, and traces. The configuration embeds the
concrete circuit, placement, costs, limits, seed, and model/code metadata. Failed,
stalled, and search-limited outcomes remain in the summary. CLI exit 0 means results
were recorded; inspect `status` and `circuit_completed` separately.

There is one command and one output layout for all experiments. A config's
`variants` list defines which policy/mode/backend combinations to run. Omit the
list to execute just the top-level policy/backend, or select one named variant
from the same configuration:

```bash
python3 -m mscmap run --config configs/experiment.yaml --variant JointScheduler --repeat 2 --output results/comparison
```

Both single and multiple variants produce `summary.csv` and `raw_data.zip`. There are no separate demo or
compare commands/configurations. The former `compare` configuration field is now
`variants`; oracle entries use `"backend": "oracle"`. Historical result directories
are preserved. Output names are chosen with `--output`.

With pip, optional installation is available:

```bash
python3 -m pip install -e .
python3 -m pip install -e '.[yaml]'
```

## Research decisions and measurements

- FixedSite retains production sites; NextRequestGreedy favors immediate request
  completion time.
- JointScheduler considers future requests, assignments, and sites with bounded
  simulation. It ranks completed continuations by total SWAPs and
  commits the first assignment. A simulated feasible continuation is retained.
- All policies share ordinary-gate routing, resource semantics, and measurement.
  Policies are the experimental treatments, not software self-tests.
- Single MSC and fixed multi-MSC footprints support `serialized`, `disjoint_paths`,
  and `space_time` relocation. Independent production can overlap.
- Metrics include SWAP count/depth, makespan, request waits, relocations, circuit
  completion, and planning wall time. They are accumulated during execution.

The current experiment-focused version removes the standalone code-test suite,
independent trace replay, and the `validate` command at the user's request. Candidate
and final traces are not re-executed by a second checker. Input validation,
operation preconditions, resource conflicts, actual DAG readiness,
and termination limits remain: they define feasible experiment schedules.
The implementation brief and AGENTS.md document this experiment-focused scope,
including the scheduling model and optimization objective.

## Optional exact reference

`oracle` is an optional exhaustive optimization backend for one MSC, at most three usable
cells, four gates, and oracle search horizon at most 14. It provides a tiny-instance search
comparison, not routine experiment execution or a code-test suite:

```bash
python3 -m mscmap run --config configs/tiny.yaml --output results/comparison
python3 -m mscmap run --config configs/tiny.yaml --variant oracle --repeat 2 --output results/comparison
```

Ordinary policy comparisons do not invoke it. It runs only when explicitly
selected by a variant with `"backend": "oracle"`. Compare its observed SWAP count,
completion time, waits, and planning cost under identical inputs, just like other
backends. Search-limit termination is reported in `reason`; any available completed
schedule is retained. Results do not evaluate optimality or gaps
to a reference optimum.

## Interpretation and scope

Compare identical circuits, placement, patches, MSC count, and costs.
Changing MSC counts or footprints is a resource comparison, not a policy-only improvement. Workloads cover localized, migrating, alternating, and
frequent data-interaction demand, with targets fixed by initial logical placement.

The framework matches the proposed question at the abstract-model level. Its
bounded site/beam search and conservative shared data routing do not enumerate all
joint schedules. Idle MSC exchanges are limited to next-production preparation,
and ordinary data routing commits one SWAP at a time. These restrictions can affect
observed gains. Small supplied examples do not establish general research superiority
or physical cultivation feasibility. A not-found/stalled result is not a proof of
infeasibility.

See [model assumptions](docs/model-assumptions.md),
[architecture and policies](docs/architecture.md), and
[experiment metrics](docs/experiments.md).

Completion deadlines have been removed. Delete `budget` from older input configs;
new exports use `evaluation_version: performance-v2` and `circuit_completed`.
Durations and waits are abstract cycles, not calibrated physical code cycles.
The optional oracle alone uses `oracle_horizon` (default 14, range 1..14) to bound
search; this restriction does not apply to policy execution.

The performance summary has eight columns: `name`, `status`, `swap_count`,
`swap_depth`, `makespan`, `total_request_wait`, `relocation_count`, and
`planning_seconds`. Keep status so partial runs cannot be mistaken for completed
low-cost schedules. Makespan and waits use abstract cycles; planning time uses
seconds. Configuration, versions, duplicate completion fields, search counters,
failure reasons, and detailed wait diagnostics stay in the archived run records,
not the summary. Custom workload batches additionally label
rows with `case_id`. These observations do not establish general superiority.

The workload helper now runs 18 controlled cases, each with three policies and
three timing repeats. The root summary adds `case_id` and reports median planning
time. Use `--case NAME` and `--repeats N` for a smaller selection. See
[controlled workload comparison](docs/experiments.md#controlled-workload-comparison)
for factors, case names, and output layout.

JointScheduler now preserves a completed schedule from the internal immediate-SWAP planner and uses
lookahead only to improve its `(SWAP count, makespan)` pair. It retains assignments
and waits on ties. Raw diagnostics identify this as `baseline-improvement-v1`.
All new runs write only `summary.csv` and `raw_data.zip`; the latter contains one
JSONL stream with all inputs, repeat measurements, and traces. See
[raw data format](docs/experiments.md#compact-raw-data-output).

Public policy names are `FixedSite`, `NextRequestGreedy`, and `JointScheduler`.
JointScheduler is implemented by the existing `LookaheadJoint` class. Its private
`_ImmediateSwapPlanner` builds the initial plan and completes candidate tails,
ranking immediate request completion by SWAPs and stable action order. It is not
selectable from configuration. Legacy `LookaheadJoint` and `ShortestPathRelocation`
policy names are rejected with a migration message; use `JointScheduler` instead.
The optional tiny oracle remains a separate explicitly selected backend, not a
fourth default policy. Existing historical outputs are not renamed or rewritten.

Reuse the same `--output results/comparison` for all cases. The final summary now
includes `case_id` and combines all archived runs. Duplicate case/repeat/policy
keys are rejected; `--case-id` labels a new input and `--repeat` adds a timing
measurement. See [shared output folder](docs/experiments.md#shared-output-folder).
