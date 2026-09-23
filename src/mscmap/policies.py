"""Shared execution services and baseline / bounded joint planning decisions."""

from dataclasses import dataclass
from time import perf_counter

from .analysis import DemandWindow, demand_window
from .model import Action, Phase, adjacent
from .routing import Goal, data_route, site_paths
from .sim import Simulator


@dataclass(frozen=True)
class PolicySettings:
    window: int = 4
    lookahead_depth: int = 2
    beam_width: int = 3
    candidate_limit: int = 12
    path_limit: int = 8
    max_hops: int = 4
    route_states: int = 2000
    search_limit: int = 36
    event_limit: int = 1000
    no_site_changes: bool = False
    fixed_assignment: bool = False

    def __post_init__(self) -> None:
        for key, value in vars(self).items():
            if key in {"no_site_changes", "fixed_assignment"}:
                if type(value) is not bool:
                    raise ValueError(f"{key} must be boolean")
            elif type(value) is not int or value <= 0:
                raise ValueError(f"{key} must be a positive integer")


@dataclass(frozen=True)
class Plan:
    prefix: tuple[Action, ...] = ()
    feasible_completion: bool = False
    reason: str = ""


@dataclass(frozen=True)
class ScheduleOutcome:
    status: str
    reason: str


def dispatch(sim: Simulator, settings: PolicySettings) -> bool:
    """Start committed work and dependency-ready data/consumption work, deterministically."""
    committed_started = _dispatch_committed_work(sim)
    gates_started, routing_goals = _dispatch_ready_gates(sim)
    routing_started = _dispatch_data_route(sim, routing_goals, settings.route_states)
    return committed_started or gates_started or routing_started


def _dispatch_committed_work(sim: Simulator) -> bool:
    state = sim.state
    changed = False
    for msc_id, msc in sorted(state.mscs.items()):
        action = None
        if msc.phase == Phase.PREPARING:
            transaction = state.transactions[msc_id]
            action = (
                Action("swap", transaction.path[transaction.index : transaction.index + 2], msc_id)
                if transaction.index < len(transaction.path) - 1
                else Action("initialize", (msc.site,), msc_id)
            )
        elif msc.phase == Phase.INITIALIZED:
            action = Action("produce", (msc.site,), msc_id)
        if action is not None and sim.can_start(action):
            sim.start(action)
            changed = True
    return changed


def _dispatch_ready_gates(sim: Simulator) -> tuple[bool, list[Goal]]:
    state = sim.state
    changed = False
    routing_goals: list[Goal] = []
    for gate in sim.problem.circuit.gates:
        if (
            gate.id in state.completed
            or not gate.predecessors <= state.completed.keys()
            or any(operation.action.gate == gate.id for operation in state.active)
        ):
            continue
        operands = tuple(state.placement[q] for q in gate.qubits)
        if gate.kind == "T":
            msc_id = state.assignments.get(gate.id)
            if msc_id is None or state.mscs[msc_id].phase != Phase.OUTPUT:
                continue
            site = state.mscs[msc_id].site
            action = Action("consume", (site, operands[0]), msc_id, gate.id)
            if not adjacent(site, operands[0]):
                routing_goals.append(
                    lambda state, q=gate.qubits[0], p=site: adjacent(state.placement[q], p)
                )
        else:
            action = Action("single" if len(operands) == 1 else "two", operands, gate=gate.id)
            if len(operands) == 2 and not adjacent(*operands):
                routing_goals.append(
                    lambda state, qs=gate.qubits: adjacent(
                        state.placement[qs[0]], state.placement[qs[1]]
                    )
                )
        if sim.can_start(action):
            sim.start(action)
            changed = True
    return changed, routing_goals


def _dispatch_data_route(sim: Simulator, routing_goals: list[Goal], max_states: int) -> bool:
    # One route at a time prevents competing goals from undoing each other's SWAPs.
    if any(
        operation.action.kind == "swap" and operation.action.msc is None
        for operation in sim.state.active
    ):
        return False
    for goal in routing_goals:
        route = data_route(sim, goal, max_states)
        if route is not None and route.swaps:
            sim.start(route.swaps[0])
            return True
    return False


def candidates(
    sim: Simulator,
    window: DemandWindow,
    settings: PolicySettings,
    *,
    joint: bool,
    fixed_site: bool = False,
) -> list[Action]:
    idle = [msc_id for msc_id, msc in sorted(sim.state.mscs.items()) if msc.phase == Phase.IDLE]
    requests = list(window.requests if joint else window.requests[:1])
    ids = sorted(sim.state.mscs)
    order = [gate.id for gate in sim.problem.circuit.gates if gate.kind == "T"]
    retained, moved = [], []
    for request in requests:
        available = idle if joint else idle[:1]
        if settings.fixed_assignment:
            owner = ids[order.index(request.request) % len(ids)]
            available = [owner] if owner in idle else []
        for msc_id in available:
            paths = site_paths(sim, msc_id, settings.path_limit, settings.max_hops)
            for path in paths:
                if (fixed_site or settings.no_site_changes) and len(path) > 1:
                    continue
                action = Action("begin", msc=msc_id, gate=request.request, path=path)
                if sim.can_start(action):
                    (retained if len(path) == 1 else moved).append(action)
    # Retention survives candidate pruning for every considered request/MSC pair.
    return retained + moved[: max(0, settings.candidate_limit - len(retained))]


