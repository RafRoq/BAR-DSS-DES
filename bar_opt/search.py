"""Beam search over build decisions.

A decision is taken whenever a builder is idle: start a unit now, start it once the
economy can pay for it without stalling (wait, then decide again), assist a running
job, or wait for the next event. Idle builders decide one at a time in id order, which
removes permutations of the same set of decisions.

Each node is scored f = cost so far (time + weighted penalties) + an estimate of the time
still needed to reach the goal. The estimate shrinks as income grows, which is what makes
economy buildings worth their build time to the search.
"""

import heapq
from dataclasses import dataclass, field, replace

from .data import FACTORY, MOBILE, NANO
from .goal import Goal
from .sim import INF, State

DEFAULT_ECONOMY = ("cormex", "corwin", "corsolar", "cormakr", "corestor", "cormstor", "cornanotc", "coradvsol")
DEFAULT_CAPS = {"corestor": 2, "cormstor": 1, "cornanotc": 4, "cormakr": 12, "corwin": 40, "corsolar": 12,
                "coradvsol": 6}


@dataclass
class SearchConfig:
    beam_width: int = 150
    max_delay: float = 90.0  # longest wait considered before starting something
    economy_units: tuple[str, ...] = DEFAULT_ECONOMY
    caps: dict[str, int] = field(default_factory=dict)  # per-unit limits, merged over DEFAULT_CAPS
    constructor_cap: int = 3  # limit for constructors on the path to a target
    max_layers: int = 2000

    @classmethod
    def from_dict(cls, values: dict | None) -> "SearchConfig":
        values = dict(values or {})
        if "economy_units" in values:
            values["economy_units"] = tuple(values["economy_units"])
        return cls(**values)


@dataclass
class SearchResult:
    ops: list[tuple]  # replayable operations, see replay_ops()
    evaluation: dict
    nodes_expanded: int


class Node:
    __slots__ = ("state", "ops", "pending", "wake", "f")

    def __init__(self, state, ops, pending, wake):
        self.state = state
        self.ops = ops  # (parent ops, op) linked list
        self.pending = pending  # idle builders still to decide at this time
        self.wake = wake  # time some waiting builder wants to decide again

    def op_list(self) -> list[tuple]:
        out, node = [], self.ops
        while node is not None:
            node, op = node
            out.append(op)
        return out[::-1]


