# Experiments and metrics

## Reproducible inputs and outputs

Each archived run's resolved `config` embeds the concrete circuit, all usable and blocked
coordinates, placement, named MSCs, costs, mode, seed, every policy limit,
model assumptions, schema/model versions, backend, and available environment/code
metadata. The source hash distinguishes builds even when the directory is not a Git
checkout. The seed is local to workload generation. Spatial workload targets are
chosen from the initial placement, never from the current moving placement.

The archived JSON config embeds the circuit, so analysis
does not need the original circuit file. General YAML input needs the optional PyYAML
parser. Unknown top-level/footprint/settings keys are rejected. Output directories may be empty or already contain the compact summary and raw archive. Results are ignored by source control by default.

Each run record's `trace` array records begin, operation start, completion, and optional oracle tick
events. Starts include resources, logical gate/request identity, MSC identity, and
end time. Completions record placement and MSC/output state for schedule analysis. IDs, ordering, and integer operation costs are stable.
Each run record's `metrics` object separates execution status and circuit completion.
`summary.csv` keeps completed, stalled, and not-found rows; no outcome is
removed because a policy performs poorly. Input and operation-precondition errors stop the CLI
with a diagnostic, rather than exporting an invalid schedule as a successful run.

`run` resolves one workload before applying `variants`. Variants may change only
policy, relocation mode, policy settings, and backend (`heuristic` or `oracle`).
They cannot change resources, gate operands or operation costs. There is
no circuit completion deadline.
Development stages do not partition experiments or results. The unified
`configs/experiment.yaml` compares the three basic policies in serialized mode with
two MSCs and one shared workload. Single-MSC inputs remain supported by the same engine.
The controlled workload helper varies one input factor at a time, then adds
two combined cases. It writes one root summary covering all cases.

Trace bytes and non-wall-clock metrics are deterministic for identical resolved
inputs and code. Planning wall time and environment metadata are excluded from
byte-for-byte reproducibility claims.

## Metrics

- `swap_count`: completed data/data and idle/data exchanges, one each.
- `swap_depth`: SWAP-only physical-patch dependency layers. In trace start order,
  a SWAP's layer is one plus the largest preceding SWAP layer on either involved
  patch (zero if none). Other operation types do not add layers. Independent SWAPs
  can share a layer. This is a defined logical layer measure, not elapsed time.
- `makespan`: final simulation timestamp; for completed runs this is circuit completion.
  For partial runs it is the observed horizon, not a claimed completion time.
- `circuit_completed`: all circuit gates and lifecycle work complete. An empty completed circuit has makespan 0.
- `relocation_count`: committed preparations whose destination differs from their
  source. Returning walks count their SWAPs but not a site change. Partial runs may
  include committed site changes that have not yet completed.
- `planning_seconds`: wall time in policy proposals plus the live shared dispatcher
  (including routing and operation dispatch). Candidate rollouts are included inside
  proposal time; their internal timers are not added again. File I/O and export
  are excluded. Oracle timing covers its entire search.
- `request_waits`: for each T, interval from actual last predecessor completion
  (or time 0 if none) to its consumption start. The consumed operation's duration
  is excluded. Partial runs record waiting only through their observed horizon.
- `supply`: time without the assigned, available output. Unassigned/allocation
  delays count as supply waiting. Production and initialization therefore count.
- `adjacency`: time an assigned MSC's current site is not adjacent to the target's
  actual current location. It can overlap missing supply. Unassigned requests have
  no selected site and therefore no adjacency label.
- `resource`: time current target/site patches intersect active reservations or
  complete-path reservations. This can overlap both other blocker labels.

Total waiting integrates the interval once. Blocker durations are diagnostic labels
and must not be summed as a decomposition. Labels may leave an allocation/policy
delay unclassified after resources are ready; total waiting still includes it.
`total_request_wait` and `supply_wait` sum their respective values over requests,
not over blocker categories.

## Tiny exact backend

Supported scope: exactly one MSC; at most three usable cells; at most four gates;
positive integer oracle search horizon at most 14; all v1 gate kinds, positive durations, and
relocation modes. Larger or multi-MSC oracle inputs are explicitly rejected.
The solver optimizes SWAP count only. Its incumbent tie makespan is not a separate
proved minimum; no weighted cost or heuristic site restriction is used.

The search enumerates, at every integer timestamp:

1. Every legal data/data edge SWAP.
2. Every unassigned T and every cardinal relocation walk of length affordable
   within the remaining oracle search horizon, including revisits and returning paths.
3. Each committed transaction's next idle exchange or initialization.
4. Production, dependency-ready single/two-qubit gates, and assigned consumption.
5. A unit wait, including when no operation is active or other work could start.

Multiple starts at the same time enumerate all compatible operation combinations.
Waits inside committed transactions are allowed. Walk length is bounded only by
remaining time after mandatory initialization, production, and consumption; these
operations and the walk are sequential for that MSC, so a longer walk cannot be
part of a complete feasible schedule. There are no heuristic shortest-path or
candidate-site restrictions. Integer start times are part of the declared model.

