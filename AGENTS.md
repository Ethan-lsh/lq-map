# AGENTS.md

Repository instructions for Codex and other coding agents working on `mscmap`.

## Purpose and instruction scope

Build a reproducible research framework for DAG-aware joint optimization of magic-state cultivation (MSC) production locations, logical-qubit SWAPs, and T-request scheduling.

Apply these instructions throughout this repository. Follow higher-priority instructions and explicit current user requirements. Read any more specific `AGENTS.md` files before editing their directories. These instructions define ongoing project constraints; they do not authorize unrelated work or require reimplementing completed milestones on every task.

When available, read `mscmap-codex-implementation-prompt.md` for the implementation brief and `msc-mapping-scheduling-research.md` for the research proposal. Use only files available in the repository or supplied by the user. The essential constraints are restated below so this file is independently usable. If sources materially disagree, identify the disagreement rather than silently changing the scientific model.

## Current execution scope

Maintain the current experiment-focused implementation. There is one public CLI,
`python3 -m mscmap run`, and one output layout for single and multiple variants.
Do not restore separate demo/compare/oracle/validate commands, duplicate example
configurations, a standalone code-test suite, or independent trace replay unless
explicitly requested by the user. This does not remove input validation, operation
preconditions, resource reservations, dependency checks, or termination checks: those
conditions define the scientific execution model.

The native implementation has no required external quantum framework. Keep the
framework-neutral circuit and execution contracts; add SDK integrations only for an
explicitly requested task. Prefer focused modules and reuse existing configuration
and execution paths rather than introducing unnecessary files or directories.

## Working procedure

1. Inspect the repository, applicable instructions, current implementation, and relevant experiment configurations. Check existing changes and preserve unrelated work.
2. State the intended change briefly. For substantial tasks, maintain a short plan with independently verifiable milestones.
3. Implement the smallest coherent change that fully satisfies the requested scope. Continue through implementation and verification; do not stop at a proposal or placeholders.
4. Use existing repository conventions and tools. Make routine implementation choices autonomously. Ask only when unresolved ambiguity materially changes the research question, modeled feasible schedules, or requested scope.
5. For behavior changes, execute a small relevant experiment and inspect its status, metrics, and summary. Fix material failures and update affected documentation and examples. For documentation-only changes, check consistency with the actual CLI and files without rerunning unrelated experiments.
6. Report what changed, checks actually executed, outcomes, and concrete limitations. Clearly distinguish implemented, verified, and deferred work.

Default to English code, identifiers, docstrings, configuration comments, and technical documentation. Use Korean for user-facing progress and final reports unless requested otherwise. Keep reports concise.

Do not overwrite user work, weaken execution preconditions to conceal failures, fabricate results, or present an unexecuted command as successful. Do not add unrelated services, interfaces, dependencies, or external integrations.

## Scientific invariants

- The objective is total logical SWAP minimization without a circuit completion deadline. Do not silently substitute a weighted objective or geometric distance. Baseline heuristics may use their documented decision rules, but all results use the same objective and evaluation metrics.
- Relocation selects where the NEXT cultivation starts. Never move, cancel, or restart an active cultivation merely to make a candidate convenient.
- Consider retaining the current site whenever legal. A future request at another location does not itself require relocation.
- Keep logical-qubit IDs, physical patch coordinates, MSC IDs, and produced-state IDs distinct.
- SWAP changes data placement, not the logical operands of circuit gates. Retain placement changes for downstream execution; no mandatory restoration.
- Allow only cardinally adjacent movement. Temporary distance increases and detours are legal when resources permit.
- Enforce dependencies for every gate. Readiness estimates and urgency estimates never replace actual execution preconditions.
- Include ordinary data gates and their movement/resource costs. Do not make nonlocal two-qubit gates free or instantaneous.
- Produce and consume each magic state through explicit lifecycle transitions. An unconsumed state occupies resources and cannot be duplicated or silently discarded.

Use deterministic production and positive integer durations in abstract time units for the initial framework. Production failures, retries, hardware control, and physical error-correction simulation are outside the initial scope unless requested.

Do not invent scientific formulas or attribute assumptions to unread references. Cite scientific claims and formulas only when verified in an available source. Label implementation heuristics, abstract timing constants, and modeling choices as such; do not present them as established physical results.

## Resource model

The initial single-MSC setup has N data qubits and one reusable production patch. Explicitly enumerate usable and blocked grid cells. Never introduce hidden routing cells, buffers, ancillas, or additional production capacity.

