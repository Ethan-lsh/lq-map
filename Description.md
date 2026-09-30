# mscmap research brief

This is the current research brief for the `mscmap` experiment framework.
`AGENTS.md` restates its essential constraints together with repository
conventions; inspect the current implementation and reuse components that
remain compatible with the research model.

## 1. Research objective

Implement **demand-aware logical-patch remapping through runtime SWAPs on a
fixed-orientation tiled architecture**. Change patch locations to establish the
local arrangements needed for Clifford gates and T-state extraction from a
magic-state patch. The execution model uses no lattice-surgery routing paths.

Use future Clifford operations and T-state demand to jointly determine initial
placement, idle MSC relocation, request assignment, and runtime data remapping.
Evaluate the effects of initial placement and runtime remapping on makespan,
area, and spacetime volume, including SWAP and scheduling overhead. Determine
when a limited reusable MSC pool improves utilization without excessive supply
delays. SWAP count is one cost metric, not the sole optimization objective.

## 2. Architecture and execution model

- Place data patches and `M` reusable MSC resources on an explicit tile grid.
  All logical patches have the same fixed boundary orientation; no rotation.
- Patch adjacency follows the grid: SWAPs (and moves into an empty data slot)
  exchange in-line neighboring slots (adjacent or separated by one junction tile)
  in the four cardinal directions (up, down, left, right) and reserve one
  auxiliary bus tile. Diagonal SWAPs are disallowed. Execution requires the
  auxiliary tile and movable participants; MSC sites and held outputs never
  move. This does not make arbitrary Clifford gates executable in every
  direction.
- Represent patch footprints and the local ancillas and inter-patch junctions
  needed by operations explicitly. Include them in space and time accounting.
  Legal cultivation sites do not create additional MSC resources.
- Do not allocate routing corridors or construct/reserve paths connecting
  distant operands. A SWAP sequence changes patch occupancy step by step;
  subsequent operations execute at the resulting local arrangement.
- For Clifford operations, T-state consumption, cultivation, and SWAP, specify
  the required relative placement, boundary contacts, auxiliary regions,
  occupied resources, and duration. Adjacency alone is insufficient for execution.
- Model SWAP as an abstract exchange with explicit reserved tiles, resource
  cost, and duration; label its physical protocol as unspecified. Track
  logical-qubit identities through each exchange so subsequent operations use
  the updated mapping. State the assumptions under which an MSC resource can
  operate at its destination (any empty cultivation slot; relocation transfers
  no state).
- Relocate MSC resources only while idle, before initialization/cultivation.
  Never move active cultivation. A produced state remains owned and occupies
  its patch until consumption; the MSC resource becomes reusable afterward.
- Schedule operations from actual DAG readiness and resource availability.
  Future-demand preparation may begin early, but consumption requires both a
  ready request and an available state.
- Use interval reservations for all occupied regions. Independent operations
  may overlap; conflicting operations must wait. Preserve ownership throughout
  movement, production, and consumption.

Use one shared legality and cost model for SWAP planning, policies, and simulation.
Record model assumptions in the resolved experiment metadata, and cite an
operation protocol only when it is verified in an available source. Keep
uncalibrated durations and areas in abstract units.

## 3. Mapping and scheduling

**Initial mapping:** Place data and MSC resources using future Clifford
interactions and T demand. Retain a supplied-placement baseline so initial
mapping and runtime remapping can be evaluated independently.

**Runtime remapping:** Search for legal patch arrangements for upcoming
operations. Jointly choose requests, idle MSC assignments, next production
locations, and SWAP sequences. Account for how displaced data affect later
Clifford operations and T-state access. Include retaining the current site and
waiting among the candidates. When the required arrangement is unavailable,
use legal SWAPs to establish it, then execute local lattice surgery with the
required boundary contacts, ancillas, and junctions. For T-state extraction,
bring the data and magic-state patches into the required local arrangement
before consumption; no remote state-transfer path is introduced.

