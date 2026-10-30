"""JSON views of recorded runs and of the unit catalog, for the web UI."""

import math
from dataclasses import asdict

from .data import UnitDef, tech_depth
from .goal import Goal
from .recipe import to_yaml_steps
from .sim import Segment, State


def _num(value: float) -> float | None:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    return round(value, 4)


def evaluation_json(evaluation: dict) -> dict:
    def value(v):
        if isinstance(v, bool):
            return v
        if isinstance(v, (list, tuple)):
            return [value(x) for x in v]
        return _num(v)

    return {k: value(v) for k, v in evaluation.items()}


def run_json(state: State, goal: Goal, label: str, kind: str, problems: list[str] | None = None,
             meta: dict | None = None) -> dict:
    """Everything the UI needs about one recorded run. kind is "optimizer" or "recipe"."""
    rec = state.recorder
    units = state.units
    segments = {field: [_num(getattr(s, field)) for s in rec.segments] for field in Segment._fields}

    jobs = []
    for job_id, job in sorted(rec.jobs.items(), key=lambda kv: kv[1]["start"]):
        end = job["end"]
        stop = end if end is not None else state.time
        stalls, open_at = [], None
        for s in rec.segments:
            if s.t1 <= job["start"] or s.t0 >= stop or s.t1 == s.t0:
                continue
            stalled = s.speed < 1 - 1e-9
            if stalled and open_at is None:
                open_at = max(s.t0, job["start"])
            elif not stalled and open_at is not None:
                stalls.append([_num(open_at), _num(s.t0)])
                open_at = None
        if open_at is not None:
            stalls.append([_num(open_at), _num(stop)])
        jobs.append({"id": job_id, "unit": job["unit"], "start": _num(job["start"]), "end": _num(end),
                     "joins": [[_num(t), b] for t, b in job["joins"]], "stalls": stalls})

    steps = []
    for job in jobs:
        for i, (t, b) in enumerate(job["joins"]):
            steps.append({"t": t, "builder": b, "action": "start" if i == 0 else "assist",
                          "unit": job["unit"], "job": job["id"]})
    steps.sort(key=lambda s: (s["t"], s["action"] != "start", s["job"], s["builder"]))

    counts: dict[str, int] = {}
    target_times = {u: None for u in goal.targets}
    for t, unit, delta in rec.unit_events:
        counts[unit] = counts.get(unit, 0) + delta
        if unit in target_times and target_times[unit] is None and counts[unit] >= goal.targets[unit]:
            target_times[unit] = _num(t)

    used = {j["unit"] for j in jobs} | set(rec.builder_units.values()) | {u for _, u, _ in rec.unit_events}
    return {
        "label": label,
        "kind": kind,
        "goal": {"name": goal.name, "targets": goal.targets, "max_time": goal.max_time,
                 "weights": asdict(goal.weights), "config": asdict(goal.config),
                 "milestones": [{"at": m.at, "units": m.units} for m in goal.milestones]},
        "evaluation": evaluation_json(goal.evaluate(state)),
        "end_time": _num(state.time),
        "problems": problems or [],
        "segments": segments,
        "jobs": jobs,
        "builders": [{"id": b, "unit": u, "spawn": _num(rec.builder_spawn[b])}
                     for b, u in sorted(rec.builder_units.items())],
        "unit_events": [[_num(t), u, d] for t, u, d in rec.unit_events],
        "target_times": target_times,
        "steps": steps,
        "yaml_steps": to_yaml_steps(state),
        "units": {u: unit_brief(units[u]) for u in sorted(used)},
        "meta": meta or {},
    }


def unit_brief(u: UnitDef) -> dict:
    return {"name": u.name, "display": u.display_name, "metal": u.metal, "energy": u.energy,
            "build_time": u.build_time, "build_power": u.build_power, "kind": u.kind}


def economy_summary(u: UnitDef, wind_average: float, tidal: float, metal_per_spot: float) -> list[str]:
    parts = []
    if u.extracts_metal:
        parts.append(f"+{u.extracts_metal * 1000 * metal_per_spot:.1f} metal/s on a spot")
    if u.metal_make:
        parts.append(f"+{u.metal_make:g} metal/s")
    if u.wind:
        parts.append(f"+{min(wind_average, u.wind):.1f} energy/s (wind, max {u.wind:g})")
    if u.tidal:
        parts.append(f"+{tidal:g} energy/s (tidal)")
    if u.energy_make:
        parts.append(f"+{u.energy_make:g} energy/s")
    if u.energy_upkeep:
        parts.append(f"-{u.energy_upkeep:g} energy/s upkeep")
    if u.conv_capacity:
        parts.append(f"converts {u.conv_capacity:g} energy/s to {u.conv_capacity * u.conv_efficiency:.2f} metal/s")
    if u.metal_storage:
        parts.append(f"+{u.metal_storage:g} metal storage")
    if u.energy_storage >= 1:
        parts.append(f"+{u.energy_storage:g} energy storage")
    if u.build_power:
        parts.append(f"{u.build_power:g} build power")
    return parts


def catalog_json(units: dict[str, UnitDef], commander: str, wind_average: float, tidal: float,
                 metal_per_spot: float) -> list[dict]:
    """Every unit the commander can eventually build, with who builds it."""
    depth = tech_depth(units, commander)
    built_by: dict[str, list[str]] = {}
    for name in depth:
        for option in units[name].build_options:
            if option in depth:
                built_by.setdefault(option, []).append(name)
    out = []
    for name, d in sorted(depth.items(), key=lambda kv: (kv[1], kv[0])):
        u = units[name]
        out.append({
            **unit_brief(u),
            "description": u.description,
            "tech_level": u.tech_level,
            "depth": d,
            "can_assist": u.can_assist,
            "is_builder": u.is_builder,
            "is_mex": u.is_mex,
            "build_options": [o for o in u.build_options if o in depth],
            "built_by": built_by.get(name, []),
            "economy": economy_summary(u, wind_average, tidal, metal_per_spot),
        })
    return out
