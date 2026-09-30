"""Initial placement of data patches and MSC resources.

`supplied` keeps the configured placement. `demand_aware` is a deterministic local
search heuristic (not an optimal placement): starting from the supplied placement,
it applies improving exchanges/moves of data patches and MSC resources under

    cost = sum_2q count * static_swap_distance(operands)
         + sum_q T_count(q) * min_msc static_swap_distance(q, msc)

where a static SWAP distance is the fewest exchanges that bring the patches into
local contact, ignoring other patches; unreachable pairs use the ranking penalty.
"""

from collections import defaultdict

from analysis import interaction_demand
from ir import CircuitDAG
from model import ContactRule, Geometry, Tile, contact_rule
from swaps import pair_distance

MAPPINGS = ("supplied", "demand_aware")
MAPPING_PASSES = 4
Placement = dict[str, Tile]  # data qubit or MSC ID -> slot


class _PlacementCost:
    def __init__(self, circuit: CircuitDAG, geometry: Geometry):
        self.geometry = geometry
        pairs, self.t_demand = interaction_demand(circuit)
        self.pairs_of: dict[str, list[tuple[ContactRule, str, str, int]]] = defaultdict(list)
        for (kind, first, second), count in sorted(pairs.items()):
            rule = contact_rule("two", kind)
            for qubit in (first, second):
                self.pairs_of[qubit].append((rule, first, second, count))
        self.consume_rule = contact_rule("consume")

    def distance(
        self, first: Tile, second: Tile, rule: ContactRule, movable: tuple[int, ...] = (0, 1)
    ) -> int:
        return pair_distance(self.geometry, rule, (first, second), movable)

    def touching(self, qubits: set[str], place: dict[str, Tile], sites: dict[str, Tile]) -> int:
        """Cost terms touching `qubits`, each pair counted once."""
        seen, total = set(), 0
        for qubit in sorted(qubits):
            for term in self.pairs_of[qubit]:
                if term not in seen:
                    seen.add(term)
                    rule, first, second, count = term
                    total += count * self.distance(place[first], place[second], rule)
            if self.t_demand[qubit]:
                nearest = min(
                    self.distance(place[qubit], s, self.consume_rule, (0,)) for s in sites.values()
                )
                total += self.t_demand[qubit] * nearest
        return total


def initial_placement(
    strategy: str,
    circuit: CircuitDAG,
    geometry: Geometry,
    placement: Placement,
    sites: Placement,
) -> tuple[Placement, Placement]:
    if strategy not in MAPPINGS:
        raise ValueError(f"mapping must be one of {', '.join(MAPPINGS)}")
    placement, sites = dict(placement), dict(sites)
    if strategy == "supplied":
        return placement, sites
    cost = _PlacementCost(circuit, geometry)
    for _ in range(MAPPING_PASSES):
        improved = _improve_data(cost, geometry, placement, sites)
        improved |= _improve_sites(cost, geometry, placement, sites)
        if not improved:
            break
    return placement, sites


def _improve_data(
    cost: _PlacementCost, geometry: Geometry, placement: Placement, sites: Placement
) -> bool:
    """First-improvement pass: each data patch tries every slot not hosting an MSC."""
    improved = False
    free_slots = sorted(geometry.slots - set(sites.values()))
    for qubit in sorted(placement):
        for slot in free_slots:
            origin = placement[qubit]
            if slot == origin:
                continue
            occupant = _data_at(placement, slot)
            touched = {qubit} | ({occupant} if occupant else set())
            trial = {**placement, qubit: slot, **({occupant: origin} if occupant else {})}
            if cost.touching(touched, trial, sites) < cost.touching(touched, placement, sites):
                placement.update(trial)
                improved = True
    return improved


def _data_at(placement: dict[str, Tile], slot: Tile) -> str | None:
    return next((q for q, t in placement.items() if t == slot), None)


def _improve_sites(
    cost: _PlacementCost, geometry: Geometry, placement: Placement, sites: Placement
) -> bool:
    """Each MSC tries every C slot; displaced data takes the MSC's former slot (initial
    placement only; at runtime relocation needs an empty C slot)."""
    improved = False
    everyone = set(placement)
    for msc in sorted(sites):
        for site in sorted(geometry.cultivation_sites - set(sites.values())):
            occupant = _data_at(placement, site)
            trial_sites = {**sites, msc: site}
            trial = {**placement, **({occupant: sites[msc]} if occupant else {})}
            before = cost.touching(everyone, placement, sites)
            if cost.touching(everyone, trial, trial_sites) < before:
                placement.update(trial)
                sites.update(trial_sites)
                improved = True
    return improved
