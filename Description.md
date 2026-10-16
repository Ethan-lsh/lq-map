# mscmap research brief

This is the current research brief for the `mscmap` experiment framework.
`AGENTS.md` restates its essential constraints together with repository
conventions; inspect the current implementation and reuse components that
remain compatible with the research model. Revised 2026-10-16 to reflect the
architecture-level comparison (`compare --layouts brick`, resource metrics, and
figures); section 8 lists the points that are still open.

## 1. Research objective

Implement **demand-aware logical-patch remapping through runtime SWAPs on a
fixed-orientation tiled architecture**. Change patch locations to establish the
local arrangements needed for Clifford gates and T-state extraction from a
magic-state patch. The execution model uses no lattice-surgery routing paths.

Use future Clifford operations and T-state demand to jointly determine initial
placement, idle MSC relocation, request assignment, and runtime data remapping.

The evaluation is an **architecture-level comparison** against an external
lattice-surgery scheduler (PureMagic): the routing-free, SWAP-based architecture
with a small movable MSC pool versus bus-routed lattice surgery with fixed magic
nodes. Evaluate execution time (`execution_lcycles`: lcycles until the last
operation finishes), area, spacetime volume, and resource utilization, including
SWAP and scheduling overhead, both at equal area and at the area each
architecture needs. Determine when a limited reusable MSC pool improves
utilization without excessive supply delays. SWAP count is one cost metric, not
the sole optimization objective.

The thesis to test is not "faster scheduling" but **area efficiency**: the same
magic resources and supply model on a fraction of the area, with a smaller
spacetime volume and higher utilization, at an execution-time cost that stays
bounded.

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

### Layouts

Two layouts of the same architecture are evaluated:

- **Matched layout.** PureMagic's default bus-routing topology for the qubit
  count, with its magic nodes reduced to the MSC count: magic nodes become `C`
  slots, data nodes `S` slots holding the same logical qubits, and every other
  node a `B` tile in the brick pattern or an empty `S` slot. Both models then
  share one grid and area N; this is the equal-area setting and, since there is
  no spare `C` slot, MSC sites stay fixed there.
- **Brick layout.** mscmap's own compact layout (`config.layouts.brick_geometry`):
  data slots in ⌈√qubits⌉ rows, slots adjacent along a row and one junction tile
  apart otherwise, every `B` tile bordering slots on both axes (L-shaped CX
  contacts anywhere), and the outer rows holding `C` slots so idle MSCs can
  relocate toward demand. The same MSC count and supply model apply. This is the
  area the architecture actually needs; comparing it with PureMagic's own
  topology is the area-time view.

## 3. Mapping and scheduling

**Initial mapping:** Place data and MSC resources using future Clifford
interactions and T demand (`demand_aware`). Retain a supplied-placement baseline
in `run` configurations so initial mapping and runtime remapping stay separable,
but the experiment commands evaluate the proposed method only.

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
| `JointScheduler` | The proposed method: jointly plan MSC assignment, idle relocation, and SWAP-induced data remapping using future demand (`demand_aware` mapping, `relocation: true`) |
| `FixedSite` | The base rule alone with fixed MSC production locations; a `run` option kept for hand-checkable cases, not part of any experiment command |

All policies use SWAP-based remapping and the same local execution rules.
Report stalled or search-limited runs without claiming infeasibility or
introducing a remote-operation fallback.

## 4. Experiments

The experiment is the comparison of the proposed method with PureMagic
(`compare`), run from its released binaries at a recorded commit, under matched
constraints:

- the same Clifford+T gate list (converted once by Qiskit; mscmap's resolved
  list, Pauli frame included, is handed to PureMagic as OpenQASM so T counts match);
- the same number of magic-state resources and the same exponential cultivation
  law (rate λ per lcycle; `--samples N` runs mscmap samples 0..N−1 and PureMagic
  seeds 1..N, same distribution but different random streams);
- the same lcycle accounting for executed gates (CX 2, S/SDG 3, T 1) and no
  T-injection failures;
- the matched layout (equal N) always, and with `--layouts brick` the brick
  layout as a second mscmap model (`mscmap/JointScheduler@brick`).

