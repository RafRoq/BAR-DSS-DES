"""Hand-written build orders: load, replay on the simulator, and export.

A recipe is a list of steps (unit, [builder unit names], repeat), the format used by
main.py, or the YAML written by interface.py ("name: Display Name - unitname").

Replay semantics: steps start strictly in order, each as soon as one idle unit of every
listed builder type exists. The first listed builder able to build the unit starts the
job and the others assist. No skipping ahead and no sustainability check, so stalls in
the recipe show up as stalls.
"""

import yaml

from .config import EconConfig
from .sim import SimError, State


def load_yaml_recipe(path: str) -> list[tuple[str, list[str], int]]:
    with open(path, encoding="utf-8") as f:
        steps = yaml.safe_load(f) or []
    return [
        (step["name"].split(" - ")[-1].strip(), list(step.get("builders", [])), int(step.get("repeat", 1)))
        for step in steps
    ]


def expand(recipe) -> list[tuple[str, list[str]]]:
    return [(unit, list(builders)) for unit, builders, repeat in recipe for _ in range(repeat)]


def _pick_builders(state: State, unit: str, builder_types: list[str]) -> tuple[int, list[int]] | None:
    """Idle builder ids for a step: (owner, assistants), or None if not available now."""
    idle = state.idle_builders()
    chosen: list[int] = []
    for name in builder_types:
        match = next((b for b in idle if state.builder_unit[b] == name and b not in chosen), None)
        if match is None:
            return None
        chosen.append(match)
    owner = next((b for b in chosen if state.can_start(b, unit) is None), None)
    if owner is None:
        return None
    helpers = [b for b in chosen if b != owner]
    if any(not state.units[state.builder_unit[b]].can_assist for b in helpers):
        return None
    return owner, helpers


def _blocked_reason(state: State, unit: str, builder_types: list[str]) -> str:
    present = set(state.builder_unit)
    missing = [b for b in builder_types if b not in present]
    if missing:
        return f"builders {missing} do not exist"
    if not any(unit in state.units[b].build_options for b in builder_types if b in state.units):
        return f"none of {builder_types} can build {unit}"
    return "builders busy"


def replay(recipe, config: EconConfig | None = None, max_time: float = 1200.0,
           record: bool = True) -> tuple[State, list[str]]:
    """Run a recipe. Returns the final state and a list of problems (steps never started)."""
    state = State(config, record=record)
    steps = expand(recipe)
    i = 0
    while i < len(steps) or state.jobs:
        while i < len(steps):
            unit, builder_types = steps[i]
            picked = _pick_builders(state, unit, builder_types)
            if picked is None:
                break
            owner, helpers = picked
            job = state.start(owner, unit)
            for b in helpers:
                state.assist(b, job)
            i += 1
        if state.time >= max_time:
            break
        if not state.jobs:
            unit, builder_types = steps[i]
            return state, [f"step {i + 1} ({unit} by {builder_types}) can never start: "
                           f"{_blocked_reason(state, unit, builder_types)}"]
        state.advance(until=max_time)
    problems = [f"step {j + 1} ({u}) not started by t={max_time:.0f}s" for j, (u, _) in enumerate(steps[i:], i)]
    return state, problems


def to_yaml_steps(state: State) -> list[dict]:
    """Export a recorded run in interface.py's YAML recipe format.

    The format has no timestamps and names builders by type, so it is lossy: replaying
    it reproduces the order and builder types, not the exact timing."""
    rec = state.recorder
    if rec is None:
        raise SimError("state was not recorded")
    steps: list[dict] = []
    for job in sorted(rec.jobs.values(), key=lambda j: j["start"]):
        start = job["start"]
        builders = [rec.builder_units[b] for t, b in job["joins"] if t <= start + 1e-9]
        entry = {"name": f"{state.units[job['unit']].display_name} - {job['unit']}",
                 "repeat": 1, "builders": builders}
        if steps and steps[-1]["name"] == entry["name"] and steps[-1]["builders"] == builders:
            steps[-1]["repeat"] += 1
        else:
            steps.append(entry)
    return steps


def save_yaml(steps: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(steps, f, default_flow_style=False, sort_keys=False)
