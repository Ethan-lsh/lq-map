"""Shared SWAP-based base rule, candidate generation, rollouts, and the two policies.

Every policy returns a `Plan` of actions to start at the current decision point.
All policies share `base_step`: a gate executes only through a local contact, and a
gate whose operands are not in contact starts the next SWAP/move of a fewest-exchange
plan. There is no routing path or remote-operation fallback. Policies differ in how
they choose MSC assignments, relocations, waiting, and extra remaps; candidate plans
are evaluated on planning snapshots only.
"""

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, fields
from itertools import chain, zip_longest
from time import perf_counter
from typing import Any

from ir import Gate
from model import RELOCATABLE_PHASES, Action, Phase, Problem, SystemState, Tile, contact_rule
from sim import Simulator
from swaps import (
    best_arrangement,
    exchange_action,
    exchange_options,
    exchange_tree,
    gate_distance,
    live_contact,
    movable_endpoints,
    relocation_action,
)


@dataclass(frozen=True)
class PolicySettings:
    window: int = 4
    site_limit: int = 3
    remap_limit: int = 4
    search_limit: int = 12
    rollout_gates: int = 0
    event_limit: int = 100000
    relocation: bool = True

    def __post_init__(self) -> None:
        for spec in fields(self):
            value = getattr(self, spec.name)
            if isinstance(spec.default, bool):
                if type(value) is not bool:
                    raise ValueError(f"settings.{spec.name} must be boolean")
            elif type(value) is not int or value < (0 if spec.name == "rollout_gates" else 1):
                raise ValueError(
                    f"settings.{spec.name} must be a positive integer "
                    "(rollout_gates may be 0, meaning the full remaining circuit)"
                )


@dataclass(frozen=True)
class Plan:
    actions: tuple[Action, ...] = ()


@dataclass(frozen=True)
class Candidate:
    """First-step decision: forced actions, then the base rule without `hold` MSCs."""

    label: str
    forced: tuple[Action, ...] = ()
    hold: frozenset[str] = frozenset()

    @property
    def kind(self) -> str:
        return self.label.split()[0]


StartAttempt = Callable[[Action | None], bool]
StopCondition = Callable[[Simulator], bool]


@dataclass(frozen=True)
class ScheduleOutcome:
    status: str
    reason: str


# Shared base rule ---------------------------------------------------------------


def idle_mscs(state: SystemState, hold: Iterable[str] = ()) -> list[str]:
    held = set(hold)
    return [
        m
        for m, msc in sorted(state.mscs.items())
        if msc.phase == Phase.IDLE and msc.site not in state.reserved and m not in held
    ]


def consume_distance(problem: Problem, state: SystemState, request: str, site: Tile) -> int:
    """Static exchanges before the request's data patch touches an output at `site`."""
    gate = problem.gates[request]
    return gate_distance(problem, gate, (state.placement[gate.qubits[0]], site))


def assignable_requests(problem: Problem, state: SystemState, limit: int) -> list[str]:
    """Up to `limit` unassigned requests, in demand order, with no unassigned T ancestor.

    Assigning a request ahead of its unassigned T ancestors can fill the pool with
    held outputs that wait on a request no MSC can serve.
    """
    pending, window = 0, []
    for request in state.unassigned:
        if len(window) == limit:
            break
        if not problem.t_ancestors[request] & pending:
            window.append(request)
        pending |= problem.request_bits[request]
    return window


def _assign_requests(sim: Simulator, hold: Iterable[str], attempt: StartAttempt) -> None:
    """Earliest demands first, each to the idle MSC its data can reach in fewest SWAPs."""
    state, problem = sim.state, sim.problem
    idle = idle_mscs(state, hold)
    for request in assignable_requests(problem, state, len(idle)):
        msc = min(
            idle, key=lambda m: (consume_distance(problem, state, request, state.mscs[m].site), m)
        )
        attempt(Action("assign", msc=msc, gate=request))
        idle.remove(msc)


def endpoints(state: SystemState, gate: Gate) -> tuple[Tile, ...] | None:
    """Slots of the gate's patches: operands, plus the output site for a T request;
    None for a T without a supplying MSC."""
    tiles = tuple(state.placement[q] for q in gate.qubits)
    if not gate.is_magic:
        return tiles
    msc = state.assignment(gate.id)
    return None if msc is None else (tiles[0], state.mscs[msc].site)