Keep an explicit, replaceable abstract operation model describing:

- Preconditions, duration, required patches/boundaries/auxiliary resources, and completion effects for each operation.
- Data-to-data SWAP and data-to-idle-patch exchange semantics and accounting.
- How site evacuation preserves data and exposes a reusable production site.
- Initialization and production occupancy, MSC capacity, and output ownership.
- Consumption adjacency, occupied resources, and resulting data/production-patch states.
- Geometry and resource requirements of ordinary data operations.

An occupied destination cannot be overwritten. Initialization and active production block incompatible movement. Production completion alone does not release a patch containing an output. Reject unsupported footprints/configurations explicitly. A runnable abstract patch model is not evidence of physical cultivation feasibility.

Record assumptions in `docs/model-assumptions.md` and in resolved experiment metadata. When changing resource semantics, update execution, online measurement, affected oracle support, configurations, and documentation together. Version or otherwise distinguish results generated under different semantics.

## Architecture boundaries

Use a Python `src/mscmap/` package with the following responsibilities:

| Module | Responsibility |
| --- | --- |
| `ir` | Circuit input, stable logical operands, dependency DAG |
| `analysis` | Demand windows and dependency-based estimates |
| `model` | Layout, state, lifecycle, operation contracts |
| `routing` | Legal movement and site-preparation candidates |
| `sim` | Event execution, reservations, traces |
| `policies` | Baselines and joint lookahead decisions |
| `oracle` | Optional exact optimization reference for explicitly supported tiny instances |
| `metrics` | Direct event-time measurement through `RunMetrics`; no independent replay |
| `experiments` | Workloads, runs, metrics, exports |

The simulator is policy-neutral. Every policy uses the same operation/resource semantics and measurement code. Routing proposes executable actions; it does not bypass reservations. Analysis supplies estimates; it does not mutate execution state.

Use explicit contracts equivalent to `CircuitDAG`, `SystemState`, `OperationSpec`, `DemandWindow`, `RelocationPlan`, `Plan`, `ExecutionTrace`, `RunMetrics`, and `RunResult`. Prefer typed dataclasses, enums, small focused functions, and minimal dependencies. Avoid mutable global simulation state and hidden random generators. Validate inputs with actionable errors.

Snapshot and rollout operations must isolate mutable placement, reservations, lifecycle state, pending events, and measurement accumulators. Policy code must not modify the live state while evaluating a candidate. Keep serialized traces and configurations schema-versioned.

## Simulation and scheduling

Use deterministic discrete-event execution. Document same-time event ordering, reservation endpoints, and when effects become visible. Allow a legal successor at the time its predecessor finishes. Use stable IDs and deterministic tie breakers; do not rely on unordered collection iteration.

Check all resource conflicts across production, initialization, SWAP, consumption, and data gates. Preserve active and committed work during replanning. Detect stalled execution without infinite loops and expose diagnostic reasons.

Development stages describe implementation order, not separate experiments.
Use these relocation constraints within the common experiment engine:

| Mode | Constraint |
| --- | --- |
| Single MSC | One production resource |
| `serialized` | Multiple MSCs; independent production overlaps; full relocation transactions use a global mutex |
| `disjoint_paths` | Concurrent relocation transactions cannot share any patch in their complete reserved paths |
| `space_time` | Relocation paths may overlap spatially when operation intervals and all other resource constraints permit |

In the current model, a transaction starts at request assignment/path commitment and ends at initialization completion; initialization is included. Release the serialized relocation mutex before cultivation so it does not accidentally serialize independent production. Reserved paths must also exclude incompatible ordinary operations. Enforce destination ownership and prevent duplicate request assignments.

## Optimization policies

Policies are the experimental decision rules, not software self-tests. Keep the
baselines and JointScheduler so the effect of future-demand-aware choices can be
compared under the same resources. Candidate rollouts and full-circuit evaluation
are part of optimization; do not remove them as redundant testing.

Provide the common interface `propose(state, demand_window) -> Plan` or an equivalent documented contract.

- `FixedSite`: retain production sites; move data as required.
- `NextRequestGreedy`: consider immediate end-to-end processing of one request, including initialization, production, and waits.
- `JointScheduler`: jointly consider upcoming requests, sites, assignments, and movement through bounded simulation.

Keep baseline definitions and tie breakers explicit. Share supporting routing/data-gate scheduling where the experiment does not intentionally vary them. Distinguish baseline decision rules rather than giving identical implementations different names.