def finish_request(sim: Simulator, request: str, settings: PolicySettings) -> bool:
    for _ in range(settings.event_limit):
        if request in sim.state.completed:
            return True
        if dispatch(sim, settings):
            continue
        if not sim.advance():
            return False
    return request in sim.state.completed


class Baseline:
    def __init__(self, name: str, settings: PolicySettings):
        if name not in {"FixedSite", "NextRequestGreedy"}:
            raise ValueError(f"unknown baseline: {name}")
        self.name, self.settings = name, settings
        self.planning_seconds = 0.0
        self.dispatch_seconds = 0.0

    def propose(self, sim: Simulator, window: DemandWindow) -> Plan:
        start = perf_counter()
        try:
            options = candidates(
                sim, window, self.settings, joint=False, fixed_site=self.name == "FixedSite"
            )
            if not options:
                return Plan(reason="no legal assignment or site-preparation candidate")
            if self.name == "FixedSite":
                return Plan((options[0],))
            ranked = []
            for action in options:
                probe = sim.snapshot()
                probe.start(action)
                if not finish_request(probe, action.gate, self.settings):
                    continue
                swaps = probe.state.swaps - sim.state.swaps
                elapsed = probe.state.completed[action.gate] - sim.state.time
                score = self._score(elapsed, swaps)
                ranked.append((score, repr(action), action))
            if ranked:
                return Plan((min(ranked, key=lambda item: item[:2])[2],))
            # Future requests may need predecessors that are not assigned yet. A legal
            # retention remains a baseline choice; failure is reported by the driver.
            return Plan((options[0],), reason="immediate completion not found")
        finally:
            self.planning_seconds += perf_counter() - start

    def _score(self, elapsed: int, swaps: int) -> tuple[int, ...]:
        return elapsed, swaps


class _ImmediateSwapPlanner(Baseline):
    """Internal completion heuristic; not an independently selectable policy."""

    def __init__(self, settings: PolicySettings):
        super().__init__("NextRequestGreedy", settings)
        self.name = "internal_immediate_swap"

    def _score(self, elapsed: int, swaps: int) -> tuple[int, ...]:
        return (swaps,)


def drive(
    sim: Simulator, policy: "Baseline | LookaheadJoint", settings: PolicySettings
) -> ScheduleOutcome:
    seen: set[tuple] = set()
    for _ in range(settings.event_limit):
        if sim.complete:
            return ScheduleOutcome("completed", "")
        signature = sim.signature()
        if signature in seen:
            return ScheduleOutcome("stalled", "repeated execution state")
        seen.add(signature)
        dispatch_start = perf_counter()
        progressed = dispatch(sim, settings)
        policy.dispatch_seconds += perf_counter() - dispatch_start
        if progressed:
            continue
        window = demand_window(sim.problem, sim.state, settings.window)
        plan = policy.propose(sim, window)
        if plan.prefix:
            for action in plan.prefix:
                sim.start(action)
            continue
        if sim.advance():
            continue
        status = "feasible_plan_not_found" if isinstance(policy, LookaheadJoint) else "stalled"
        unfinished = [
            gate.id for gate in sim.problem.circuit.gates if gate.id not in sim.state.completed
        ]
        owners = ", ".join(
            f"{msc_id}:{msc.phase.value}@{msc.site},request={msc.request}"
            for msc_id, msc in sorted(sim.state.mscs.items())
        )
        reason = plan.reason or "no executable operation and no pending event"
        return ScheduleOutcome(status, f"{reason}; unfinished={unfinished}; MSCs=[{owners}]")
    if sim.complete:
        return ScheduleOutcome("completed", "")
    return ScheduleOutcome("search_limit", "execution decision/event limit reached")


@dataclass(frozen=True)
class _SearchPrefix:
    simulator: Simulator
    first_action: Action | None = None

    @property
    def rank(self) -> tuple[int, int, str, str]:
        state = self.simulator.state
        return state.swaps, state.time, repr(self.first_action), repr(self.simulator.signature())


@dataclass(frozen=True)
class _CompletedContinuation:
    first_action: Action | None
    simulator: Simulator

    @property
    def cost(self) -> tuple[int, int]:
        return self.simulator.state.swaps, self.simulator.state.time