class Planner:
    def __init__(self, goal: Goal, config: SearchConfig | None = None):
        self.goal = goal
        self.cfg = config or SearchConfig()
        self.root = State(goal.config)
        self.units = self.root.units
        self.depth = self._tech_depth()
        self.allowed, self.caps = self._candidate_units()

    # ------------------------------------------------------------ setup

    def _tech_depth(self) -> dict[str, int]:
        depth = {self.goal.config.commander: 0}
        frontier = [self.goal.config.commander]
        while frontier:
            nxt = []
            for name in frontier:
                for option in self.units[name].build_options:
                    if option in self.units and option not in depth:
                        depth[option] = depth[name] + 1
                        nxt.append(option)
            frontier = nxt
        return depth

    def _builders_of(self, unit: str) -> list[str]:
        return [n for n, d in self.depth.items() if unit in self.units[n].build_options and d < self.depth[unit]]

    def prerequisites(self, unit: str) -> list[str]:
        """Cheapest chain of builders leading to `unit`, excluding the commander."""
        chain, current = [], unit
        while self.depth.get(current, 0) > 1:
            builders = self._builders_of(current)
            if not builders:
                break
            current = min(builders, key=lambda b: (self.depth[b], self.units[b].metal))
            chain.append(current)
        return chain[::-1]

    def _candidate_units(self) -> tuple[set[str], dict[str, int]]:
        for unit in self.goal.targets:
            if unit not in self.depth:
                raise ValueError(f"{unit} cannot be built starting from {self.goal.config.commander}")
        allowed = set(self.goal.targets)
        chain = {p for t in self.goal.targets for p in self.prerequisites(t)}
        allowed |= chain
        allowed |= {u for u in self.cfg.economy_units if u in self.depth}
        caps = dict(DEFAULT_CAPS)
        for unit in chain:
            kind = self.units[unit].kind
            caps.setdefault(unit, 1 if kind == FACTORY else self.cfg.constructor_cap)
        for unit, n in self.goal.targets.items():
            caps[unit] = max(n, caps.get(unit, 0) if unit in chain else 0)
        caps["cormex"] = self.goal.config.mex_spots
        caps.update(self.cfg.caps)
        return allowed, caps

    # ------------------------------------------------------------ scoring

    def _owned_or_building(self, state: State, unit: str) -> int:
        return state.count(unit) + sum(1 for j in state.jobs.values() if j.unit == unit)

    def remaining_estimate(self, state: State) -> float:
        need_m = need_e = need_bt = 0.0
        work: dict[str, float] = {}
        for unit, n in self.goal.targets.items():
            have = state.count(unit)
            if have >= n:
                continue
            fractions = sorted((1 - j.progress / self.units[unit].build_time
                                for j in state.jobs.values() if j.unit == unit))
            missing = n - have
            parts = fractions[:missing] + [1.0] * max(0, missing - len(fractions))
            work[unit] = sum(parts)
            for p in self.prerequisites(unit):
                if self._owned_or_building(state, p) == 0:
                    work[p] = work.get(p, 0) + 1.0
        if not work:
            return 0.0
        for unit, amount in work.items():
            u = self.units[unit]
            need_m += amount * u.metal
            need_e += amount * u.energy
            need_bt += amount * u.build_time
        helpers = sum(self.units[n].build_power for n in state.builder_unit
                      if self.units[n].kind in (MOBILE, NANO))
        t_bp = need_bt / max(helpers, 1.0)
        t_m = max(0.0, need_m - state.metal) / max(state.metal_income(), 0.1)
        e_in = state.energy_income()
        t_e = max(0.0, need_e - state.energy) / max(e_in, 0.1)
        return min(max(t_bp, t_m, t_e), self.goal.max_time)

    def _score(self, node: Node) -> float:
        return node.state.time + self._penalties(node.state) + self.remaining_estimate(node.state)

    def _penalties(self, state: State) -> float:
        m, w = state.metrics, self.goal.weights
        return (w.stall * m.stall_bp_seconds + w.overflow_metal * m.overflow_metal
                + w.overflow_energy * m.overflow_energy + w.idle * m.idle_bp_seconds)

    # ------------------------------------------------------------ actions

    def _delay_until_affordable(self, state: State, builder: int, unit: str) -> float:
        """Seconds to wait before `unit` can be built by `builder` without stalling,
        assuming current income and running jobs at full speed."""
        u = self.units[unit]
        duration = u.build_time / self.units[state.builder_unit[builder]].build_power
        load_m = load_e = 0.0
        for job in state.jobs.values():
            j = self.units[job.unit]
            load_m += job.bp / j.build_time * j.metal
            load_e += job.bp / j.build_time * j.energy
        wait = 0.0
        for stored, cap, income, load, cost in (
            (state.metal, state.max_metal, state.metal_income(), load_m, u.metal),
            (state.energy, state.max_energy, state.energy_income(), load_e, u.energy),
        ):
            net = income - load
            needed = cost - net * duration  # stock needed at the start
            if needed <= stored:
                continue
            if needed > cap or net <= 0:
                return INF
            wait = max(wait, (needed - stored) / net)
        return wait

    def _actions(self, node: Node, builder: int) -> list[tuple]:
        state = node.state
        bdef = self.units[state.builder_unit[builder]]
        actions: list[tuple] = []
        waits: set[float] = set()
        for unit in bdef.build_options:
            if unit not in self.allowed:
                continue
            if self._owned_or_building(state, unit) >= self.caps.get(unit, INF):
                continue
            if state.can_start(builder, unit) is not None:
                continue
            delay = self._delay_until_affordable(state, builder, unit)
            if delay <= 0.25 or delay == INF:
                actions.append(("start", builder, unit))
            elif delay <= self.cfg.max_delay:
                actions.append(("start", builder, unit))
                waits.add(round(delay + 0.05, 1))
        if bdef.can_assist:
            for job in state.jobs.values():
                remaining = (self.units[job.unit].build_time - job.progress) / job.bp
                if remaining > 1.0:
                    actions.append(("assist", builder, job.id))
        for delay in sorted(waits):
            actions.append(("wait", builder, state.time + delay))
        if state.jobs or node.wake < INF or waits:
            actions.append(("wait", builder, INF))
        return actions

    # ------------------------------------------------------------ search

    def _settle(self, node: Node) -> Node | None:
        """Advance a node whose idle builders have all decided to its next decision point.
        Returns None for dead ends."""
        state = node.state
        while not node.pending:
            if self.goal.satisfied(state):
                return node
            until = min(node.wake, self.goal.max_time)
            if not state.jobs and until == INF:
                return None
            before = state.time
            done = state.advance(until=until)
            if not done and state.time == before:
                return None  # nothing will ever change again
            node.ops = (node.ops, ("advance", until))
            if state.time >= self.goal.max_time and not self.goal.satisfied(state):
                return None
            if self.goal.satisfied(state):
                return node
            node.wake = INF
            node.pending = tuple(state.idle_builders())
        return node

    def _child(self, node: Node, action: tuple) -> Node | None:
        state = node.state.copy()
        kind, builder, arg = action
        wake = node.wake
        if kind == "start":
            state.start(builder, arg)
            op = ("start", builder, arg)
        elif kind == "assist":
            state.assist(builder, arg)
            op = ("assist", builder, arg)
        else:
            wake = min(wake, arg)
            op = None
        child = Node(state, (node.ops, op) if op else node.ops, node.pending[1:], wake)
        return self._settle(child)

    def _key(self, node: Node) -> tuple:
        s = node.state
        jobs = tuple(sorted((j.unit, round(j.progress / self.units[j.unit].build_time, 2), len(j.builders))
                            for j in s.jobs.values()))
        return (round(s.time, 1), tuple(sorted(s.counts.items())), jobs, node.pending,
                round(s.metal / 10), round(s.energy / 50), node.wake < INF)

    def search(self, progress=None) -> SearchResult | None:
        root = Node(self.root.copy(), None, tuple(self.root.idle_builders()), INF)
        root.f = self._score(root)
        frontier = [root]
        best: Node | None = None
        best_cost = INF
        expanded = 0
        for layer in range(self.cfg.max_layers):
            children: dict[tuple, Node] = {}
            for node in frontier:
                builder = node.pending[0]
                for action in self._actions(node, builder):
                    child = self._child(node, action)
                    expanded += 1
                    if child is None:
                        continue
                    if self.goal.satisfied(child.state):
                        cost = self.goal.evaluate(child.state)["cost"]
                        if cost < best_cost:
                            best, best_cost = child, cost
                        continue
                    child.f = self._score(child)
                    if child.f >= best_cost:
                        continue
                    key = self._key(child)
                    if key not in children or child.f < children[key].f:
                        children[key] = child
            frontier = heapq.nsmallest(self.cfg.beam_width, children.values(), key=lambda n: n.f)
            if progress:
                progress(layer, frontier, best_cost)
            if not frontier:
                break
        if best is None:
            return None
        return SearchResult(best.op_list(), self.goal.evaluate(best.state), expanded)


def replay_ops(goal: Goal, ops: list[tuple], record: bool = True) -> State:
    """Re-run a search result on a fresh (recorded) state."""
    state = State(goal.config, record=record)
    for op in ops:
        if op[0] == "start":
            state.start(op[1], op[2])
        elif op[0] == "assist":
            state.assist(op[1], op[2])
        elif op[0] == "advance":
            state.advance(until=op[1])
    return state


def optimize(goal: Goal, config: SearchConfig | None = None, progress=None) -> SearchResult | None:
    """Beam search at the configured width and at a quarter and half of it, keeping the
    cheapest result: beam search is not monotonic in its width."""
    config = config or SearchConfig()
    best = None
    for width in sorted({max(5, config.beam_width // 4), max(5, config.beam_width // 2), config.beam_width}):
        result = Planner(goal, replace(config, beam_width=width)).search(progress)
        if result and (best is None or result.evaluation["cost"] < best.evaluation["cost"]):
            best = result
    return best
