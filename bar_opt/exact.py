"""Depth-first branch and bound: proves how far the beam search is from optimal.

Explores the same decisions as the beam search (Planner._actions): start a unit, wait
until it is affordable, assist, or wait for the next event, so "optimal" means optimal
within that decision space, not over every possible wait time. A branch is cut when a
lower bound on its final cost reaches the best cost found so far. The bound never
overestimates, so when the search space is exhausted the best build is proven optimal;
when a time or node budget stops it first, the result is an interval: the true optimum
lies between `lower_bound` and the best cost found.
"""

import math
import time
from dataclasses import dataclass

from .config import EconConfig
from .data import FACTORY
from .goal import Goal
from .search import INF, Node, Planner, SearchConfig, SearchResult


@dataclass
class ExactResult:
    result: SearchResult | None  # best build found (possibly the incumbent passed in)
    lower_bound: float  # the optimal cost is at least this
    proven: bool  # True if the search space was exhausted: result is optimal
    nodes: int
    seconds: float

    @property
    def gap(self) -> float | None:
        """Largest possible relative distance of the best build from optimal."""
        if self.result is None:
            return None
        cost = self.result.evaluation["cost"]
        return (cost - self.lower_bound) / cost if cost > 0 else 0.0


class BranchAndBound:
    def __init__(self, goal: Goal, config: SearchConfig | None = None):
        self.goal = goal
        self.planner = Planner(goal, config)
        self.units = self.planner.units
        p = self.planner
        self.builder_types = [u for u in p.allowed if self.units[u].is_builder]
        # Upper bounds on income from everything the caps still allow (loose but safe).
        cfg: EconConfig = goal.config
        self._mex_metal = {u: self.units[u].extracts_metal * 1000 * cfg.metal_per_spot
                           for u in p.allowed if self.units[u].is_mex}

    # ------------------------------------------------------------ lower bound

    def _required_prereqs(self, state, unit: str) -> list[str]:
        """Builders that every way of building `unit` needs and that do not exist yet."""
        possible = self.planner.allowed | {self.goal.config.commander} | set(state.builder_unit)
        out, current = [], unit
        while True:
            makers = [b for b in possible if current in self.units[b].build_options]
            if len(makers) != 1:
                return out
            (maker,) = makers
            if self.planner._owned_or_building(state, maker) > 0 or maker in out or maker == unit:
                return out
            out.append(maker)
            current = maker

    def _cap_left(self, state, unit: str) -> float:
        cap = self.planner.caps.get(unit, INF)
        return max(0.0, cap - self.planner._owned_or_building(state, unit))

    def remaining_lower_bound(self, state) -> float:
        work: dict[str, float] = {}  # unit -> remaining build fraction summed over instances
        for unit, n in self.goal.targets.items():
            have = state.count(unit)
            if have >= n:
                continue
            fractions = sorted(1 - j.progress / self.units[unit].build_time
                               for j in state.jobs.values() if j.unit == unit)
            missing = n - have
            work[unit] = work.get(unit, 0.0) + sum(fractions[:missing]) + max(0, missing - len(fractions))
            for p in self._required_prereqs(state, unit):
                work.setdefault(p, 1.0)
        if not work:
            return 0.0
        total_work = sum(f * self.units[u].build_time for u, f in work.items())

        # Build power: what exists now, plus every builder the caps still allow, each
        # available no earlier than its own build time at the highest conceivable build power.
        bp_now = state.total_bp
        future = []
        for b in self.builder_types:
            left = self._cap_left(state, b)
            if left == INF:
                return 0.0  # unbounded build power: no useful bound from work
            future += [self.units[b].build_power] * int(left)
        building = [j for j in state.jobs.values() if self.units[j.unit].is_builder]
        bp_cap = bp_now + sum(future) + sum(self.units[j.unit].build_power for j in building)
        ramp = []
        for b in self.builder_types:
            left = int(self._cap_left(state, b))
            if left:
                ramp += [(self.units[b].build_time / bp_cap, self.units[b].build_power)] * left
        for j in building:  # builders under construction join once finished
            u = self.units[j.unit]
            ramp.append(((u.build_time - j.progress) / bp_cap, u.build_power))
        t_work = _time_to_do(total_work, bp_now, sorted(ramp))

        # Critical path: unavoidable prerequisites come before their target.
        t_path = 0.0
        for unit in self.goal.targets:
            if unit not in work:
                continue
            chain = list(reversed(self._required_prereqs(state, unit))) + [unit]
            t_path = max(t_path, sum(work.get(u, 1.0) * self.units[u].build_time / bp_cap for u in chain))

        # Resources: shortfall over the highest income the caps could ever allow.
        need_m = sum(f * self.units[u].metal for u, f in work.items()) - state.metal
        if any(self.units[u].is_mex and self.units[u].tech_level > 1 for u in self.planner.allowed):
            # each T2 extractor finishing on a T1 refunds the T1's metal
            need_m -= self.goal.config.mex_spots * max((self.units[u].metal for u in self._mex_metal), default=0.0)
        need_e = sum(f * self.units[u].energy for u, f in work.items()) - state.energy
        t_res = max(_over(need_m, self._max_metal_income(state)), _over(need_e, self._max_energy_income(state)))
        return max(t_work, t_path, t_res)

    def _max_metal_income(self, state) -> float:
        cfg = self.goal.config
        best_mex = max(self._mex_metal.values(), default=0.0)
        income = state.metal_make + cfg.mex_spots * best_mex  # every spot with the best extractor
        conv = 0.0
        for u in self.planner.allowed:
            d = self.units[u]
            if d.conv_capacity:
                left = self._cap_left(state, u)
                if left == INF:
                    return INF
                conv += (left + state.count(u)) * d.conv_capacity * d.conv_efficiency
        return income + conv

    def _max_energy_income(self, state) -> float:
        cfg = self.goal.config
        income = state.energy_make
        for u in self.planner.allowed:
            d = self.units[u]
            gain = d.energy_make + (min(cfg.wind_average, d.wind) if d.wind else 0) + (cfg.tidal_strength if d.tidal else 0)
            if gain > 0:
                left = self._cap_left(state, u)
                if left == INF:
                    return INF
                income += left * gain
        return income

    def lower_bound(self, node: Node) -> float:
        return node.state.time + self.planner._penalties(node.state) + self.remaining_lower_bound(node.state)

    # ------------------------------------------------------------ search

    def _key(self, node: Node) -> tuple:
        s = node.state
        jobs = tuple(sorted((j.unit, round(j.progress, 3), tuple(j.builders)) for j in s.jobs.values()))
        return (round(s.time, 4), tuple(sorted(s.counts.items())), jobs, tuple(s.builder_job), node.pending,
                round(s.metal, 2), round(s.energy, 2), node.wake)

    def solve(self, incumbent: SearchResult | None = None, time_limit: float = 60.0,
              node_limit: int = 2_000_000) -> ExactResult:
        p = self.planner
        started = time.perf_counter()
        best = incumbent
        best_cost = incumbent.evaluation["cost"] if incumbent else INF
        best_node = None
        root = Node(p.root.copy(), None, tuple(p.root.idle_builders()), INF)
        root_bound = self.lower_bound(root)
        stack = [(root_bound, root)]
        seen: dict[tuple, float] = {}
        nodes = 0
        stopped = False
        while stack:
            bound, node = stack.pop()
            if bound >= best_cost - 1e-9:
                continue
            if nodes >= node_limit or time.perf_counter() - started > time_limit:
                stack.append((bound, node))
                stopped = True
                break
            children = []
            for action in p._actions(node, node.pending[0]):
                child = p._child(node, action)
                nodes += 1
                if child is None:
                    continue
                if self.goal.satisfied(child.state):
                    cost = self.goal.evaluate(child.state)["cost"]
                    if cost < best_cost - 1e-9:
                        best_cost, best_node = cost, child
                    continue
                g = child.state.time + p._penalties(child.state)
                key = self._key(child)
                if seen.get(key, INF) <= g + 1e-9:
                    continue
                seen[key] = g
                child_bound = g + self.remaining_lower_bound(child.state)
                if child_bound < best_cost - 1e-9:
                    children.append((p._score(child), child_bound, child))
            # Most promising child (by the beam's own score) is popped first.
            for _, child_bound, child in sorted(children, key=lambda c: -c[0]):
                stack.append((child_bound, child))
        if best_node is not None:
            best = SearchResult(best_node.op_list(), self.goal.evaluate(best_node.state), nodes)
        open_bounds = [b for b, _ in stack if b < best_cost]
        lower = min([best_cost] + open_bounds) if stopped else best_cost
        if best is None:
            lower = min(open_bounds, default=root_bound) if stopped else INF
        return ExactResult(best, max(root_bound, lower) if best else lower, not stopped, nodes,
                           time.perf_counter() - started)


def _over(shortfall: float, income: float) -> float:
    if shortfall <= 0:
        return 0.0
    if income == INF or income <= 0:
        return 0.0 if income == INF else INF
    return shortfall / income


def _time_to_do(work: float, bp_now: float, ramp: list[tuple[float, float]]) -> float:
    """Smallest T with bp_now*T + sum(bp_i * max(0, T - t_i)) >= work. `ramp` is sorted by t_i."""
    if work <= 0:
        return 0.0
    rate, done, t = bp_now, 0.0, 0.0
    for start, bp in ramp:
        if rate > 0 and done + rate * (start - t) >= work:
            return t + (work - done) / rate
        done += rate * (start - t)
        t = start
        rate += bp
    return t + (work - done) / rate if rate > 0 else math.inf
