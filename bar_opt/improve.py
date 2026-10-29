"""Improvement phase: polish the best build found by the beam searches.

Two complementary operators share the time budget:

- Step local search (simulated annealing). The build becomes an ordered list of steps:
  "build unit U with builder B, assisted by H", or "builder B assists step k", each with an
  optional "not before time T". A decoder runs the steps strictly in order on the
  simulator and returns the true cost, so small edits are cheap to evaluate: swap or
  move steps, change who builds or assists, shift or clear waits, insert or remove an
  economy building. This finds changes the beam's estimate ranks badly, such as a
  different opening order.
- Suffix re-planning. Cut the best build at a random decision point, rebuild the exact
  state there, and run a fresh beam search for the rest with varied settings.

Changes are kept only when the build gets cheaper (annealing may wander through worse
builds, but the best one seen is what is returned), so the result is never worse than
the build it starts from.
"""

import math
import random
import time
from dataclasses import dataclass, field, replace

from .goal import Goal
from .search import INF, Node, Planner, SearchCancelled, SearchConfig, SearchResult, replay_ops
from .sim import SimError, State


class _OutOfTime(Exception):
    pass


Ref = tuple[str, int]  # a builder by unit type and spawn order: ("corck", 1) is the second corck


@dataclass
class Step:
    unit: str | None  # unit to build; None for an assist step
    builder: Ref  # starts the job, or assists
    helpers: tuple[Ref, ...] = ()  # assist from the moment the job starts
    target: int = -1  # assist steps: index of the build step to help
    not_before: float = 0.0


@dataclass
class Decoded:
    cost: float
    ops: list = field(default_factory=list)
    evaluation: dict | None = None


# ---------------------------------------------------------------- build <-> steps

def steps_from_result(goal: Goal, result: SearchResult) -> list[Step]:
    """Turn a build into steps, keeping its timing where builders waited on purpose."""
    state = replay_ops(goal, result.ops)
    rec = state.recorder
    refs = _refs(rec.builder_units)
    busy_until = dict(rec.builder_spawn)  # builder -> time it last became free
    events = []
    for job_id, job in rec.jobs.items():
        for k, (t, b) in enumerate(job["joins"]):
            events.append((t, k, job_id, b))
    events.sort()
    steps: list[Step] = []
    step_of_job: dict[int, int] = {}
    for t, k, job_id, b in events:
        job = rec.jobs[job_id]
        waited = t - busy_until.get(b, 0.0) > 0.05
        if k == 0:
            helpers = tuple(refs[hb] for ht, hb in job["joins"][1:] if abs(ht - t) < 1e-9)
            step_of_job[job_id] = len(steps)
            steps.append(Step(job["unit"], refs[b], helpers, not_before=t if waited else 0.0))
        elif abs(t - job["start"]) > 1e-9:
            steps.append(Step(None, refs[b], target=step_of_job[job_id], not_before=t if waited else 0.0))
        busy_until[b] = job["end"] if job["end"] is not None else INF
    return steps


def _refs(builder_units: dict[int, str]) -> dict[int, Ref]:
    seen: dict[str, int] = {}
    out = {}
    for b in sorted(builder_units):
        unit = builder_units[b]
        out[b] = (unit, seen.get(unit, 0))
        seen[unit] = seen.get(unit, 0) + 1
    return out