Search states include absolute time, placement, lifecycle/output ownership,
completed gate identities, active actions with end times, and remaining committed
paths. Histories with the same future-relevant state are dominated by the smaller
SWAP count. Completed timestamps affect reported waits but not future legal actions,
so they do not distinguish canonical states. Operation IDs and historical trace
ordering similarly do not affect future legality of disjoint operations.

The search prioritizes completed plus active SWAPs and prunes states that cannot
improve the incumbent SWAP count. `oracle_state_limit` bounds expanded states.
If a useful frontier remains at the limit, `reason` reports the interruption.
An available incumbent is measured using the same simulator metrics as policies.
No optimality status or gap to a reference optimum is part of experiment evaluation.
Ordinary policy comparisons do not invoke the oracle.

## Online measurement and experiment scope

Measurements now come directly from event execution (`metrics_version: online-v1`),
not from independent replay. Wait intervals end before same-time completion effects
are applied, and resume from the updated state. This retains the documented
predecessor-readiness and overlapping-blocker definitions. Snapshot measurements
are isolated for candidate evaluation. Complete schedules use circuit completion
time; partial schedules use their actual simulated horizon.

The code-test suite, repeated candidate/final trace checking, and standalone validate
command have been removed at the user's request. The implementation brief and
AGENTS.md document this experiment-focused scope. Results are simulator-derived,
not independently replay-certified.
Model resource semantics are unchanged. The metrics version and source hash identify
the measurement implementation; planning wall times include different work from
older runs and should not be treated as directly comparable performance evidence.

The supplied configurations are small experiments. Data-permutation routing and
lookahead can grow rapidly and are bounded. One active data-routing SWAP and the
restriction of idle-MSC exchanges to assigned production preparation limit the
heuristic schedule space. Use larger varied circuits and multiple seeds before
interpreting general improvements; no such benchmark claim follows from these examples.

## Unified execution

`run` executes a config's `variants` list through a common engine. Without that
list it executes one variant from the top-level policy/backend. `--variant NAME`
selects one entry from the same config, without duplicating the problem definition.
All cases write only `summary.csv` and `raw_data.zip`. Oracle is a
backend choice, not a separate command. The supplied configurations are
`experiment.yaml` and the optional tiny-backend example `tiny.yaml`.

The former `compare` key is now `variants`; former `oracle: true` entries are now
`backend: oracle`. Old demo/compare configuration duplicates and their CLI commands
have been removed. Existing result directories are kept as historical data.

## Performance evaluation

Compare observed SWAP count among completed runs with identical
inputs. Report makespan, waits, and planning wall time alongside it. Equal SWAP
counts are ties on the primary objective, even when secondary metrics differ.
Do not infer general superiority from a single circuit or omit failed outcomes.

Deadline-free summary and metrics exports use `evaluation_version: performance-v2` and omit
`proof_status`. Operation/resource semantics and trace schema are unchanged.
Historical result directories retain their original fields; they are not rewritten.

## Deadline-free experiment migration

Remove `budget` from old input configurations; it is rejected with an explicit
migration error, not silently ignored. `performance-v2` results omit `budget` and
`budget_satisfied` and expose `circuit_completed`. Durations, makespan, and request
waits use abstract integer cycles, not calibrated physical error-correction cycles.
Completed JointScheduler continuations rank by SWAP count, then makespan,
retaining the incumbent on ties. A long complete continuation is never rejected for its duration.
Search/event/routing limits still bound computation; partial runs remain visible.

The optional oracle retains a separate `oracle_horizon` (default 14, range 1..14)
to bound its integer-time action enumeration. This is an oracle search restriction,
not an execution deadline for policies. Oracle metrics report the horizon explicitly;
do not interpret its result as an unrestricted reference for deadline-free policies.
Existing result directories are preserved with their original semantics.

The root `summary.csv` is regenerated from all archived runs on successful writer
closure. Single and multiple variants use the same archive and aggregation path.
Invalid input and duplicate keys reject the addition without changing existing
results. During a batch, the previous final summary remains available. New raw
records are staged in temporary files and published when the batch succeeds.

The performance summary has eight columns: `name`, `status`, `swap_count`,
`swap_depth`, `makespan`, `total_request_wait`, `relocation_count`, and
`planning_seconds`. Keep status so partial runs cannot be mistaken for completed
low-cost schedules. Makespan and waits use abstract cycles; planning time uses
seconds. Configuration, versions, duplicate completion fields, search counters,
failure reasons, and detailed wait diagnostics stay in the archived run records,
not the summary. Custom workload batches additionally label
rows with `case_id`. These observations do not establish general superiority.

## Controlled workload comparison

Run the predefined cases through the existing experiment engine:

```bash
PYTHONPATH=src python3 examples/workload_sweep.py --output results/comparison --repeats 3
```