Use bounded lookahead with configurable search limits. Evaluate candidates by
execution cost under a documented objective that accounts for SWAPs, local
operations, cultivation, and waiting. Commit legal actions and replan as the
schedule advances. Preserve existing reservations and active cultivation.

| Policy | Role |
| --- | --- |
| `FixedSite` | Keep MSC production locations fixed; remap data through legal SWAPs as required |
| `JointScheduler` | Jointly plan MSC assignment, relocation, and SWAP-induced data remapping using future demand |

All policies use SWAP-based remapping and the same local execution rules.
Report stalled or search-limited runs without claiming infeasibility or
introducing a remote-operation fallback.

## 4. Experiments

Separate the following comparisons:

1. **Policy quality:** Compare policies with identical circuits, initial
   placements, geometry, MSC count, and operation costs.
2. **Initial mapping:** Compare supplied and demand-aware placements under the
   same runtime policy.
3. **Mobility:** Compare fixed and movable MSC pools at the same pool size.
4. **Resource pooling:** Compare distributed fixed cultivation capacity with a
   smaller movable pool. Report differences in total area and capacity explicitly;
   use equal-area comparisons where feasible.

Vary T-demand locality and burstiness, Clifford interaction density, circuit
size, MSC pool size, cultivation latency and supply variability, relocation
cost, and available ancillary space. Include large circuits and high T demand,
as well as cases where extra relocation worsens execution time or waiting.
Define workload targets relative to the supplied placement; never regenerate the
circuit after movement.

Circuits come from seeded synthetic workloads or from explicit gate lists,
including OpenQASM files decomposed into Clifford+T by an optional Qiskit front
end. Qiskit only prepares the circuit; dependencies, placement, and scheduling
remain the framework's own.

Support deterministic reference runs and reproducible stochastic cultivation
experiments. Declare the supply model and retry costs; planning must not access
future realized outcomes. Separate stochastic samples from timing repetitions.

Use FlowRouter's spacetime/stochastic-cultivation ideas and RushHour's tile
geometry as design references only. RushHour is conceptual; the tile structure
and the logical-qubit placement need not follow it. Do not equate distributed
capacity with a dedicated patch beside every T gate, confuse backup injection
space with production capacity, or label a simplified comparator as an exact
reproduction of either paper.

## 5. Metrics and outputs

| Category | Measurements |
| --- | --- |
| Execution | Completion status, makespan, planning time |
| Movement | SWAP count, depth, implementation cost, and resource occupancy |
| Space | Total provisioned area and spacetime volume, including idle capacity, local ancillas, and junctions |
| MSC service | Resource utilization, relocation time, production/retry time, output holding, and request waiting |

Define units, accounting conventions, and utilization denominators explicitly
in the resolved experiment metadata.
Measure request waiting from DAG readiness to consumption start. Retain failed
and partial runs, but compare completed-run performance separately.

Keep the existing `run` CLI and one output folder containing `summary.csv` and
`raw_data.zip`. Store resolved configurations (supplied and resolved placements),
seeds, samples, per-run metrics, and operation traces in the raw data. Version
changed model/schema semantics; reject duplicate records and incompatible appends
rather than altering old data.

## 6. Implementation and verification

Keep responsibilities aligned with the existing architecture: circuit input
(including optional OpenQASM import) and demand analysis; geometry and
operation contracts; resource simulation; mapping, SWAP planning, and policies;
metrics and experiment exports.

Implement the geometry and operation rules first, then mapping/scheduling and
experiments. Verify boundary and ancilla constraints, SWAP-induced mapping
changes, idle-only MSC relocation, ownership, and resource conflicts using
small executable cases. Check that all policies realize nonlocal operand
arrangements through explicit SWAPs, without routing paths or remote-operation
fallbacks. Run representative comparisons and a larger workload
to check planning scalability.

Report changes, actual verification results, and unresolved modeling assumptions
concisely. A working simulator alone does not validate the research hypothesis.