There is no internal ablation (no policy-quality, initial-mapping, mobility, or
resource-pooling comparison between variants). `benchmark` records the proposed
method alone on brick layouts. Circuits come from `data/<small|medium|large>/*.qasm`;
circuits whose Clifford+T synthesis reaches 10⁴–10⁵ gates are excluded by size.

**Remaining model differences are reported, not normalized.** They bound what the
comparison can claim:

1. *Executed circuit shape.* PureMagic's transpiler absorbs every Clifford after
   the last T into measurement products and absorbs earlier Cliffords whenever the
   conjugated Pauli stays weight 1; it expands H into three 3-lcycle products.
   T-free and Clifford-heavy circuits therefore favor PureMagic (it executes almost
   nothing), T-dense circuits favor mscmap (its dependency bound is about half).
   Report each model's own dependency lower bound (`critical_path_lcycles`) next to L.
2. *Distance cost semantics.* PureMagic routes Steiner trees through bus nodes in
   one lcycle per product (distance costs parallelism, not latency); mscmap pays
   `costs.exchange` lcycles per SWAP step (distance costs latency). This is the
   difference under study.
3. *Measurement.* PureMagic schedules multi-patch measurement products; mscmap
   removes final measurements.
4. *Random streams.* Cultivation samples differ between models and PureMagic's
   seeds are correlated; small circuits are supply-dominated, so use ≥10 samples
   and report the realized cultivation time alongside L.
5. *PureMagic transpile drops CZ and SWAP in a width-limited flush* (observed on
   its binary; see section 8). Results for circuits with CZ are provisional until
   that is handled.
6. *Topology.* PureMagic's default topology generator provisions the bus; its
   minimum-area layout was not searched. Area claims are relative to that default.

Use FlowRouter's spacetime/stochastic-cultivation ideas and RushHour's tile
geometry as design references only. RushHour is conceptual; the tile structure
and the logical-qubit placement need not follow it. Do not equate distributed
capacity with a dedicated patch beside every T gate, confuse backup injection
space with production capacity, or label a simplified comparator as an exact
reproduction of either paper.

Support deterministic reference runs and reproducible stochastic cultivation
experiments. Declare the supply model and retry costs; planning must not access
future realized outcomes. Separate stochastic samples from timing repetitions.

## 5. Metrics and outputs

Per-run measurements (mscmap `summary.csv` / `raw_data.zip`):

| Category | Measurements |
| --- | --- |
| Execution | Completion status, `execution_lcycles`, planning time |
| Movement | SWAP count, depth, implementation cost, and resource occupancy |
| Space | Total provisioned area and spacetime volume, including idle capacity, local ancillas, and junctions; `active_series` per lcycle by layer (data, aux, cultivating, holding, consume) |
| MSC service | Resource utilization, relocation time, production/retry time, output holding, and request waiting |

Architecture metrics of the comparison (`comparison.csv`, medians in
`comparison_median.csv`), computed identically for both models and labeled as
first-order estimates:

| Metric | Definition |
| --- | --- |
| Area N, `tiles_per_logical_qubit` | Provisioned tiles; N over logical qubits (footprint overhead) |
| `physical_qubits` | N × (2d² − 1): one rotated surface-code patch of distance d (`timing.code_distance`) per tile. Tiles carry no patch or ancilla geometry, so this is an estimate shared by both models |
| `execution_us`, `physical_volume_qubit_us` | L × lcycle_us; physical qubits × execution time |
| Spacetime volume | N × L in tile·lcycle (and tile·µs); reported absolute and relative to PureMagic |
| `active_volume`, `active_utilization` | Cell-lcycles engaged by operations; their share of N × L |
| `t_throughput` | T gates per lcycle |
| `critical_path_lcycles`, `depth_overhead` | Dependency-only lower bound of L for the operations that model executes (mscmap: gate DAG with its costs; PureMagic: `.trans` products chained per qubit, T/M 1, CX 2, S-type 3); L over that bound |
| `magic_utilization`, `holding_fraction` | (cultivating + holding + consume) and holding cell-lcycles over magic resources × L |
| `aux_share` | Aux cell-lcycles (ancilla/junction tiles, or routed bus nodes) over active cell-lcycles: the communication share |
| `mean_t_wait` | mscmap only: request wait from DAG readiness to consumption start per T |

Define units, accounting conventions, and utilization denominators explicitly
in the resolved experiment metadata and the comparison manifest. Measure request
waiting from DAG readiness to consumption start. Retain failed and partial runs,
but compare completed-run performance separately.

