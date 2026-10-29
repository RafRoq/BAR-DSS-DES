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

from .data import FACTORY, MOBILE, NANO, tech_depth
from .goal import Goal
from .sim import INF, State

# T2 economy (cormoho, corfus, cormmkr) needs a T2 constructor; the tech tree keeps it out of
# reach until one exists, so listing it costs nothing for T1 goals.
DEFAULT_ECONOMY = ("cormex", "corwin", "corsolar", "cormakr", "corestor", "cormstor", "cornanotc", "coradvsol",
                   "cormoho", "corfus", "cormmkr")
DEFAULT_CAPS = {"corestor": 2, "cormstor": 1, "cornanotc": 4, "cormakr": 12, "corwin": 40, "corsolar": 12,
                "coradvsol": 6, "corfus": 4, "cormmkr": 10}


@dataclass
class SearchConfig:
    beam_width: int = 150
    max_delay: float = 90.0  # longest wait considered before starting something
    economy_units: tuple[str, ...] = DEFAULT_ECONOMY
    caps: dict[str, int] = field(default_factory=dict)  # per-unit limits, merged over DEFAULT_CAPS
    constructor_cap: int = 3  # limit for constructors on the path to a target
    max_layers: int = 20000  # safety net; searches end when the frontier is empty
    # How running jobs are charged when checking whether something is affordable:
    # "refined" charges what each job still has to pay, "conservative" charges their
    # current rate for the whole duration. None tries both and keeps the cheaper result.
    affordability: str | None = None
    # Remaining-time estimate: "plain" divides the metal and energy shortfalls by current
    # income; "converters" also lets converters turn spare energy into metal. Neither wins
    # on every benchmark goal.
    estimate: str = "plain"

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
        return tech_depth(self.units, self.goal.config.commander)

    def _builders_of(self, unit: str) -> list[str]:
        return [n for n, d in self.depth.items() if unit in self.units[n].build_options and d < self.depth[unit]]

    def prerequisites(self, unit: str, free: frozenset = frozenset()) -> list[str]:
        """Cheapest chain of builders leading to `unit`, excluding the commander, by total
        metal + energy / 70 of the chain. Units in `free` (already needed) cost nothing."""
        best: dict[str, tuple[float, list[str]]] = {self.goal.config.commander: (0.0, [])}
        for name in sorted(self.depth, key=self.depth.get):
            if name in best:
                continue
            options = [best[b] for b in self._builders_of(name) if b in best and self.units[b].is_builder]
            if not options:
                continue
            cost, chain = min(options, key=lambda o: o[0])
            u = self.units[name]
            own = 0.0 if name in free else u.metal + u.energy / 70
            best[name] = (cost + own, chain + [name])
        if unit not in best:
            return []
        return best[unit][1][:-1]

    def _target_chains(self) -> dict[str, list[str]]:
        """Prerequisites per target, deepest target first so shallower ones reuse its chain.
        Targets themselves are free, since they get built anyway."""
        chains, needed = {}, frozenset(self.goal.targets)
        for unit in sorted(self.goal.targets, key=lambda t: -self.depth[t]):
            chains[unit] = self.prerequisites(unit, needed)
            needed |= set(chains[unit]) | {unit}
        return chains

    def _candidate_units(self) -> tuple[set[str], dict[str, int]]:
        for unit in self.goal.targets:
            if unit not in self.depth:
                raise ValueError(f"{unit} cannot be built starting from {self.goal.config.commander}")
        self.chains = self._target_chains()
        allowed = set(self.goal.targets)
        chain = {p for c in self.chains.values() for p in c}
        allowed |= chain
        allowed |= {u for u in self.cfg.economy_units if u in self.depth}
        caps = dict(DEFAULT_CAPS)
        for unit in chain:
            kind = self.units[unit].kind
            caps.setdefault(unit, 1 if kind == FACTORY else self.cfg.constructor_cap)
        for unit, n in self.goal.targets.items():
            caps[unit] = max(n, caps.get(unit, 0) if unit in chain else 0)
        caps["cormex"] = self.goal.config.mex_spots
        caps["cormoho"] = self.goal.config.mex_spots
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
            for p in self.chains[unit]:
                if self._owned_or_building(state, p) == 0:
                    work[p] = work.get(p, 0) + 1.0
        if not work:
            return 0.0
        for unit, amount in work.items():
            u = self.units[unit]
            need_m += amount * u.metal
            need_e += amount * u.energy
            need_bt += amount * u.build_time
        helpers = 0.0
        for name in state.builder_unit:
            u = self.units[name]
            if u.kind in (MOBILE, NANO) or (u.kind == FACTORY and any(w in u.build_options for w in work)):
                helpers += u.build_power
        t_bp = need_bt / max(helpers, 1.0)
        short_m, short_e = need_m - state.metal, need_e - state.energy
        if self.cfg.estimate == "converters":
            t_res = self._resource_time(state, short_m, short_e)
        else:
            t_res = max(max(0.0, short_m) / max(state.metal_income(), 0.1),
                        max(0.0, short_e) / max(state.energy_income(), 0.1))
        # Not capped at max_time: for big goals the raw value is what tells the search that
        # economy is worth building. Nodes past max_time are dropped when they get there.
        return max(t_bp, t_res)

    @staticmethod
    def _resource_time(state: State, short_m: float, short_e: float) -> float:
        """Time for current income to cover a metal and energy shortfall, with converters
        turning spare energy into metal at the best split between the two."""
        m_in = max(state.metal_income(), 0.1)
        e_in = max(state.energy_income(), 0.1)
        short_m, short_e = max(0.0, short_m), max(0.0, short_e)
        efficiency = (sum(e * c for e, c in state.converters) / state.conv_capacity) if state.conv_capacity else 0.0
        top = min(state.conv_capacity, e_in * 0.999)  # energy/s that could go into converters

        def times(use):
            return short_m / (m_in + use * efficiency), short_e / (e_in - use)

        t_m, t_e = times(0.0)
        if t_m <= t_e or top <= 0 or efficiency <= 0:
            return max(t_m, t_e)
        t_m, t_e = times(top)
        if t_m >= t_e:
            return t_m
        lo, hi = 0.0, top  # metal time falls and energy time rises with conversion: find where they meet
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            t_m, t_e = times(mid)
            if t_m > t_e:
                lo = mid
            else:
                hi = mid
        return max(times(hi))

    def _score(self, node: Node) -> float:
        return node.state.time + self._penalties(node.state) + self.remaining_estimate(node.state)

    def _penalties(self, state: State) -> float:
        m, w = state.metrics, self.goal.weights
        return (w.stall * m.stall_bp_seconds + w.overflow_metal * m.overflow_metal
                + w.overflow_energy * m.overflow_energy + w.idle * m.idle_bp_seconds)

    # ------------------------------------------------------------ actions

    def _delay_until_affordable(self, state: State, bp: float, unit: str, duration: float) -> float | None:
        """Seconds to wait before adding `bp` build power to building `unit` for `duration`
        seconds can be paid without stalling, assuming current income and running jobs.

        Returns 0 if affordable now, a positive wait, INF if it cannot be paid without
        stalling even with nothing else running (start anyway: waiting does not help), or
        None if it only becomes affordable once running jobs finish (wait for an event)."""
        u = self.units[unit]
        conservative = self.cfg.affordability == "conservative"
        load_m = load_e = 0.0  # current spending rate of running jobs
        owed_m = owed_e = 0.0  # what running jobs still spend within `duration`
        for job in state.jobs.values():
            j = self.units[job.unit]
            rate = job.bp / j.build_time
            load_m += rate * j.metal
            load_e += rate * j.energy
            if conservative:
                owed_m += rate * j.metal * duration
                owed_e += rate * j.energy * duration
            else:
                left = 1.0 - job.progress / j.build_time
                done_within = min(left, rate * duration)
                owed_m += done_within * j.metal
                owed_e += done_within * j.energy
        # Converters are ignored here on purpose: making this converter-aware (energy above
        # the converter level grows only at net - capacity) never helped on the benchmark
        # goals and made the sea build 60% worse.
        wait = 0.0
        for stored, cap, income, load, owed, cost in (
            (state.metal, state.max_metal, state.metal_income(), load_m, owed_m, u.metal),
            (state.energy, state.max_energy, state.energy_income(), load_e, owed_e, u.energy),
        ):
            spend = bp / u.build_time * cost * duration
            needed = spend + owed - income * duration  # stock needed at the start
            if needed <= stored:
                continue
            net = income - load
            if net > 0 and needed <= cap:
                wait = max(wait, (needed - stored) / net)
                continue
            needed_alone = spend - income * duration
            if income > 0 and needed_alone <= cap:
                return None
            return INF
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
            delay = self._delay_until_affordable(state, bdef.build_power, unit,
                                                 self.units[unit].build_time / bdef.build_power)
            if delay is None:
                continue
            if delay <= 0.25 or delay == INF:
                actions.append(("start", builder, unit))
            elif delay <= self.cfg.max_delay:
                actions.append(("start", builder, unit))
                waits.add(round(delay + 0.05, 1))
        if bdef.can_assist:
            for job in state.jobs.values():
                u = self.units[job.unit]
                remaining = (u.build_time - job.progress) / (job.bp + bdef.build_power)
                if remaining <= 1.0:
                    continue
                if self._delay_until_affordable(state, bdef.build_power, job.unit, remaining) == 0:
                    actions.append(("assist", builder, job.id))
        for delay in sorted(waits):
            actions.append(("wait", builder, state.time + delay))
        # Waiting needs something to wait for: a running job, a wake-up, or another builder
        # still to decide this turn (if nobody starts anything, _settle drops the node).
        if state.jobs or node.wake < INF or waits or len(node.pending) > 1:
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
        """Run the beam search. `progress(info)` is called after every layer with a dict of
        layer, frontier, nodes, best_cost and fraction (rough share of the way to the goal
        of the best frontier node); it may raise SearchCancelled to stop early, after
        which best_result() still returns the best build found so far."""
        root = Node(self.root.copy(), None, tuple(self.root.idle_builders()), INF)
        root.f = self._score(root)
        frontier = [root]
        self._best, self._best_cost, self._expanded = None, INF, 0
        for layer in range(self.cfg.max_layers):
            children: dict[tuple, Node] = {}
            for node in frontier:
                builder = node.pending[0]
                for action in self._actions(node, builder):
                    child = self._child(node, action)
                    self._expanded += 1
                    if child is None:
                        continue
                    if self.goal.satisfied(child.state):
                        cost = self.goal.evaluate(child.state)["cost"]
                        if cost < self._best_cost:
                            self._best, self._best_cost = child, cost
                        continue
                    child.f = self._score(child)
                    if child.f >= self._best_cost:
                        continue
                    key = self._key(child)
                    if key not in children or child.f < children[key].f:
                        children[key] = child
            frontier = heapq.nsmallest(self.cfg.beam_width, children.values(), key=lambda n: n.f)
            if progress:
                fraction = 1.0
                if frontier:
                    lead = frontier[0].state
                    fraction = lead.time / max(lead.time + self.remaining_estimate(lead), 1e-9)
                progress({"layer": layer, "frontier": len(frontier), "nodes": self._expanded,
                          "best_cost": None if self._best_cost == INF else self._best_cost,
                          "fraction": fraction})
            if not frontier:
                break
        return self.best_result()

    def best_result(self) -> SearchResult | None:
        best = getattr(self, "_best", None)
        if best is None:
            return None
        return SearchResult(best.op_list(), self.goal.evaluate(best.state), self._expanded)


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