def decode(goal: Goal, steps: list[Step]) -> Decoded:
    """Run steps strictly in order: each starts once its builders exist and are idle and
    its wait time has passed. Impossible steps are skipped; a deadlock costs INF."""
    state = State(goal.config)
    ops: list = []
    by_type: dict[str, list[int]] = {}
    job_of_step: dict[int, int] = {}

    def resolve(ref: Ref) -> int | None:
        ids = by_type.get(ref[0])
        if ids is None or len(ids) != sum(1 for u in state.builder_unit if u == ref[0]):
            ids = by_type[ref[0]] = [b for b, u in enumerate(state.builder_unit) if u == ref[0]]
        return ids[ref[1]] if ref[1] < len(ids) else None

    i = 0
    while not goal.satisfied(state):
        wake = INF
        while i < len(steps):
            step = steps[i]
            if step.not_before > state.time + 1e-9:
                wake = step.not_before
                break
            b = resolve(step.builder)
            if b is None:
                break  # builder not built yet
            if state.builder_job[b] >= 0:
                break
            if step.unit is None:
                job = job_of_step.get(step.target)
                if job is not None and job in state.jobs and state.can_assist(b, job) is None:
                    state.assist(b, job)
                    ops.append(("assist", b, job))
                i += 1
                continue
            helpers = [resolve(h) for h in step.helpers]
            if any(h is not None and state.builder_job[h] >= 0 for h in helpers):
                break
            reason = state.can_start(b, step.unit)
            if reason is None:
                job = state.start(b, step.unit)
                ops.append(("start", b, step.unit))
                job_of_step[i] = job
                for h in helpers:
                    if h is not None and h != b and state.can_assist(h, job) is None:
                        state.assist(h, job)
                        ops.append(("assist", h, job))
            i += 1  # started, or impossible for good (wrong builder, no spot): skip
        if goal.satisfied(state):
            break
        until = min(wake, goal.max_time)
        if not state.jobs and until == INF:
            return Decoded(INF)
        before = state.time
        try:
            done = state.advance(until=until)
        except SimError:
            return Decoded(INF)
        ops.append(("advance", until))
        if state.time >= goal.max_time and not goal.satisfied(state):
            return Decoded(INF)
        if not done and state.time == before:
            return Decoded(INF)
    evaluation = goal.evaluate(state)
    return Decoded(evaluation["cost"], ops, evaluation)


# ---------------------------------------------------------------- moves

class _Moves:
    def __init__(self, planner: Planner, rng: random.Random):
        self.p = planner
        self.rng = rng
        self.units = planner.units
        self.extras = [u for u in planner.allowed if u not in planner.goal.targets]

    def _builder_refs(self, steps: list[Step]) -> list[Ref]:
        """Builders that can exist: the commander plus every builder some step builds."""
        refs = [(self.p.goal.config.commander, 0)]
        count: dict[str, int] = {}
        for s in steps:
            if s.unit and self.units[s.unit].is_builder:
                refs.append((s.unit, count.get(s.unit, 0)))
                count[s.unit] = count.get(s.unit, 0) + 1
        return refs

    def _owner_for(self, steps, unit) -> Ref | None:
        options = [r for r in self._builder_refs(steps) if unit in self.units[r[0]].build_options]
        return self.rng.choice(options) if options else None

    def mutate(self, steps: list[Step]) -> list[Step] | None:
        rng = self.rng
        s = [replace(x) for x in steps]
        n = len(s)
        move = rng.random()
        if move < 0.25 and n >= 2:  # swap two steps
            a, b = rng.sample(range(n), 2)
            s[a], s[b] = s[b], s[a]
            return _fix_targets(s, {a: b, b: a})
        if move < 0.45 and n >= 2:  # move one step elsewhere
            a = rng.randrange(n)
            b = rng.randrange(n)
            order = list(range(n))
            order.insert(b, order.pop(a))
            return _fix_targets([s[k] for k in order], {old: new for new, old in enumerate(order)})
        if move < 0.55:  # change who builds a step
            builds = [k for k, x in enumerate(s) if x.unit]
            if builds:
                k = rng.choice(builds)
                owner = self._owner_for(s, s[k].unit)
                if owner:
                    s[k].builder = owner
                    return s
        if move < 0.65:  # add or remove a helper at the start of a build step
            builds = [k for k, x in enumerate(s) if x.unit]
            if builds:
                k = rng.choice(builds)
                if s[k].helpers and rng.random() < 0.5:
                    helpers = list(s[k].helpers)
                    helpers.pop(rng.randrange(len(helpers)))
                    s[k].helpers = tuple(helpers)
                else:
                    assisters = [r for r in self._builder_refs(s) if self.units[r[0]].can_assist
                                 and r != s[k].builder and r not in s[k].helpers]
                    if assisters:
                        s[k].helpers += (rng.choice(assisters),)
                return s
        if move < 0.8:  # shift or clear a wait
            k = rng.randrange(n) if n else None
            if k is not None:
                if s[k].not_before and rng.random() < 0.3:
                    s[k].not_before = 0.0
                else:
                    s[k].not_before = max(0.0, s[k].not_before + rng.uniform(-10, 10))
                return s
        if move < 0.9 and self.extras:  # insert an extra economy building
            unit = rng.choice(self.extras)
            owner = self._owner_for(s, unit)
            if owner:
                k = rng.randrange(n + 1)
                s.insert(k, Step(unit, owner))
                return _fix_targets(s, {old: (old + 1 if old >= k else old) for old in range(n)})
        # remove a step that is not needed for the goal's counts
        removable = [k for k, x in enumerate(s) if x.unit is None or x.unit not in self.p.goal.targets
                     or sum(1 for y in s if y.unit == x.unit) > self.p.goal.targets[x.unit]]
        if removable:
            k = rng.choice(removable)
            s.pop(k)
            mapping = {old: (old - 1 if old > k else old) for old in range(n) if old != k}
            return _fix_targets(s, mapping)
        return None


