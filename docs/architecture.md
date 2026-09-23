# Architecture and planning

The package uses one focused Python module per responsibility, avoiding empty
subpackages and unnecessary dependencies.

| Module | Responsibility and contracts |
| --- | --- |
| ir | Gate, CircuitDAG, stable operands, predecessor/successor relations |
| analysis | Demand, DemandWindow, dependency-only readiness/critical-tail estimates |
| model | Layout, MSC, SystemState, OperationModel/OperationSpec, Action, RelocationPlan |
| routing | Bounded BFS over legal data permutations; site-path generation; oracle walks |
| sim | Policy-neutral events, reservations, ExecutionTrace, isolated state snapshots |
| policies | Plan, common dispatcher, baselines, JointScheduler, execution statuses |
| oracle | Integer-time exhaustive action search for tiny-instance comparisons |
| metrics | Direct event-time accumulation of waits and SWAP layers; no replay |
| experiments | Seeded workloads, resolved configurations, RunResult, exports/comparisons |

`propose(simulator, demand_window) -> Plan` is the injected-service equivalent of
`propose(state, demand_window)`. It evaluates cloned state and returns a tuple of
committable actions. It does not modify live placement, reservations, pending
events, transactions, assignments, or lifecycle state. `begin` contains the
assignment, site choice, and ordered movement path; the transaction tracks its
remaining path and dependency on previous steps. A policy commits one begin action
and the common dispatcher executes its committed operations.

## Shared execution service

At each decision point, the dispatcher starts available committed relocation steps
and production, then dependency-ready circuit gates in input order. T consumption
requires the actual owned output. Nonadjacent ready data gates and supplied T
requests use the common data/data permutation BFS. Each edge is a legal adjacent
SWAP; no distance metric substitutes for movement cost. Only the first SWAP is
committed, with one data-routing SWAP active at a time. It replans after completion.
This conservative shared routing choice does not restrict the simulator or oracle
from executing other legal concurrent SWAPs.

The routing BFS can take detours and temporarily increase target distance. MSC
sites are excluded from data/data routes. Site-preparation candidates supply
idle/data moves. The route-state bound may miss a route; it cannot prove one absent.
The dispatcher does not pre-route an unready gate or an unsupplied T request.
Ordinary data operations execute with their configured duration and resources.

When no more common work can start, the policy proposes a new request assignment.
If none is available, execution advances to the next completion. Active and committed
work is preserved. Assignment decisions and completion events therefore refresh
DAG estimates without cancelling work.

## Policies and tie breakers

Demand estimates use completed and active gate times and a dependency-only duration
lower bound, ignoring routing and future production. The critical tail similarly
sums gate durations along dependencies. These are readiness estimates, not deadlines. Request windows include not-yet-ready
T gates, sorted by estimated readiness then stable gate ID.

- **FixedSite:** take the first request in the window, assign the lexicographically
  first idle MSC, retain its site, and use the shared router for data movement.
- **NextRequestGreedy:** same request/MSC assignment rule, simulate each legal site
  path through completion of that request, including initialization, production,
  movement, ordinary gates, and resource/dependency waits. Rank by immediate request
  completion time, then added SWAPs, then stable action representation.
- **Internal immediate-SWAP planner:** evaluate the same immediate legal site paths through
  request completion, but rank only by added SWAPs and stable action representation.
  Timing feasibility is checked by execution; elapsed time and downstream demand
  do not enter its ranking. This deliberately differs from NextRequestGreedy.
- **JointScheduler:** branch on request order, idle MSC assignment, and preparation
  walk, across a bounded number of assignment decisions. Use simulator snapshots
  for every branch, and complete each with the shared immediate-SWAP
  heuristic. Require completed rollout states. Seed a full schedule using the internal immediate-SWAP planner first. Rank full
  schedules by total SWAPs, then makespan; retain the incumbent on exact ties.
  Only the first assignment is committed. Keep the best simulated completed full continuation;
  subsequent limited searches retain its next assignment or waiting decision
  unless a strictly better completed schedule is found.
  The live trace must exactly match the cached prefix to use that fallback.

Baseline immediate rollouts may not complete when a future request needs an
unassigned predecessor. In that case the baseline falls back to its first legal
retention candidate, and any eventual failure remains visible. All policies execute without a completion deadline. Lookahead does not accept a merely locally feasible prefix.

The beam uses prefix SWAP count, current time, and stable state/action ordering for
pruning. This is a search heuristic, not a replacement objective. Completion tails
are heuristic and bounded; a feasible schedule may be missed.

## Search settings and ablations

| Setting | Meaning |
| --- | --- |
| window | Number of unassigned T requests considered, including future requests |
| lookahead_depth | Assignment decisions expanded before a shared completion tail |
| beam_width | Maximum retained prefix states per depth |
| candidate_limit | Soft per-node candidate cap; all considered retention pairs survive |
| path_limit | Maximum simple site walks per MSC, retention first |
| max_hops | Maximum heuristic site-walk edges |
| route_states | Data-permutation BFS state bound |
| search_limit | Full completion rollouts per lookahead decision |
| event_limit | Driver iterations per run/rollout and bounded prefix advancement |
| no_site_changes | Remove moved-site candidates from all planning/completion steps |
| fixed_assignment | Assign T requests in circuit order round-robin across sorted MSC IDs |

Heuristic walks are BFS-ordered simple paths and can include multiple paths to one
site. The oracle does not use these limits and enumerates revisiting walks too.
A depth-one ablation still evaluates a full heuristic completion without a completion deadline.
Fixed assignment also applies to completion rollouts; it waits when the designated
MSC is occupied. Limits must be positive integers. Candidate_limit may be exceeded
only to preserve the retention options already under consideration.

## Experiment-only execution

The standalone test suite and independent trace validator have been removed at the
user's request. `RunMetrics` receives operation starts/completions and elapsed
intervals from the simulator. It does not reconstruct execution or make duplicate
legality checks. Snapshots copy measurement state along with the execution state.
The same metric collector serves every policy and the optional exact backend.
Operation preconditions and resource reservations remain enforced by the simulator:
removing them would change the modeled schedules rather than merely remove tests.

The policy layer implements the experimental decision rules. It is necessary to
compare fixed sites, immediate decisions, and DAG-aware joint lookahead under common
conditions. Lookahead rollouts are algorithm computation, not self-tests. The oracle
is a separately selected tiny-instance optimization reference. The single `run`
entry point executes the configured `variants`, or only the variant named by
`--variant`. Single and multiple executions always write the same summary and
raw archive structure. No separate demo or comparison entry point remains.

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
