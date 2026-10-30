"""Live re-planning for the in-game companion (companion.py, widget/gui_build_guide.lua).

The widget writes a snapshot of the game every few seconds (build_guide_state.json): time,
resources, wind, and every unit with its build progress and what each builder is doing.
This module rebuilds that exact moment in the simulator, plans from there to the goal and
returns the next steps (build_guide_live.json). Re-planning from what really happened
absorbs walking, wind changes and any deviation from the plan, without predicting them.

Snapshot format (all times in game seconds):

    {"format": "bar-build-guide-state", "seq": 12, "time": 83.4, "commander": "corcom",
     "metal": [stored, storage, income], "energy": [stored, storage, income],
     "wind": {"now": 13.1, "min": 0, "max": 20},
     "units": [{"id": 5, "name": "corcom", "done": true, "building": 41},
               {"id": 41, "name": "corwin", "done": false, "progress": 0.35},
               {"id": 52, "name": "corck", "done": true, "order": "cormex"}]}

`building` is the unit a builder is working on; `order` is a build it was told to do but has
not started yet (usually it is walking there).
"""

import time
from dataclasses import dataclass, field, replace

from .config import EconConfig
from .goal import Goal
from .search import INF, Node, Planner, SearchConfig, apply_ops
from .sim import State

STATE_FILE = "build_guide_state.json"
LIVE_FILE = "build_guide_live.json"
WIND_PRIOR_WEIGHT = 12  # the configured wind counts as this many observed samples


@dataclass
class Live:
    state: State
    game_id: dict[int, int]  # simulator builder id -> game unit id
    labels: dict[int, str]  # simulator builder id -> "Commander", "Construction Bot 2", ...
    existing_jobs: dict[int, int] = field(default_factory=dict)  # job id -> builders on it at the snapshot


class WindEstimate:
    """Running mean of the wind seen in the game, starting from the configured average."""

    def __init__(self, prior: float):
        self.prior, self.total, self.n = prior, 0.0, 0
        self.last_seq = None

    def update(self, snapshot: dict) -> float:
        now = (snapshot.get("wind") or {}).get("now")
        if now is not None and snapshot.get("seq") != self.last_seq:
            self.total += float(now)
            self.n += 1
            self.last_seq = snapshot.get("seq")
        return self.value

    @property
    def value(self) -> float:
        return (self.prior * WIND_PRIOR_WEIGHT + self.total) / (WIND_PRIOR_WEIGHT + self.n)


def state_from_snapshot(snapshot: dict, config: EconConfig, record: bool = False) -> Live:
    """The simulator state of a game snapshot: finished units, resources, jobs in progress
    with their builders, and builders already sent to build something."""
    commander = snapshot.get("commander") or config.commander
    config = replace(config, commander=commander)
    state = State(config, record=record)
    units = state.units
    state.time = float(snapshot["time"])
    if record:
        state.recorder.builder_spawn[0] = state.time
    entries = [u for u in snapshot["units"] if u.get("name") in units]
    by_id = {u["id"]: u for u in entries}

    game_id: dict[int, int] = {}
    sim_id: dict[int, int] = {}  # game unit id -> simulator builder id
    seen: dict[str, int] = {}
    labels: dict[int, str] = {}

    def register(game_unit: dict, builder: int) -> None:
        game_id[builder], sim_id[game_unit["id"]] = game_unit["id"], builder
        name = game_unit["name"]
        seen[name] = seen.get(name, 0) + 1
        labels[builder] = "Commander" if name == commander and seen[name] == 1 else \
            f"{units[name].display_name or name} {seen[name]}"

    commander_unit = next((u for u in entries if u["name"] == commander and u.get("done", True)), None)
    if commander_unit:
        register(commander_unit, 0)
    for u in entries:
        if not u.get("done", True) or u is commander_unit:
            continue
        before = len(state.builder_unit)
        state._add_unit(u["name"], +1)
        if units[u["name"]].is_mex:
            state.spots_used += 1
        if len(state.builder_unit) > before:
            register(u, before)
            if record:
                state.recorder.builder_spawn[before] = state.time

    metal, metal_storage = snapshot["metal"][0], snapshot["metal"][1]
    energy, energy_storage = snapshot["energy"][0], snapshot["energy"][1]
    state.max_metal, state.max_energy = float(metal_storage), float(energy_storage)
    state.metal = min(float(metal), state.max_metal)
    state.energy = min(float(energy), state.max_energy)

    # Nanoframes: one job each, owned by a builder able to build it, assisted by the others.
    for frame in entries:
        if frame.get("done", True):
            continue
        name = frame["name"]
        workers = [sim_id[u["id"]] for u in entries if u.get("building") == frame["id"] and u["id"] in sim_id]
        owners = [b for b in workers if name in units[state.builder_unit[b]].build_options]
        if not owners or state.can_start(owners[0], name) is not None:
            continue  # nobody on it: an abandoned frame
        job = state.start(owners[0], name)
        state.jobs[job].progress = min(0.999, float(frame.get("progress", 0.0))) * units[name].build_time
        for b in workers:
            if b != owners[0] and state.can_assist(b, job) is None:
                state.assist(b, job)

    # Builders walking to an order they have not started: committed to it.
    for u in entries:
        order = u.get("order")
        b = sim_id.get(u["id"])
        if b is None or not order or order not in units or by_id.get(u.get("building")):
            continue
        if state.builder_job[b] < 0 and state.can_start(b, order) is None:
            state.start(b, order)

    return Live(state, game_id, labels, {job_id: len(job.builders) for job_id, job in state.jobs.items()})