Figures (`plot` on a `compare` output) visualize recorded values only: per-circuit
active-cell layers, the overview of total fraction, and the resource figures
(physical qubits, active volume, the area-time plane with iso-volume guides and
the volume relative to PureMagic split into active and idle, and one column chart
each for area, relative spacetime volume, and active utilization).

Keep the `run`, `benchmark`, `compare`, and `plot` CLI and one output folder
layout. Store resolved configurations (supplied and resolved placements), seeds,
samples, per-run metrics, and operation traces in the raw data. Version changed
model/schema semantics; reject duplicate records and incompatible appends rather
than altering old data.

## 6. Implementation and verification

Keep responsibilities aligned with the existing architecture: circuit input
(including optional OpenQASM import) and demand analysis; geometry and
operation contracts; resource simulation; mapping, SWAP planning, and policies;
metrics and experiment exports; comparison and figures in `analysis`.

Verify boundary and ancilla constraints, SWAP-induced mapping changes, idle-only
MSC relocation, ownership, and resource conflicts using small executable cases.
Check that all policies realize nonlocal operand arrangements through explicit
SWAPs, without routing paths or remote-operation fallbacks. For the comparison,
check that both models received the same gate list and magic count, that the
PureMagic stats hook was present (otherwise its magic and aux shares are blank),
and that every row's status is reported.

Report changes, actual verification results, and unresolved modeling assumptions
concisely. A working simulator alone does not validate the research hypothesis.

## 7. Current evidence (observed software behavior, not general superiority)

Nine circuits (five small; bb84_n8, bv_n14, simon_n6, seca_n11), MSC count 2, ten
samples, PureMagic commit f990c3e with the stats hook. For the four circuits with
at least one T whose comparison is not dominated by Clifford absorption
(toffoli_n3, teleportation_n3, simon_n6, seca_n11), medians of mscmap on the brick
layout relative to PureMagic:

| Metric | Range |
| --- | --- |
| Area N | 0.44–0.57× |
| `execution_lcycles` | 1.03–1.51× |
| Spacetime volume, physical qubit·µs | 0.59–0.67× |
| Active utilization | 9–15% vs 4.5–6.6% |
| `aux_share` | 0.16–0.22 vs 0.12–0.31 (lower for the larger circuits) |
| `t_throughput` | 0.66–1.01× |
| `depth_overhead` | 6.4–14.2 vs 3.3–5.0 (bounds differ by model) |

On the matched layout mscmap is 1.2–1.66× in both L and volume. T-free circuits
and qec_en_n5 (one T, 22 Cliffords) are PureMagic-favorable by construction
(section 4, difference 1). Earlier trace analysis attributes mscmap's time gap
mainly to produced states waiting for their statically assigned request
(holding fraction 0.20–0.33) and the SWAP round trips of data to the MSC, not to
the executed circuit shape.

## 8. Open items for discussion

1. **Claim scope.** Is the paper's claim "area efficiency at bounded time cost"
   (section 1), or must execution time reach parity? The latter needs supply-side
   changes (near-data cultivation, chain- or readiness-aware assignment, or a larger
   pool in the saved area), which were explored only as uncommitted experiments.
2. **Fairness normalization.** Should Cliffords after the last T be removed for
   both models (equivalent to PureMagic's measurement absorption), and should CZ be
   decomposed into H·CX·H before PureMagic to avoid its flush drop? Both change the
   executed circuits and must be reported as such.
3. **Equal-area pooling.** Should the brick layout be compared at PureMagic's area
   with the saved tiles spent on more MSCs (resource pooling, section 1), and how is
   the pool size chosen?
4. **PureMagic minimum area.** Should its topology be shrunk (fewer bus nodes) to
   test whether the area advantage survives a denser baseline?
5. **Physical-qubit estimate.** Is one distance-d patch per tile acceptable for both
   models, or should bus/junction tiles be costed differently?
6. **Statistics.** Ten samples per circuit; PureMagic's seeds are correlated. How
   many samples, and should supply-neutral (deterministic cultivation) references
   be part of the reported protocol?
7. **Circuit set.** ising/qft (10⁴–10⁵ gates after synthesis) are excluded by cost;
   which large circuits should the final evaluation include?