def gate_action(sim: Simulator, gate: Gate) -> Action | None:
    """The gate's action if its patches are in local contact with free tiles now."""
    state, problem = sim.state, sim.problem
    kind, tiles = problem.gate_kind(gate), endpoints(state, gate)
    if tiles is None:
        return None
    msc = state.assignment(gate.id) if kind == "consume" else None
    if msc is not None and state.mscs[msc].phase != Phase.OUTPUT:
        return None
    contact = None
    if contact_rule(kind, gate.kind) is not None:
        contact = live_contact(problem, state, kind, tiles, gate)
        if contact is None:
            return None
    return Action(kind, tiles, contact, msc, gate.id)


def arrange(sim: Simulator, gate: Gate, protected: Iterable[Tile] = ()) -> Action | None:
    """Next SWAP/move of the fewest-exchange plan that brings the gate's patches into
    local contact (either or both operands may move; a T's output stays). MSC sites and
    `protected` data never move. None if the patches already touch, an operand is busy,
    or no plan exists."""
    state, problem = sim.state, sim.problem
    tiles = endpoints(state, gate)
    if tiles is None or len(tiles) < 2:
        return None
    if any(t in state.reserved for t in tiles[: len(gate.qubits)]):
        return None  # an operand is busy; replan at a later decision point
    fixed = {m.site for m in state.mscs.values()} | set(protected)
    movable = movable_endpoints(gate)
    trees = [
        exchange_tree(problem.geometry, t, fixed | {tiles[1 - i]})
        if i in movable
        else ({t: None}, {t: 0})
        for i, t in enumerate(tiles)
    ]
    rule = contact_rule(problem.gate_kind(gate), gate.kind)
    best = best_arrangement(problem.geometry, rule, (trees[0][1], trees[1][1]))
    if best is None or best[0] == 0:
        return None
    moving = 0 if trees[0][1][best[1]] > 0 else 1
    step = _first_exchange(trees[moving][0], tiles[moving], best[1 + moving])
    return exchange_action(problem, state, tiles[moving], step)


def _first_exchange(parents: dict[Tile, Tile | None], origin: Tile, target: Tile) -> Tile:
    """The slot one exchange from `origin` on the tree path toward `target`."""
    step = target
    while parents[step] != origin:
        step = parents[step]
    return step


def base_step(sim: Simulator, hold: Iterable[str] = ()) -> list[Action]:
    """One decision point, applied in place: assign requests, then give each ready gate
    (input order) its local action or the next exchange of its SWAP plan. Operands of
    earlier ready gates are protected from later gates' plans. Returns the actions."""
    started: list[Action] = []

    def attempt(action: Action | None) -> bool:
        if action is None or not sim.can_start(action):
            return False
        sim.start(action)
        started.append(action)
        return True

    _assign_requests(sim, hold, attempt)
    state, problem = sim.state, sim.problem
    protected: set[Tile] = set()
    for gate_id in sorted(state.ready, key=problem.gate_index.__getitem__):
        gate = problem.gates[gate_id]
        if not attempt(gate_action(sim, gate)):
            attempt(arrange(sim, gate, protected))
        if endpoints(state, gate) is not None:  # a T without an MSC cannot act yet
            protected.update(state.placement[q] for q in gate.qubits)
    return started


def start_lifecycle(sim: Simulator) -> None:
    """Committed production work: initialize assigned MSCs, cultivate initialized ones."""
    for msc_id, msc in sorted(sim.state.mscs.items()):
        kind = {Phase.ASSIGNED: "initialize", Phase.INITIALIZED: "cultivate"}.get(msc.phase)
        if kind is not None:
            action = Action(kind, (msc.site,), msc=msc_id)
            if sim.can_start(action):
                sim.start(action)


# Candidate generation -----------------------------------------------------------


def assignment_candidates(
    sim: Simulator, requests: Iterable[str], settings: PolicySettings
) -> tuple[list[Candidate], list[Candidate]]:
    """(retain, relocate) candidates for request/idle-MSC pairs."""
    state = sim.state
    retain, relocate = [], []
    for request in requests:
        for msc in idle_mscs(state):
            assign = Action("assign", msc=msc, gate=request)
            retain.append(Candidate(f"assign {request}->{msc}", (assign,)))
            if not settings.relocation:
                continue
            for site in _candidate_sites(sim, request, msc, settings.site_limit):
                move = relocation_action(state, msc, site)
                relocate.append(
                    Candidate(f"relocate {msc}->{site}; assign {request}", (move, assign))
                )
    return retain, relocate