@dataclass
class Template:
    """The previous live plan as steps, with what was built or under way when it was made."""
    steps: list
    counts: dict[str, int]


SWITCH_GAIN = 1.0  # seconds a fresh plan must gain over continuing the previous one


def _started_counts(state: State) -> dict[str, int]:
    counts = {u: n for u, n in state.counts.items() if n > 0}
    for job in state.jobs.values():
        counts[job.unit] = counts.get(job.unit, 0) + 1
    return counts


def plan_live(goal: Goal, snapshot: dict, wind: float | None = None, width: int = 40,
              seconds: float = 2.0, template: Template | None = None) -> tuple[dict, Template | None]:
    """Plan from a snapshot to the goal. Returns the live plan the widget shows and the
    template to pass with the next snapshot.

    With a template, the rest of the previous plan is replayed from this snapshot too, and
    kept unless a fresh beam search beats it by SWITCH_GAIN seconds: while the game follows
    the plan, the steps shown stay put instead of jumping between near-equal plans."""
    from .improve import decode, remaining_steps, steps_from_recorder  # imports search

    started = time.perf_counter()
    config = goal.config if wind is None else replace(goal.config, wind_average=wind)
    goal = replace(goal, config=config, max_time=max(goal.max_time, float(snapshot["time"]) + 900.0))
    head = {"format": "bar-build-guide-live", "version": 1, "for_seq": snapshot.get("seq"),
            "for_time": snapshot["time"], "goal": goal.name, "targets": goal.targets,
            "wind_used": round(config.wind_average, 2)}

    probe = state_from_snapshot(snapshot, config).state
    if goal.satisfied(probe):
        return {**head, "done": True, "eta": snapshot["time"], "steps": []}, None

    candidates = []  # (cost, ops, source)
    if template is not None:
        now = _started_counts(probe)
        done = {u: max(0, n - template.counts.get(u, 0)) for u, n in now.items()}
        kept = decode(goal, remaining_steps(template.steps, done), probe)
        if kept.cost < INF:
            candidates.append((kept.cost - SWITCH_GAIN, kept.ops, "kept"))  # a bonus for staying put
    for mode in ("refined", "conservative"):
        if candidates and time.perf_counter() - started > seconds:
            break
        planner = Planner(goal, SearchConfig(beam_width=width, affordability=mode, improve_seconds=0))
        live = state_from_snapshot(snapshot, config)
        root = planner._settle(Node(live.state, None, tuple(live.state.idle_builders()), INF))
        if root is None:
            continue
        if goal.satisfied(root.state):
            candidates.append((goal.evaluate(root.state)["cost"], root.op_list(), "new"))
            continue
        result = planner.search(root=root)
        if result is not None:
            candidates.append((result.evaluation["cost"], result.ops, "new"))
    if not candidates:
        return {**head, "done": False, "eta": None, "steps": [], "problem": "no build reaches the goal"}, None

    _, ops, source = min(candidates, key=lambda c: c[0])
    live = state_from_snapshot(snapshot, config, record=True)
    counts = _started_counts(live.state)
    final = apply_ops(live.state, ops)
    template = Template(steps_from_recorder(final.recorder, running=live.existing_jobs), counts)
    return ({**head, "done": False, "eta": round(final.time, 2), "source": source,
             "steps": _steps(live, final), "planned_in": round(time.perf_counter() - started, 3)}, template)


