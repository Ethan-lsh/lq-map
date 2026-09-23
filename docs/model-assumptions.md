# Abstract patch model v1

Model ID: `abstract-patch-v1`; configuration/trace schema: `1`.
These are implementation modeling choices, not literature-derived physical claims.
The implementation brief and AGENTS.md are the available project sources. The
separately named research proposal was not present at implementation time.

## Geometry and conservation

The layout explicitly lists `usable` and `blocked` integer grid coordinates;
every unlisted cell is unavailable. Lists must be disjoint and contain no duplicates.
There are exactly N data cells and M reusable MSC cells, with a bijective placement
of all data qubits and distinct MSC sites. There are no spare routing cells, hidden
buffers, ancillas, diagonal edges, or multi-cell cultivation footprints. Additional
footprint/configuration keys are rejected instead of being silently simulated.
Disconnected layouts are accepted and can produce a stalled schedule.

Logical qubit IDs, MSC IDs, physical coordinates, gate/request IDs, and produced
state IDs are separate. A SWAP updates placement only; gate operands never change.
Circuit input preserves per-qubit order and may add predecessors that occur earlier
in the input. Supported gates are H, S, X, Z, T, CX, and CZ; no commutation is assumed.

## Operation contracts

`OperationModel.specs` records durations, preconditions, footprints, and completion
effects. `Simulator.check` enforces the executable contract. Measurements are collected
directly by `RunMetrics`; there is no independent trace replay. Duration configuration is replaceable;
changing resource semantics requires a new model version and corresponding changes
to execution, measurement, oracle coverage, and resolved assumptions.

| Operation | Preconditions | Reserved resources | Completion effect |
| --- | --- | --- | --- |
| Data SWAP | Cardinally adjacent data patches | Both patches and contact | Exchange data, count one SWAP |
| Idle-patch exchange | Next step of the owned relocation walk, adjacent data | Both patches, contact, MSC | Move data to old idle patch; move idle MSC to data's old patch; count one SWAP |
| Initialize | Owned idle endpoint; entire evacuation walk completed | Site, MSC, transaction locks | Mark initialized; release transaction |
| Produce | Initialized MSC with one assigned T request | Site, MSC | Create exactly one output, mark output-occupied |
| Consume | Assigned output; actual T predecessors complete; adjacent target | Site, target patch, contact, MSC | Complete T; consume output once; return MSC site to idle |
| Single-qubit data gate | Actual predecessors complete | Current operand patch | Complete gate |
| Two-qubit data gate | Actual predecessors complete; adjacent data | Both patches and contact | Complete gate |

Idle-patch exchanges occur only within a declared next-production preparation
transaction; no standalone unassigned MSC relocation is part of v1. Data routing
uses data/data SWAPs; site preparation supplies the idle/data exchange actions.
Thus a final ordinary data gate cannot freely borrow an idle MSC as an additional
buffer. This is an explicit action-space restriction shared by policies and oracle.
A future broader movement model must version this assumption, not compare its
results as though the feasible schedule sets were unchanged.

Production requires fresh initialization for each output. Initialization,
production, and unconsumed output occupancy block movement of that MSC site.
Production completion does not release the site. Consumption preserves the data
qubit's current position and frees exactly the existing reusable production site.
Every output belongs to one MSC and one assigned request; no cancellation, restart,
duplication, discard, or output movement exists. All complete schedules consume all
produced states. Assignment may precede request readiness, but consumption cannot.

## Relocation transactions

`begin` assigns an unassigned, unfinished T request to an idle MSC and commits a
finite cardinal walk starting at that MSC. Returning to the original site and
revisiting cells are legal. Each step exchanges the owned idle patch with data.
When the walk finishes, initialization closes the transaction. Retention is a
one-cell walk and still requires initialization. There is no mandatory restoration.
A transaction may wait for resources; replanning cannot rewrite its path or request.

A destination cannot be another MSC's current site or another transaction's claimed
destination. Other transactions cannot move their idle patch through a claimed
destination until its transaction releases. Ordinary data operations may temporarily
use a destination under `serialized`/`space_time` until an operation actually needs
it; their resource reservations must finish before that operation can start.

| Mode | Transaction reservation |
| --- | --- |
| serialized | One global mutex from begin through initialization completion |
| disjoint_paths | Union of all patches of the complete walk for the full transaction |
| space_time | Per-operation patch/contact/MSC reservations only, plus destination ownership |

Complete-path reservations exclude ordinary data gates, SWAPs, production,
initialization, and consumption by other owners. A disjoint-path begin is rejected
if any path cell already has an incompatible active reservation. `space_time`
permits shared path interiors at different times; the same patch cannot be used by
two incompatible active operations. No mode holds a transaction lock during
cultivation, so independent production can overlap even in `serialized` mode.

## Time and failure

Time and durations are integer abstract units. Defaults are SWAP 1, initialize 1,
produce 3, consume 1, single-qubit gate 1, two-qubit gate 2. None is a measured
hardware duration. Configured durations must be strictly positive integers.

Reservations occupy `[start, end)`. At each timestamp, all completions are applied
in ascending operation ID before any starts. Effects become visible on completion;
a dependency successor may start at exactly its predecessor's completion time.
Non-conflicting starts at the same timestamp follow deterministic scheduler order.
A begin event has zero duration; it assigns ownership and locks the transaction.
The oracle may also record integer `tick` events for deliberate waiting.

No executable work and no pending event is a stall. Repeated states and configured
execution limits also stop execution with diagnostics. Deadlocks are neither
silently repaired by deleting resources nor claimed to prove global infeasibility.

## Deadline-free evaluation

Operation and resource semantics remain abstract-patch-v1. Performance-v2 removes
the circuit completion deadline and measures durations, makespan, and waiting in
abstract integer cycles. These are not calibrated physical code cycles. Search
and event limits constrain computation, not allowed circuit completion time.
The optional oracle alone has an explicit finite search horizon.