def _fix_targets(steps: list[Step], mapping: dict[int, int]) -> list[Step]:
    """Keep assist steps pointing at the same build step after a reorder; drop orphans."""
    out = []
    for x in steps:
        if x.unit is None:
            if x.target not in mapping:
                continue
            x.target = mapping[x.target]
        out.append(x)
    return out


# ---------------------------------------------------------------- driver

def improve(goal: Goal, result: SearchResult, config: SearchConfig, seconds: float,
            progress=None) -> SearchResult:
    """Spend up to `seconds` improving `result`. `progress(info)` gets fraction, best_cost,
    tries and accepted after each round; raising SearchCancelled from it stops early."""
    rng = random.Random(config.seed)
    started = time.perf_counter()
    deadline = started + seconds
    best = result
    tries = accepted = 0
    planner = Planner(goal, config)
    moves = _Moves(planner, rng)

    def report() -> bool:
        if progress is None:
            return True
        try:
            progress({"fraction": min(1.0, (time.perf_counter() - started) / seconds),
                      "best_cost": best.evaluation["cost"], "nodes": tries, "tries": tries, "accepted": accepted})
        except SearchCancelled:
            return False
        return True

    while time.perf_counter() < deadline:
        # Round of step local search, starting from the current best build.
        steps = steps_from_result(goal, best)
        current = decode(goal, steps)
        if current.cost > best.evaluation["cost"] + 1e-6:
            current = Decoded(best.evaluation["cost"])  # decoder lost some timing: anneal from here anyway
        round_end = min(deadline, time.perf_counter() + max(1.0, seconds / 6))
        temperature0 = 0.01 * best.evaluation["cost"]
        round_start = last_report = time.perf_counter()
        while time.perf_counter() < round_end:
            if time.perf_counter() - last_report > 0.5:  # keep progress live and Stop responsive
                if not report():
                    return best
                last_report = time.perf_counter()
            candidate = moves.mutate(steps)
            if candidate is None:
                continue
            tries += 1
            decoded = decode(goal, candidate)
            if decoded.cost == INF:
                continue
            delta = decoded.cost - current.cost
            heat = temperature0 * max(0.0, 1 - (time.perf_counter() - round_start) / max(round_end - round_start, 1e-9))
            if delta <= 0 or (heat > 0 and rng.random() < math.exp(-delta / heat)):
                steps, current = candidate, decoded
                if decoded.cost < best.evaluation["cost"] - 1e-9:
                    best = SearchResult(decoded.ops, decoded.evaluation, tries)
                    accepted += 1
        if not report() or time.perf_counter() >= deadline:
            break

        # One suffix re-plan.
        found = _replan_suffix(goal, best, config, rng, deadline)
        tries += 1
        if found is not None and found.evaluation["cost"] < best.evaluation["cost"] - 1e-9:
            best = found
            accepted += 1
        if not report():
            break
    return best


def _replan_suffix(goal: Goal, best: SearchResult, config: SearchConfig, rng: random.Random,
                   deadline: float) -> SearchResult | None:
    cuts = [i for i, op in enumerate(best.ops) if op[0] == "advance"]
    cut = rng.choice([-1] + cuts)  # -1: re-plan from the game start
    prefix = best.ops[:cut + 1]
    state = replay_ops(goal, prefix, record=False)
    pending = tuple(state.idle_builders())
    if goal.satisfied(state) or not pending:
        return None
    ops = None
    for op in prefix:
        ops = (ops, op)
    widths = sorted({max(5, config.beam_width // 8), max(5, config.beam_width // 4), max(5, config.beam_width // 2)})
    planner = Planner(goal, replace(
        config,
        beam_width=rng.choice(widths),
        affordability=rng.choice(["refined", "conservative"]),
        estimate=rng.choice(["plain", "converters"]),
        noise=rng.choice([0.0, 0.1, 0.25]),
        seed=rng.randrange(1 << 30),
    ))

    def check_time(_info):
        if time.perf_counter() > deadline:
            raise _OutOfTime

    try:
        return planner.search(check_time, root=Node(state, ops, pending, INF))
    except _OutOfTime:
        return planner.best_result()