def _candidate_sites(sim: Simulator, request: str, msc: str, limit: int) -> list[Tile]:
    """Empty C slots that the request's data reaches in strictly fewer SWAPs, best first."""
    state, problem = sim.state, sim.problem
    current = consume_distance(problem, state, request, state.mscs[msc].site)
    ranked = sorted(
        (consume_distance(problem, state, request, site), site)
        for site in problem.geometry.cultivation_sites
        if state.msc_at(site) is None and site not in state.data_at
    )
    better = [site for distance, site in ranked if distance < current]
    return [s for s in better if sim.can_start(relocation_action(state, msc, s))][:limit]


def remap_candidates(
    sim: Simulator, requests: Iterable[str], settings: PolicySettings
) -> list[Candidate]:
    """Single SWAP/moves that shorten an upcoming operation's SWAP distance, including
    gates that are not ready yet (pre-positioning from future demand)."""
    state, problem = sim.state, sim.problem
    options: dict[Action, tuple[int, str]] = {}
    for gate in _upcoming_gates(sim, requests, settings.window):
        before = gate_distance(problem, gate, endpoints(state, gate))
        for moving in movable_endpoints(gate):
            for action in exchange_options(problem, state, gate.qubits[moving]):
                if not sim.can_start(action):
                    continue
                after = gate_distance(problem, gate, _moved_endpoints(state, gate, action))
                if after < before:
                    score = (after - before, gate.id)
                    options[action] = min(options.get(action, score), score)
    ranked = sorted(options, key=lambda a: (options[a], repr(a)))[: settings.remap_limit]
    return [Candidate(f"remap {a.kind} {a.tiles} for {options[a][1]}", (a,)) for a in ranked]


def _upcoming_gates(sim: Simulator, requests: Iterable[str], limit: int) -> list[Gate]:
    """The first `limit` unstarted two-qubit gates plus T requests with a supplying MSC."""
    state, problem = sim.state, sim.problem
    started = set(state.completed) | {op.action.gate for op in state.active}
    gates = [g for g in problem.circuit.gates if len(g.qubits) == 2 and g.id not in started][:limit]
    pending_t = chain(sorted(m.request for m in state.mscs.values() if m.request), requests)
    gates.extend(
        problem.gates[r]
        for r in dict.fromkeys(pending_t)
        if r not in started and state.assignment(r) is not None
    )
    return gates


def _moved_endpoints(state: SystemState, gate: Gate, action: Action) -> tuple[Tile, ...]:
    """The gate's endpoints after an exchange of `action.tiles` completes."""
    source, destination = action.tiles
    swapped = {source: destination, destination: source}
    tiles = endpoints(state, gate)
    moved = tuple(swapped.get(t, t) for t in tiles[: len(gate.qubits)])
    return moved + tiles[len(gate.qubits) :]


def interleave(*groups: list[Candidate]) -> list[Candidate]:
    return [c for c in chain.from_iterable(zip_longest(*groups)) if c is not None]


# Rollouts -----------------------------------------------------------------------


@dataclass
class RolloutResult:
    cost: float
    first_step: tuple[Action, ...]


def rollout(
    sim: Simulator,
    candidate: Candidate,
    settings: PolicySettings,
    stop: StopCondition,
) -> RolloutResult | None:
    """Apply the candidate on a planning snapshot, continue with the base rule until
    `stop` (or completion), and return the objective there; None if it stalls."""
    probe = sim.snapshot()
    for action in candidate.forced:
        if not probe.can_start(action):
            return None
        probe.start(action)
    first_step = candidate.forced + tuple(base_step(probe, candidate.hold))
    for _ in range(settings.event_limit):
        start_lifecycle(probe)
        if probe.complete or stop(probe):
            cost = probe.metrics.objective(probe.problem.objective, probe.state.time)
            return RolloutResult(cost, first_step)
        if not probe.advance():
            return None
        base_step(probe)
    return None


def horizon_stop(sim: Simulator, settings: PolicySettings) -> StopCondition:
    """Stop once the first `rollout_gates` unfinished gates (input order) complete."""
    if settings.rollout_gates == 0:
        return lambda probe: False
    unfinished = [g.id for g in sim.problem.circuit.gates if g.id not in sim.state.completed]
    targets = unfinished[: settings.rollout_gates]
    return lambda probe: all(g in probe.state.completed for g in targets)


# Policies -----------------------------------------------------------------------


@dataclass
class Policy:
    settings: PolicySettings
    planning_seconds: float = 0.0
    rollouts: int = 0
    decisions: int = 0
    labels: Counter[str] = field(default_factory=Counter)

    def propose(self, sim: Simulator) -> Plan:
        started = perf_counter()
        try:
            return self._propose(sim)
        finally:
            self.planning_seconds += perf_counter() - started

    def _propose(self, sim: Simulator) -> Plan:
        return Plan(tuple(base_step(sim.snapshot())))

    def diagnostics(self) -> dict[str, Any]:
        return {
            "rollouts": self.rollouts,
            "rollout_decisions": self.decisions,
            "chosen_candidate_kinds": dict(sorted(self.labels.items())),
        }