def _steps(live: Live, final: State) -> list[dict]:
    """New builds and new assists after the snapshot, in start order."""
    rec, units = final.recorder, final.units
    labels = dict(live.labels)
    seen: dict[str, int] = {}
    for b in sorted(rec.builder_units):  # builders the plan makes later
        if b not in labels:
            name = rec.builder_units[b]
            seen[name] = seen.get(name, 0) + 1
            labels[b] = f"new {units[name].display_name or name} {seen[name]}"

    def who(b: int) -> dict:
        return {"builder": labels.get(b, f"builder {b}"), "builder_id": live.game_id.get(b),
                "builder_unit": final.builder_unit[b]}

    steps = []
    for job_id, job in sorted(rec.jobs.items(), key=lambda kv: kv[1]["start"]):
        unit = job["unit"]
        display = units[unit].display_name or unit
        joins = job["joins"]
        if job_id in live.existing_jobs:  # already being built: only builders sent to help later
            for t, b in joins[live.existing_jobs[job_id]:]:
                steps.append({"action": "assist", "unit": unit, "display": display, **who(b),
                              "start": round(t, 2), "finish": _round(job["end"])})
            continue
        (t, owner), *helpers = joins
        steps.append({"action": "build", "unit": unit, "display": display, **who(owner),
                      "helpers": [{**who(b), "t": round(ht, 2)} for ht, b in helpers],
                      "start": round(t, 2), "finish": _round(job["end"])})
    steps.sort(key=lambda s: (s["start"], s["action"] != "build"))
    for n, s in enumerate(steps, 1):
        s["n"] = n
    return steps


def _round(v):
    return None if v is None else round(v, 2)


def snapshot_of(state: State, seq: int = 0, wind: float | None = None, game_ids: dict[int, int] | None = None) -> dict:
    """A snapshot in the widget's format from a simulator state (for tests and replays).
    Unit ids are simulator builder ids for builders and 1000 + job id for nanoframes."""
    game_ids = game_ids or {}
    gid = lambda b: game_ids.get(b, b + 1)  # noqa: E731
    units = []
    frames = {job_id: 1000 + job_id for job_id in state.jobs}
    for b, name in enumerate(state.builder_unit):
        entry = {"id": gid(b), "name": name, "done": True}
        job = state.builder_job[b]
        if job >= 0:
            entry["building"] = frames[job]
        units.append(entry)
    counts = dict(state.counts)
    for name in state.builder_unit:
        counts[name] = counts.get(name, 0) - 1
    n = 0
    for name, c in counts.items():
        for _ in range(max(0, c)):
            n += 1
            units.append({"id": 500 + n, "name": name, "done": True})
    for job_id, job in state.jobs.items():
        units.append({"id": frames[job_id], "name": job.unit, "done": False,
                      "progress": job.progress / state.units[job.unit].build_time})
    return {"format": "bar-build-guide-state", "seq": seq, "time": state.time,
            "commander": state.config.commander,
            "metal": [state.metal, state.max_metal, state.metal_income()],
            "energy": [state.energy, state.max_energy, state.energy_income()],
            "wind": {"now": wind if wind is not None else state.config.wind_average},
            "units": units}
