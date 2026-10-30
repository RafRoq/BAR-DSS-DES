"""What does a T1 army cost in tech time?

    python analyze_army_cost.py                              # T2 vehicle plant, Pounder + Wolverine 75/25
    python analyze_army_cost.py --target coravp,corfus       # T2 vehicle plant and a fusion reactor
    python analyze_army_cost.py --mix corak:1 --target coralab --sizes 0,500,1000

For each army size and deadline, the optimizer finds the fastest build that reaches the tech
target with that army standing by the deadline; the report shows when the tech arrives and
how much later than with no army. Units are never lost in the simulator, so "army by the
deadline" is also "army kept": losses you rebuild cost like a bigger army. Writes
results/economy/army_cost_<target>.md and .json.
"""

import argparse
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor

from bar_opt.config import EconConfig
from bar_opt.data import load_units
from bar_opt.economy import ENERGY_PER_METAL
from bar_opt.goal import Goal, Milestone, parse_time
from bar_opt.search import SearchConfig, optimize, replay_ops


def unit_cost(name: str) -> float:
    u = load_units()[name]
    return u.metal + u.energy / ENERGY_PER_METAL


def army_counts(mix: dict[str, float], size: float) -> dict[str, int]:
    """Whole units of each type in the mix, split by metal share, close to `size` metal."""
    total = sum(mix.values())
    counts = {name: round(size * share / total / unit_cost(name)) for name, share in mix.items()}
    return {name: n for name, n in counts.items() if n > 0}