The default includes 18 cases (54 policy rows, 162 executions with three repeats).
Each case compares only FixedSite, NextRequestGreedy, and
JointScheduler. MSC count stays two, relocation stays serialized, and operation
costs and search settings come from `configs/experiment.yaml`. Policy algorithms
and resource semantics are unchanged. No oracle or ablation is run.

The reference has four data qubits and six alternating T requests. Cases vary
localized/migrating/distributed demand; serial/fork-join control dependencies;
CX every third/every request; near/far/asymmetric/shuffled data placement (shuffle
seeds 7, 19, 31); twelve requests; or six data qubits. Two final cases combine
migrating + fork/join + CX every second request, and distributed + serial + CX
every request + shuffled placement. These are illustrative synthetic treatments,
not calibrated physical circuits or a statistically representative sample.

`workload` additionally accepts `dependency` and `cx_every`. `independent` keeps
only implicit per-qubit dependencies, `serial` chains complete request blocks,
and `fork_join` places a barrier between pairs of request blocks. There is no
extra barrier gate or extra operation cost. Each block is optional CX, then T, H.
CX before every kth request makes the rate explicit; zero disables CX.
`distributed` cycles across all logical targets in initial-coordinate order.
`data_heavy` retains seeded random targets and defaults to a CX before every request.

The helper first fixes the circuit against a reference placement. Placement cases
then permute only data IDs, keeping gate operands and dependencies identical.
Near/far assigns the distinct T targets first, by ascending/descending distance
from the left MSC column. Asymmetric swaps the second and last data positions;
random uses a local seeded shuffle. No placement changes during execution are
used to regenerate demand. Layout growth retains exactly N data + 2 MSC patches;
it is a size comparison, not a claim of policy improvement from added resources.

The archived `manifest` record contains treatments, generator version, repeat
count, and timing aggregation. Each `case` record includes a runnable `input`
configuration. Each `run` record identifies its case, policy, and repeat and stores
the resolved configuration, measurements, and trace. Policy execution order rotates
by repeat index. Root `summary.csv` has `case_id` plus the eight performance columns;
`planning_seconds` is the median across repeats, including measurements of partial
outcomes. Other columns must match across repeats or aggregation stops with an
error, preserving individual raw records. The root is updated after the batch completes.

Use `--case migrating --case cx_dense` to select a small subset. All predefined
cases are retained by default regardless of which policy wins. Compare SWAPs only
where both policies completed, report completion counts separately, and examine
added planning time together with makespan and wait changes. Repeats estimate
local timing variation; they are not independent workload samples or evidence of
statistical significance. The fixed bounded search may behave differently on
larger circuits, and not-found outcomes do not establish infeasibility.

## Compact raw data output

New ordinary and controlled runs create exactly two files: `summary.csv` and
`raw_data.zip`. Historical result directories are unchanged. There are no case,
repeat, or policy directories. The ZIP contains one UTF-8 `raw_data.jsonl` stream.
Each record has `raw_schema_version: 1` and a `type`:

- `manifest`: controlled-case definitions, repeat count, and aggregation rules.
- `case`: `case_id`, treatment `spec`, and runnable `input` configuration.
- `run`: `case_id` (null for a standalone run), one-based `repeat`, variant `name`,
  resolved `config`, complete `metrics` including status/reason, and `trace` array.

Standalone runs contain only run records. Incomplete outcomes are retained in both
summary and archive. The archive closes on normal completion or a Python exception;
read it after the writer has closed. A forced process termination can leave temporary files behind; existing
results stay available until publication. Run writers sequentially, not concurrently. The previous final summary remains in place until successful completion.

Read the records without creating directories:

```python
import json
from zipfile import ZipFile

with ZipFile("results/integrated_comparison/raw_data.zip") as archive:
    with archive.open("raw_data.jsonl") as stream:
        for line in stream:
            record = json.loads(line)
            if record["type"] == "run":
                print(record["case_id"], record["name"], record["metrics"]["swap_count"])
```

## Shared output folder

Use one `--output` path (for example `results/comparison`) for ordinary and workload
runs. The only final files are `summary.csv` and `raw_data.zip`. Existing compact
archives are read and combined with new records; historical multi-folder layouts
are not implicitly migrated. Temporary working files are removed on success or a
handled exception. Do not run simultaneous writers against the same destination.

The summary always includes `case_id` plus the eight performance columns. The CLI
uses the configuration filename stem as case ID unless `--case-id` is supplied;
programmatic runs without a case ID use `default`. Each row identifies one
(case_id, policy), with median planning time across recorded repeats. Repeated
inputs and non-timing outcomes must agree; otherwise use a new case ID. Details
and all individual measurements remain in the raw archive.

A (case_id, repeat, policy) key must be unique. Repeating a command with the same
key is rejected rather than overwritten or counted twice. Use `--repeat 2` for a
second standalone measurement, `--case-id` for a different input, or `--case` in
the workload helper to add previously unrecorded cases. No result subfolders are
created. On an exception, the pending batch is discarded and previous files are
preserved. Publication replaces the archive then the summary; the two-file swap
is not an atomic transaction under forced process termination.