class LookaheadJoint:
    """Implement JointScheduler by improving an internal immediate-SWAP schedule.

    Completed incumbents include waiting decisions. Replace only on a strict
    (total SWAPs, makespan) improvement; ties retain the existing schedule.
    """

    def __init__(self, settings: PolicySettings):
        self.settings = settings
        self._completion_planner = _ImmediateSwapPlanner(settings)
        self.fallback: Simulator | None = None
        self.planning_seconds = 0.0
        self.dispatch_seconds = 0.0
        self.rollouts = 0
        self.fallback_uses = 0
        self.baseline_status = "not_attempted"
        self.baseline_cost: tuple[int, int] | None = None
        self.accepted_costs: list[tuple[int, int]] = []

    def _continuation_action(self, sim: Simulator, full: Simulator) -> Action | None:
        prefix_length = len(sim.trace.events)
        if full.trace.events[:prefix_length] != sim.trace.events:
            return None
        if prefix_length < len(full.trace.events):
            event = full.trace.events[prefix_length]
            if event["event"] == "begin" and event["time"] == sim.state.time:
                return Action.from_dict(event["action"])
        return None

    def propose(self, sim: Simulator, window: DemandWindow) -> Plan:
        start = perf_counter()
        try:
            self._initialize_baseline(sim)
            continuations = self._preserved_continuation(sim)
            continuations.extend(self._search_continuations(sim))
            if not continuations:
                return Plan(reason="bounded search found no simulated full-circuit continuation")
            return self._select_continuation(continuations)
        finally:
            self.planning_seconds += perf_counter() - start

    def _initialize_baseline(self, sim: Simulator) -> None:
        if self.baseline_status != "not_attempted":
            return
        baseline = sim.snapshot()
        outcome = drive(baseline, self._completion_planner, self.settings)
        self.baseline_status = outcome.status
        if outcome.status == "completed":
            self.fallback = baseline
            self.baseline_cost = (baseline.state.swaps, baseline.state.time)
            self.accepted_costs.append(self.baseline_cost)

    def _preserved_continuation(self, sim: Simulator) -> list[_CompletedContinuation]:
        if self.fallback is None:
            return []
        prefix_length = len(sim.trace.events)
        if self.fallback.trace.events[:prefix_length] != sim.trace.events:
            raise RuntimeError("live execution diverged from the preserved schedule")
        action = self._continuation_action(sim, self.fallback)
        # No action preserves a wait, even when another request could be assigned now.
        return [_CompletedContinuation(action, self.fallback)]

    def _search_continuations(self, sim: Simulator) -> list[_CompletedContinuation]:
        continuations: list[_CompletedContinuation] = []
        frontier = [_SearchPrefix(sim)]
        evaluated = 0
        for _ in range(self.settings.lookahead_depth):
            children: list[_SearchPrefix] = []
            for prefix in frontier:
                node = prefix.simulator
                window = demand_window(node.problem, node.state, self.settings.window)
                for action in candidates(node, window, self.settings, joint=True):
                    if evaluated >= self.settings.search_limit:
                        break
                    child = self._start_search_prefix(prefix, action)
                    continuation = self._complete_prefix(child)
                    evaluated += 1
                    if continuation is not None:
                        continuations.append(continuation)
                    self._advance_to_assignment(child.simulator)
                    children.append(child)
            children.sort(key=lambda child: child.rank)
            frontier = children[: self.settings.beam_width]
            if not frontier or evaluated >= self.settings.search_limit:
                break
        self.rollouts += evaluated
        return continuations

    def _start_search_prefix(self, prefix: _SearchPrefix, action: Action) -> _SearchPrefix:
        probe = prefix.simulator.snapshot()
        probe.start(action)
        return _SearchPrefix(probe, prefix.first_action or action)

    def _complete_prefix(self, prefix: _SearchPrefix) -> _CompletedContinuation | None:
        full = prefix.simulator.snapshot()
        outcome = drive(full, self._completion_planner, self.settings)
        if outcome.status != "completed":
            return None
        return _CompletedContinuation(prefix.first_action, full)

    def _select_continuation(self, continuations: list[_CompletedContinuation]) -> Plan:
        # Stable min retains the incumbent on exact cost ties.
        winner = min(continuations, key=lambda continuation: continuation.cost)
        if winner.simulator is self.fallback:
            self.fallback_uses += 1
        else:
            self.accepted_costs.append(winner.cost)
        self.fallback = winner.simulator
        prefix = (winner.first_action,) if winner.first_action is not None else ()
        return Plan(prefix, True)

    def _advance_to_assignment(self, sim: Simulator) -> None:
        """Advance a snapshot to an assignment decision, preserving active and committed work."""
        for _ in range(self.settings.event_limit):
            if dispatch(sim, self.settings):
                continue
            window = demand_window(sim.problem, sim.state, self.settings.window)
            if candidates(sim, window, self.settings, joint=True) or not sim.advance():
                break


def make_policy(name: str, settings: PolicySettings) -> Baseline | LookaheadJoint:
    if name == "JointScheduler":
        return LookaheadJoint(settings)
    if name in {"LookaheadJoint", "ShortestPathRelocation"}:
        raise ValueError(f"policy {name} was replaced by JointScheduler; update the config")
    return Baseline(name, settings)
