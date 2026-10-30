"""Live companion: re-plans your build from the running game every few seconds.

    python companion.py                       # goal goals/coralab.yaml, BAR folder found automatically
    python companion.py goals/my_goal.yaml
    python companion.py --bar-data D:/BAR/data

Start it next to BAR (before or during a game) with the Build Order Guide widget enabled.
The widget writes the game state to LuaUI/Config/build_guide_state.json; this program
rebuilds that moment in the simulator, plans to the goal and writes the next steps to
LuaUI/Config/build_guide_live.json, which the widget shows. Stop it with Ctrl+C.

The wind used for planning is the average seen in the game so far, blended with the goal's
wind_average early on.
"""

import argparse
import json
import os
import sys
import time

from bar_opt import guide
from bar_opt.companion import LIVE_FILE, STATE_FILE, WindEstimate, plan_live
from bar_opt.goal import Goal


def read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None  # missing, or caught mid-write: try again on the next poll


def write_json(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)  # the widget never sees a half-written file


def clock(t: float | None) -> str:
    return "--:--" if t is None else f"{int(t // 60)}:{int(t % 60):02d}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("goal", nargs="?", default="goals/coralab.yaml", help="goal YAML file")
    parser.add_argument("--bar-data", help="BAR data folder (default: $BAR_DATA_DIR or the standard install)")
    parser.add_argument("--width", type=int, default=40, help="beam width per re-plan")
    args = parser.parse_args()

    data_dir = args.bar_data or guide.find_bar_data()
    if not data_dir:
        sys.exit("BAR data folder not found: pass --bar-data or set BAR_DATA_DIR")
    config_dir = os.path.join(data_dir, "LuaUI", "Config")
    os.makedirs(config_dir, exist_ok=True)
    state_path, live_path = os.path.join(config_dir, STATE_FILE), os.path.join(config_dir, LIVE_FILE)

    goal = Goal.from_yaml(args.goal)
    print(f"Companion for '{goal.name}' {goal.targets}. Watching {state_path}  (Ctrl+C to stop)")
    wind, template, last, template_time = WindEstimate(goal.config.wind_average), None, None, 0.0
    try:
        while True:
            time.sleep(0.25)
            try:
                stamp = os.stat(state_path).st_mtime_ns
            except OSError:
                continue
            if stamp == last:
                continue
            snapshot = read_json(state_path)
            if not snapshot or snapshot.get("format") != "bar-build-guide-state":
                continue
            last = stamp
            if template is not None and snapshot.get("time", 0) < template_time - 1:
                template, wind = None, WindEstimate(goal.config.wind_average)  # a new game started
            try:
                live, template = plan_live(goal, snapshot, wind=wind.update(snapshot), width=args.width,
                                           template=template)
            except Exception as e:  # keep running through odd snapshots; show what happened
                print(f"  {clock(snapshot.get('time'))}  could not plan: {type(e).__name__}: {e}")
                continue
            template_time = snapshot.get("time", 0)
            write_json(live_path, live)
            first = next((s for s in live["steps"] if s["action"] == "build"), None)
            what = "goal reached" if live.get("done") else (
                f"ETA {clock(live['eta'])} ({live.get('source')}, {live.get('planned_in', 0):.2f}s, "
                f"wind {live['wind_used']:.1f})" + (f"  next: {first['display']} by {first['builder']}" if first else ""))
            print(f"  {clock(snapshot['time'])}  {what}", flush=True)
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