def clock(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def run(task: dict) -> dict:
    """Optimize one (army, deadline) case and measure it."""
    counts, targets = task["counts"], task["targets"]
    goal = Goal(name="army cost", targets=dict(targets), max_time=task["max_time"],
                config=EconConfig(mex_spots=task["mex_spots"]),
                milestones=(Milestone(at=task["deadline"], units=counts),) if counts else ())
    search = SearchConfig(improve_seconds=task["improve"], seed=task["seed"])
    result = optimize(goal, search)
    if result is None:
        return {**task, "reached": False}
    best = replay_ops(goal, result.ops)
    ev = goal.evaluate(best)
    tech = max(best.reached[u][n - 1] for u, n in targets.items())
    return {**task, "reached": ev["reached"], "cost": ev["cost"], "tech_time": tech, "finish_time": ev["finish_time"],
            "army_time": ev["milestone_times"][0] if counts else None, "late": ev["milestones_late"],
            "stall_seconds": ev["stall_seconds"], "metal_income": best.metal_income(),
            "energy_income": best.energy_income(),
            "army_metal": sum(unit_cost(u) * n for u, n in counts.items())}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", default="coravp", help="tech units to reach, comma separated (unit or unit:n)")
    parser.add_argument("--mix", default="corlevlr:0.75,corwolv:0.25", help="army units and metal shares")
    parser.add_argument("--sizes", default="0,500,1000,1500,2000,3000", help="army metal to have by the deadline")
    parser.add_argument("--deadlines", default="3:00,4:00,5:00")
    parser.add_argument("--mex-spots", type=int, default=EconConfig().mex_spots)
    parser.add_argument("--improve", type=float, default=30.0,
                        help="seconds of polishing per optimizer run; long goals (a fusion at 10:00) need about 60")
    parser.add_argument("--repeats", type=int, default=1,
                        help="optimizer runs per case with different seeds, keeping the best; 3 steadies long goals")
    parser.add_argument("--max-time", type=float, default=1500.0)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--out", default="results/economy")
    args = parser.parse_args()

    units = load_units()
    targets = {}
    for part in args.target.split(","):
        name, _, n = part.strip().partition(":")
        targets[name] = int(n or 1)
    mix = {}
    for part in args.mix.split(","):
        name, _, share = part.strip().partition(":")
        mix[name] = float(share or 1)
    for name in [*targets, *mix]:
        if name not in units:
            parser.error(f"unknown unit {name}")
    sizes = [float(s) for s in args.sizes.split(",")]
    deadlines = [parse_time(d.strip()) for d in args.deadlines.split(",")]

    base = {"targets": targets, "mex_spots": args.mex_spots, "improve": args.improve, "max_time": args.max_time}
    cases = [{**base, "size": 0.0, "deadline": None, "counts": {}}]
    cases += [{**base, "size": s, "deadline": d, "counts": army_counts(mix, s)}
              for d in deadlines for s in sizes if s > 0]
    tasks = [{**case, "case": i, "seed": seed} for i, case in enumerate(cases) for seed in range(args.repeats)]
    started = time.perf_counter()
    with ProcessPoolExecutor(min(args.workers, len(tasks))) as pool:
        runs = list(pool.map(run, tasks))
    took = time.perf_counter() - started
    # the best of each case's runs: a search that took a poor path only ever comes out slower
    results = [min((r for r in runs if r["case"] == i), key=lambda r: (not r.get("reached"), r.get("cost", 0.0)))
               for i in range(len(cases))]
    baseline = [r["tech_time"] for r in runs if r["case"] == 0 and r.get("reached")]

    none = results[0]
    target_text = " + ".join(f"{n}x {units[u].display_name}" if n > 1 else units[u].display_name
                             for u, n in targets.items())
    mix_text = " + ".join(f"{units[u].display_name} {share / sum(mix.values()):.0%}" for u, share in mix.items())
    lines = [f"# What an army costs in tech time: {target_text}", "",
             f"Army: {mix_text} (by metal), {args.mex_spots} metal spots, default map. For each army size and "
             f"deadline the optimizer finds the fastest build that has the army by the deadline and reaches "
             f"{target_text}. Units are never lost here, so this is also the cost of keeping the army; "
             "rebuilding losses costs like a bigger army.", "",
             f"**With no army: {target_text} at {clock(none.get('tech_time'))}**, income then "
             f"{none.get('metal_income', 0):.1f} metal/s and {none.get('energy_income', 0):.0f} energy/s.", ""]
    if len(baseline) > 1:
        lines += [f"Each case is the best of {args.repeats} optimizer runs. The runs with no army ended "
                  f"{max(baseline) - min(baseline):.0f} s apart: smaller differences between cases are noise.", ""]
    for d in deadlines:
        lines += [f"## Army by {clock(d)}", "",
                  "| Army (metal) | Units | Tech at | Later by | Per 100 metal | Army ready | Late | "
                  "Income then (M/s) |", "|---|---|---|---|---|---|---|---|"]
        for r in results:
            if r["deadline"] != d:
                continue
            units_text = ", ".join(f"{n} {units[u].display_name}" for u, n in r["counts"].items())
            if not r.get("reached"):
                lines.append(f"| {r['size']:.0f} | {units_text} | not reached | | | | | |")
                continue
            later = r["tech_time"] - none["tech_time"]
            per_100 = later / r["army_metal"] * 100 if r["army_metal"] else 0.0
            lines.append(f"| {r['army_metal']:.0f} | {units_text} | {clock(r['tech_time'])} | {later:+.0f} s | "
                         f"{per_100:.1f} s | {clock(r['army_time'])} | {r['late']:.0f} s | "
                         f"{r['metal_income']:.1f} |")
        lines.append("")
    lines += ["**Later by** compares with no army. **Per 100 metal** is that delay per 100 metal of army. "
              "**Late** is how far the army missed its deadline (the optimizer trades 1 s of tech for 0.5 s "
              "of lateness, see the `late` weight). The optimizer polishes each case for "
              f"{args.improve:g} s, so differences of a few seconds are noise.", "",
              f"_{len(tasks)} optimizer runs ({len(cases)} cases) in {took:.0f} s._"]

    os.makedirs(args.out, exist_ok=True)
    stem = os.path.join(args.out, "army_cost_" + "+".join(targets))
    with open(stem + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(stem + ".json", "w", encoding="utf-8") as f:
        json.dump({"targets": targets, "mix": mix, "mex_spots": args.mex_spots, "results": results}, f, indent=1)
    print("\n".join(lines))
    print(f"\nWritten to {stem}.md")


if __name__ == "__main__":
    main()
