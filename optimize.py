"""Search the best build order for a goal and compare it with a hand-written one.

    python optimize.py goals/coralab.yaml
    python optimize.py goals/armada_bot.yaml --compare recipes/armada_bot.yaml
    python optimize.py --from-recipe recipes/cortex_air.yaml --no-show

--from-recipe uses the units a hand-written recipe ends with as the goal and compares
against that recipe. Results go to results/<name>/: optimized.yaml (interface.py recipe
format), cookbook.txt (timed schedule with builder ids), graph PNGs and comparison.txt.
"""

import argparse
import importlib
import os
import time

import yaml

from bar_opt.config import EconConfig
from bar_opt.goal import Goal
from bar_opt.recipe import load_yaml_recipe, replay, save_yaml, to_yaml_steps
from bar_opt.report import cookbook, plot_run
from bar_opt.search import SearchConfig, optimize, replay_ops

ROWS = [
    ("Goal reached at (s)", "finish_time", "{:.1f}"),
    ("Stall (s with s<1)", "stall_seconds", "{:.1f}"),
    ("Stall (lost BP-s)", "stall_bp_seconds", "{:.0f}"),
    ("Overflow metal", "overflow_metal", "{:.0f}"),
    ("Overflow energy", "overflow_energy", "{:.0f}"),
    ("Idle build power (BP-s)", "idle_bp_seconds", "{:.0f}"),
    ("Weighted cost", "cost", "{:.1f}"),
]


def load_recipe(spec: str):
    if spec.endswith((".yaml", ".yml")):
        return load_yaml_recipe(spec)
    module, _, name = spec.partition(":")
    return getattr(importlib.import_module(module), name)


def comparison_table(columns: dict[str, dict]) -> str:
    names = list(columns)
    width = max(14, *(len(n) for n in names))
    lines = ["".ljust(26) + "".join(n.rjust(width + 2) for n in names)]
    for label, key, fmt in ROWS:
        cells = []
        for n in names:
            value = columns[n][key]
            cells.append(("not reached" if value == float("inf") else fmt.format(value)).rjust(width + 2))
        lines.append(label.ljust(26) + "".join(cells))
    return "\n".join(lines)


def goal_from_recipe(spec: str) -> Goal:
    """Goal = everything the recipe has built when it finishes, on a map with enough spots."""
    recipe = load_recipe(spec)
    probe = EconConfig(mex_spots=1000)
    state, problems = replay(recipe, probe, record=False)
    if problems:
        raise SystemExit(f"{spec}: {problems[0]}")
    targets = {u: n for u, n in state.counts.items() if n > 0 and u != probe.commander}
    mexes = sum(n for u, n in targets.items() if state.units[u].is_mex)
    config = EconConfig(mex_spots=max(EconConfig().mex_spots, mexes))
    return Goal(name=f"end state of {spec}", targets=targets, max_time=max(900.0, 2 * state.time), config=config)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("goal", nargs="?", help="goal YAML file")
    parser.add_argument("--from-recipe", help="use the end state of this recipe as the goal and compare with it")
    parser.add_argument("--beam", type=int, help="beam width (default: goal file's search.beam_width or 150)")
    parser.add_argument("--compare", help="hand-written recipe: a YAML file or module:VARIABLE")
    parser.add_argument("--out", default="results", help="output directory")
    parser.add_argument("--no-show", action="store_true", help="save graphs without opening them")
    args = parser.parse_args()

    if args.from_recipe:
        goal, search = goal_from_recipe(args.from_recipe), SearchConfig()
        args.compare = args.compare or args.from_recipe
        name = os.path.splitext(os.path.basename(args.from_recipe))[0]
    elif args.goal:
        goal = Goal.from_yaml(args.goal)
        with open(args.goal, encoding="utf-8") as f:
            search = SearchConfig.from_dict(yaml.safe_load(f).get("search"))
        name = os.path.splitext(os.path.basename(args.goal))[0]
    else:
        parser.error("give a goal file or --from-recipe")
    if args.beam:
        search.beam_width = args.beam
    out = os.path.join(args.out, name)
    os.makedirs(out, exist_ok=True)

    print(f"Optimizing '{goal.name}' (targets {goal.targets}), beam width {search.beam_width}...")
    started = time.perf_counter()
    result = optimize(goal, search)
    if result is None:
        print(f"No build reaches the goal within {goal.max_time:.0f}s.")
        return
    print(f"Search took {time.perf_counter() - started:.1f}s ({result.nodes_expanded} nodes).")

    best = replay_ops(goal, result.ops)
    columns = {"optimizer": goal.evaluate(best)}
    save_yaml(to_yaml_steps(best), os.path.join(out, "optimized.yaml"))
    with open(os.path.join(out, "cookbook.txt"), "w", encoding="utf-8") as f:
        f.write(cookbook(best, f"{goal.name}: optimizer"))

    if args.compare:
        hand, problems = replay(load_recipe(args.compare), goal.config, max_time=goal.max_time,
                                stop_when=goal.satisfied)
        for p in problems:
            print("hand-written:", p)
        columns["hand-written"] = goal.evaluate(hand)
        if not columns["hand-written"]["reached"]:
            print(f"hand-written: goal not reached, missing {goal.missing(hand)}")
        with open(os.path.join(out, "cookbook_hand.txt"), "w", encoding="utf-8") as f:
            f.write(cookbook(hand, f"{goal.name}: {args.compare}"))
        plot_run(hand, f"{goal.name} - hand-written ({args.compare})",
                 os.path.join(out, "graph_hand.png"), show=not args.no_show)

    table = comparison_table(columns)
    print("\n" + table)
    with open(os.path.join(out, "comparison.txt"), "w", encoding="utf-8") as f:
        f.write(f"{goal.name}  targets={goal.targets}  beam={search.beam_width}\n\n{table}\n")
    plot_run(best, f"{goal.name} - optimizer", os.path.join(out, "graph_optimizer.png"), show=not args.no_show)
    print(f"\nResults written to {out}/")


if __name__ == "__main__":
    main()
