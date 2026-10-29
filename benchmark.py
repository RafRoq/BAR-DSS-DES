"""Run the optimizer on a fixed set of goals and print one table, to compare search changes.

    python benchmark.py                 # all goals
    python benchmark.py armada coralab  # only goals whose name contains one of these words
    python benchmark.py --beam 60       # override the beam width
"""

import argparse
import time

from bar_opt.goal import Goal
from bar_opt.search import SearchConfig, optimize
from optimize import goal_from_recipe

GOALS = {
    "armada_bot": lambda: Goal.from_yaml("goals/armada_bot.yaml"),
    "coralab": lambda: Goal.from_yaml("goals/coralab.yaml"),
    "cortex_air": lambda: goal_from_recipe("recipes/cortex_air.yaml"),
    "cortex_sea": lambda: goal_from_recipe("recipes/cortex_sea.yaml"),
    "t2_fusion": lambda: Goal(name="corack + fusion", targets={"corack": 1, "corfus": 1}, max_time=1500),
    "t3_gantry": lambda: Goal(name="experimental gantry", targets={"corgant": 1}, max_time=1800),
    "t3_juggernaut": lambda: Goal(name="juggernaut", targets={"corkorg": 1}, max_time=2400),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("only", nargs="*", help="substrings of goal names to run")
    parser.add_argument("--beam", type=int, default=150)
    parser.add_argument("--improve", type=float, default=None, help="improvement time budget in seconds")
    args = parser.parse_args()

    config = SearchConfig(beam_width=args.beam)
    if args.improve is not None:
        config.improve_seconds = args.improve
    print(f"{'goal':15} {'reached':>9} {'stall s':>8} {'overflow M/E':>14} {'idle BP-s':>10} {'cost':>9} {'search s':>9}")
    for name, make in GOALS.items():
        if args.only and not any(word in name for word in args.only):
            continue
        goal = make()
        started = time.perf_counter()
        result = optimize(goal, config)
        took = time.perf_counter() - started
        if result is None:
            print(f"{name:15} {'no build':>9} {'':>8} {'':>14} {'':>10} {'':>9} {took:9.1f}")
            continue
        ev = result.evaluation
        print(f"{name:15} {ev['finish_time']:9.1f} {ev['stall_seconds']:8.1f} "
              f"{ev['overflow_metal']:6.0f}/{ev['overflow_energy']:<7.0f} {ev['idle_bp_seconds']:10.0f} "
              f"{ev['cost']:9.1f} {took:9.1f}", flush=True)


if __name__ == "__main__":
    main()