class FixedSite(Policy):
    """Fixed MSC sites; the base rule SWAPs data into contact as gates become ready."""


class JointScheduler(Policy):
    """Window planning of assignments, waiting, relocation, and extra SWAP remaps,
    ranked by the objective of a base-rule rollout of the remaining (or bounded)
    circuit."""

    def _propose(self, sim: Simulator) -> Plan:
        state = sim.state
        window = assignable_requests(sim.problem, state, self.settings.window)
        retain, relocate = assignment_candidates(sim, window, self.settings)
        hold = [Candidate(f"wait {m}", hold=frozenset({m})) for m in idle_mscs(state)]
        idle_moves = self._idle_relocations(sim, window)
        remaps = remap_candidates(sim, window, self.settings)
        candidates = [Candidate("default"), *interleave(retain, relocate, remaps, hold, idle_moves)]
        return self._choose(sim, candidates, horizon_stop(sim, self.settings))

    def _choose(self, sim: Simulator, candidates: list[Candidate], stop: StopCondition) -> Plan:
        """Lowest rollout cost; the first candidate (the default) wins exact ties."""
        candidates = candidates[: self.settings.search_limit]
        if len(candidates) == 1:
            return Plan(tuple(base_step(sim.snapshot(), candidates[0].hold)))
        self.decisions += 1
        best: tuple[Candidate, RolloutResult] | None = None
        for candidate in candidates:
            result = rollout(sim, candidate, self.settings, stop)
            self.rollouts += 1
            if result is not None and (best is None or result.cost < best[1].cost):
                best = (candidate, result)
        if best is None:
            return Plan(tuple(base_step(sim.snapshot())))
        self.labels[best[0].kind] += 1
        return Plan(best[1].first_step)

    def _idle_relocations(self, sim: Simulator, window: list[str]) -> list[Candidate]:
        """Move an idle or assigned-but-uninitialized MSC toward upcoming demand."""
        if not self.settings.relocation or not window:
            return []
        state = sim.state
        options = []
        for msc_id, msc in sorted(state.mscs.items()):
            if msc.phase not in RELOCATABLE_PHASES or msc.site in state.reserved:
                continue
            request = msc.request or window[0]
            hold = frozenset({msc_id}) if msc.phase == Phase.IDLE else frozenset()
            for site in _candidate_sites(sim, request, msc_id, self.settings.site_limit):
                move = relocation_action(state, msc_id, site)
                options.append(Candidate(f"premove {msc_id}->{site}", (move,), hold))
        return options


POLICIES = {p.__name__: p for p in (FixedSite, JointScheduler)}
RETIRED_POLICIES = {
    "LookaheadJoint": "was replaced by JointScheduler; update the config",
    "ShortestPathRelocation": "was replaced by JointScheduler; update the config",
    "NextRequestGreedy": "was removed; use FixedSite or JointScheduler",
}


def make_policy(name: str, settings: PolicySettings) -> Policy:
    if name in RETIRED_POLICIES:
        raise ValueError(f"policy {name} {RETIRED_POLICIES[name]}")
    if name not in POLICIES:
        raise ValueError(f"unknown policy {name!r}; choose one of {', '.join(POLICIES)}")
    return POLICIES[name](settings)


def drive(sim: Simulator, policy: Policy, settings: PolicySettings) -> ScheduleOutcome:
    """Decision point per completion time: policy plan, committed lifecycle, advance."""
    for _ in range(settings.event_limit):
        if sim.complete:
            return ScheduleOutcome("completed", "")
        for action in policy.propose(sim).actions:
            sim.start(action)
        start_lifecycle(sim)
        if sim.complete:
            return ScheduleOutcome("completed", "")
        if not sim.advance():
            return ScheduleOutcome("stalled", _stall_reason(sim))
    return ScheduleOutcome("search_limit", "event_limit reached before completion")


def _stall_reason(sim: Simulator) -> str:
    state = sim.state
    ready = sorted(state.ready, key=sim.problem.gate_index.__getitem__)[:8]
    owners = ", ".join(
        f"{m}:{msc.phase.value}@{list(msc.site)} request={msc.request}"
        for m, msc in sorted(state.mscs.items())
    )
    return (
        f"no startable operation and no pending event; ready={ready}; "
        f"unassigned={len(state.unassigned)}; MSCs=[{owners}]"
    )