For lookahead, generate legal candidates including site retention, simulate on snapshots, and use a shared completion heuristic to construct full remaining-circuit schedules. A locally feasible prefix does not establish full-circuit completion. Keep a simulated completed full continuation as fallback, commit only the intended prefix, and refresh demand estimates after execution events.

Expose lookahead depth, candidate/beam limits, and search limits in configuration. Bounded search can return `feasible_plan_not_found`; it cannot establish infeasibility by failing to find a plan.

## Online measurement and optional exact reference

`RunMetrics` accumulates SWAP layers and T-request wait intervals directly from
operation starts, completions, and elapsed simulation time. Final metrics use the
same simulator state for every policy and backend. Keep `metrics_version: online-v1`
in output, along with model/schema versions and code metadata. There is no
`TraceValidator`, independent replay, or second pass that certifies candidate or
final traces. Report results as simulator-derived, not independently replay-certified.

The oracle is an optional analysis reference, selected explicitly with
`backend: oracle` at the top level or in a variant. It is not automatically run
before or after ordinary policy experiments. Its current supported scope is one
MSC, at most three usable cells, at most four gates, and an integer oracle search horizon at
most 14. Reject unsupported inputs; do not imply exact multi-MSC support.

Evaluate experimental performance using observed SWAP count, depth, makespan,
request waits, circuit completion, relocations, and planning wall time under
identical inputs. Do not export proof status or evaluate gaps to a reference
optimum. Keep execution status and circuit completion separate. The optional
oracle remains a tiny-instance search comparison; report search-limit termination
in the reason, without optimality labels. Preserve its supported action enumeration,
canonicalization, pruning, and input limits.

## Current model and search limits

The model has exactly N data cells plus M reusable MSC cells. Idle/data exchanges
are permitted only inside assigned next-production preparation transactions; there
is no standalone unassigned MSC relocation or extra idle routing buffer. The shared
heuristic router uses data/data exchanges, commits one data-routing SWAP at a time,
and does not pre-route unready gates or unsupplied T requests. These heuristic
restrictions do not limit all legal concurrent actions available to the simulator
and oracle. Heuristic site paths and beam searches are bounded; not every joint
schedule is considered. Clearly state these restrictions in research interpretation.
Changing the model action space requires versioned assumptions; do not silently
broaden it or claim that the current heuristic solves the general problem exactly.

## Experiments and reporting

Compare identical circuits, initial placement, usable patches, MSC counts, operation costs, with no completion deadline. Separate additional-MSC benefits from scheduling-policy benefits.

Include localized, migrating, alternating, and data-interaction-heavy T-demand workloads. Define their logical targets relative to the initial placement; never change the circuit after movement to preserve a desired spatial pattern. Keep ablation settings available for explicitly configured follow-up experiments;
do not include them in the default basic-policy comparison.

Record SWAP count, defined SWAP depth, circuit makespan, T-request waits, circuit completion, relocation count, and planning wall time. Distinguish logical depth from elapsed time. Start request waiting after predecessor completion; record supply, adjacency, and resource blockers without double-counting overlapping intervals. Document allocation-delay treatment.

Use a `variants` list for one or more experimental conditions. Without the list,
run one condition using the top-level policy/backend. `--variant NAME` selects a
single named condition from the same config. Variants may override only `name`,
`policy`, `mode`, `settings`, and `backend`; they cannot change the common circuit,
layout, placement, MSC count, costs. Resolve the workload once per run.

Always write only `summary.csv` and `raw_data.zip` at the output root.
Archive resolved configurations, metrics, and traces as typed JSONL records. The output
layout is identical for one or many variants. The configuration key is `variants`,
not the removed `compare`; select the exact backend with `backend: oracle`, not
`oracle: true`. Preserve historical result directories rather than migrating or
overwriting them implicitly. Include seed, model assumptions, policy limits, schema version, and available code/environment versions. Keep failed and infeasible runs visible. Avoid overwriting prior result directories implicitly; keep large generated results out of source control by default.

Small example runs establish observed software behavior, not general research superiority. Never fabricate benchmark numbers, exclude inconvenient outcomes silently, or mix incompatible model versions. Exclude wall-clock measurements from deterministic-output claims.

## Development sequence and completion

The existing implementation covers the following responsibilities. For later tasks,
change only the requested behavior and its dependencies; do not restart the roadmap:

1. Model, circuit importer, simulator, and online measurement.
2. Single-MSC baselines and hand-checkable examples.
3. Optional tiny exact reference under the same model and objective.
4. Joint lookahead, simulated feasible continuation, and policy comparisons.
5. Multi-MSC assignment with serialized relocation and overlapping production.
6. Disjoint-path and space-time relocation experiments.

Maintain the single `run` entry point and these configuration files:

- `configs/experiment.yaml`: three basic policies under identical resources and
  serialized relocation; no default ablation or additional mode variants.
- `configs/tiny.yaml`: tiny heuristics and the explicitly selected exact reference.

Representative invocations from the repository root:

```bash
export PYTHONPATH="$PWD/src"
python3 -m mscmap run --config configs/experiment.yaml --output results/comparison
python3 -m mscmap run --config configs/experiment.yaml --variant JointScheduler --repeat 2 --output results/comparison
python3 -m mscmap run --config configs/tiny.yaml --variant oracle --repeat 2 --output results/comparison
```

Use one shared output directory for the requested experiments. Use the repository's actual installation and
formatting tools where relevant; do not introduce or run a standalone self-test
suite by default. The workload helper `examples/workload_sweep.py` uses the same
`experiments.run` path. Do not run broad sweeps for routine edits.

A requested behavior change is complete when implemented, its interfaces/examples/
docs are consistent, and a small representative experiment has actually run or its
blocker is reported. Inspect the resulting status and measurements without claiming
independent replay validation. A documentation-only change needs consistency review,
not new benchmark output. Report only actions actually performed and concrete
limitations. Do not leave required paths as silent no-ops or stubs while reporting
completion.

## Deadline-free experiments

The current user-authorized objective is logical SWAP minimization without a
circuit completion deadline. Keep operation durations and compare completed runs
by SWAP count, reporting makespan and waits in abstract cycles alongside it.
Reject the removed `budget` input; export `circuit_completed`, not budget outcomes.
Use `evaluation_version: performance-v2`. Preserve computation limits and completed
full-continuation fallback. The optional oracle alone uses `oracle_horizon` (1..14)
as an explicitly reported search restriction, not a common feasibility constraint.

The performance summary has eight columns: `name`, `status`, `swap_count`,
`swap_depth`, `makespan`, `total_request_wait`, `relocation_count`, and
`planning_seconds`. Keep status so partial runs cannot be mistaken for completed
low-cost schedules. Makespan and waits use abstract cycles; planning time uses
seconds. Configuration, versions, duplicate completion fields, search counters,
failure reasons, and detailed wait diagnostics stay in the archived run records,
not the summary. Custom workload batches additionally label
rows with `case_id`. These observations do not establish general superiority.

## Integrated baseline and lookahead

JointScheduler is a single baseline-improvement method. At its first planning
point it simulates the internal immediate-SWAP planner to completion and, if that
succeeds, preserves the full schedule before exploring candidates. The baseline
rollout is additional to `search_limit` candidate rollouts and is included in
planning time. The immediate-SWAP planner is internal only, not an additional result row.

Candidate tails still use the internal immediate-SWAP planner. An incumbent is replaced only
by a strictly smaller `(total SWAPs, makespan)` pair; exact ties retain it. Preserve
waiting decisions as well as assignments. Live execution must match the saved
prefix; divergence raises an error instead of silently discarding the incumbent.
No active work is cancelled and no resource legality checks are bypassed.

A completed baseline plus preservation of every continuation prevents an accepted
schedule from increasing its SWAP count. This is not an optimality claim, a bound
on planning time, or a promise of completion when execution limits interrupt the
run. If the baseline rollout fails, ordinary bounded candidate search may still
find a completed schedule; otherwise report not-found or the execution limit.
Raw metrics include `integration.version: baseline-improvement-v1`, baseline
status/cost, and the accepted cost sequence. These diagnose preservation without
adding summary columns. Model and measurement semantics remain unchanged.

Public policy names are `FixedSite`, `NextRequestGreedy`, and `JointScheduler`.
JointScheduler is implemented by the existing `LookaheadJoint` class. Its private
`_ImmediateSwapPlanner` builds the initial plan and completes candidate tails,
ranking immediate request completion by SWAPs and stable action order. It is not
selectable from configuration. Legacy `LookaheadJoint` and `ShortestPathRelocation`
policy names are rejected with a migration message; use `JointScheduler` instead.
The optional tiny oracle remains a separate explicitly selected backend, not a
fourth default policy. Existing historical outputs are not renamed or rewritten.

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
