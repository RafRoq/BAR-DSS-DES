"""Plan files for the in-game build guide widget (widget/gui_build_guide.lua).

The widget reads `LuaUI/Config/build_guide_plan.json` from the BAR data folder, follows your
own units during the game, and writes what actually happened to
`LuaUI/Config/build_guide_log_<date>.json`, one file per game. The web UI compares that log
with the plan.

A plan is made from a run as serialized by serialize.run_json, so it can be built from any
optimizer or recipe run the web UI holds.
"""

import bisect
import json
import os
import re
import shutil

FORMAT = "bar-build-guide"
VERSION = 1
PLAN_FILE = "build_guide_plan.json"
SAMPLE_EVERY = 5.0  # seconds between resource samples


def builder_labels(run: dict) -> dict[int, str]:
    """"Commander", "Construction Bot 1", "Construction Bot 2", ... by builder id."""
    commander = run["goal"]["config"]["commander"]
    seen: dict[str, int] = {}
    out = {}
    for b in sorted(run["builders"], key=lambda b: b["id"]):
        unit = b["unit"]
        display = run["units"].get(unit, {}).get("display") or unit
        if unit == commander:
            out[b["id"]] = "Commander"
            continue
        seen[unit] = seen.get(unit, 0) + 1
        out[b["id"]] = f"{display} {seen[unit]}"
    return out


def plan_json(run: dict) -> dict:
    """The guide plan of a run: one step per job, in the order the jobs start.

    `occurrence` is how many of that unit have been started so far, counting this one: the
    widget matches the nth unit of a kind you start in-game to the step with occurrence n."""
    labels = builder_labels(run)
    units = run["units"]
    started: dict[str, int] = {}
    steps = []
    for job in sorted(run["jobs"], key=lambda j: (j["start"], j["id"])):
        unit = job["unit"]
        started[unit] = started.get(unit, 0) + 1
        (_, owner), *helpers = job["joins"]
        steps.append({
            "n": len(steps) + 1,
            "unit": unit,
            "display": units.get(unit, {}).get("display") or unit,
            "occurrence": started[unit],
            "builder": labels.get(owner, f"builder {owner}"),
            "helpers": [{"builder": labels.get(b, f"builder {b}"), "t": t} for t, b in helpers],
            "start": job["start"],
            "finish": job["end"],
        })
    ev = run["evaluation"]
    return {
        "format": FORMAT,
        "version": VERSION,
        "name": run["goal"]["name"],
        "label": run["label"],
        "commander": run["goal"]["config"]["commander"],
        "targets": run["goal"]["targets"],
        "finish_time": ev.get("finish_time"),
        "cost": ev.get("cost"),
        "steps": steps,
        # [t, metal, energy, metal income, energy income] every SAMPLE_EVERY seconds
        "resources": resource_samples(run["segments"], run["end_time"] or 0.0),
    }


def resource_samples(segments: dict, end_time: float, every: float = SAMPLE_EVERY) -> list[list[float]]:
    t0s, t1s = segments["t0"], segments["t1"]
    if not t0s:
        return []
    out = []
    t = 0.0
    while t <= end_time + 1e-9:
        i = max(0, min(len(t0s) - 1, bisect.bisect_right(t0s, t) - 1))
        span = t1s[i] - t0s[i]
        a = 0.0 if span <= 0 else min(1.0, max(0.0, (t - t0s[i]) / span))
        metal = segments["metal0"][i] + a * (segments["metal1"][i] - segments["metal0"][i])
        energy = segments["energy0"][i] + a * (segments["energy1"][i] - segments["energy0"][i])
        out.append([round(t, 1), round(metal, 1), round(energy, 1),
                    round(segments["metal_income"][i], 2), round(segments["energy_income"][i], 2)])
        t += every
    return out


# ---------------------------------------------------------------- BAR data folder

WIDGET_SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "widget", "gui_build_guide.lua")
LOG_PATTERN = re.compile(r"^build_guide_log[\w-]*\.json$")


def find_bar_data() -> str | None:
    """BAR's data folder (the one with LuaUI/ and infolog.txt): $BAR_DATA_DIR, else the
    default Windows install location."""
    candidates = [os.environ.get("BAR_DATA_DIR")]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, "Programs", "Beyond-All-Reason", "data"))
    for path in candidates:
        if path and os.path.isdir(os.path.join(path, "LuaUI")):
            return path
    return None


def install(plan: dict, data_dir: str) -> list[str]:
    """Copy the widget and write the plan into a BAR data folder. Returns the files written."""
    widgets = os.path.join(data_dir, "LuaUI", "Widgets")
    config = os.path.join(data_dir, "LuaUI", "Config")
    os.makedirs(widgets, exist_ok=True)
    os.makedirs(config, exist_ok=True)
    widget = os.path.join(widgets, os.path.basename(WIDGET_SOURCE))
    shutil.copyfile(WIDGET_SOURCE, widget)
    plan_path = os.path.join(config, PLAN_FILE)
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f)
    return [widget, plan_path]


def list_logs(data_dir: str) -> list[dict]:
    """Game logs the widget wrote, newest first."""
    config = os.path.join(data_dir, "LuaUI", "Config")
    if not os.path.isdir(config):
        return []
    logs = [{"name": n, "modified": os.path.getmtime(os.path.join(config, n))}
            for n in os.listdir(config) if LOG_PATTERN.match(n)]
    return sorted(logs, key=lambda log: -log["modified"])


def read_log(data_dir: str, name: str) -> dict:
    if not LOG_PATTERN.match(name):
        raise ValueError(f"not a guide log: {name}")
    with open(os.path.join(data_dir, "LuaUI", "Config", name), encoding="utf-8") as f:
        return json.load(f)