class SearchCancelled(Exception):
    """Raised by a progress callback to stop the search."""


def search_runs(config: SearchConfig) -> list[tuple[str, int]]:
    """The (affordability mode, beam width) runs optimize() performs."""
    widths = sorted({max(5, config.beam_width // 4), max(5, config.beam_width // 2), config.beam_width})
    modes = [config.affordability] if config.affordability else ["refined", "conservative"]
    return [(mode, width) for mode in modes for width in widths]


def optimize(goal: Goal, config: SearchConfig | None = None, progress=None) -> SearchResult | None:
    """Beam search at the configured width and at a quarter and half of it, with both
    affordability estimates unless one is configured, keeping the cheapest result. Beam
    search is not monotonic in its width, and small changes to the candidate actions
    can lead it into a different part of the search space.

    `progress(info)` receives the per-layer info of Planner.search plus run, runs, mode,
    width, and done/cost when a run finishes. Raising SearchCancelled from it stops the
    search and returns the best build found so far."""
    config = config or SearchConfig()
    runs = search_runs(config)
    best = None

    def keep(result):
        nonlocal best
        if result and (best is None or result.evaluation["cost"] < best.evaluation["cost"]):
            best = result

    for i, (mode, width) in enumerate(runs):
        planner = Planner(goal, replace(config, beam_width=width, affordability=mode))
        tag = {"run": i, "runs": len(runs), "mode": mode, "width": width}
        report = (lambda info, tag=tag: progress({**info, **tag})) if progress else None
        try:
            result = planner.search(report)
        except SearchCancelled:
            keep(planner.best_result())
            break
        keep(result)
        if progress:
            progress({**tag, "done": True, "cost": result.evaluation["cost"] if result else None,
                      "nodes": result.nodes_expanded if result else 0, "fraction": 1.0})
    return best
