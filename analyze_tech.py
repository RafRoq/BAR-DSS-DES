"""How much is T1 army worth once the enemy has T2?

    python analyze_tech.py                       # Cortex, 3000-metal armies, against all factions
    python analyze_tech.py --faction arm --budget 5000 --vs cor

Finds your most robust T1 army (single types and 50/50 pairs, as analyze_units.py), then
fights it against every T2 army each enemy faction can field (single types and 50/50 pairs)
at equal cost, tight and spread out, and measures how much more metal it needs to beat the
best of them. Also fights each of your T1 and T2 units against every enemy T1 and T2 unit.
Writes results/units/tech_<faction>_<budget>.md and .json. Same fight model and caveats as
analyze_units.py: a first filter, not a verdict.
"""

import argparse
import json
import math
import os
import statistics
import time
from concurrent.futures import ProcessPoolExecutor

from bar_opt.combat import (BLOB, FACTIONS, SPREAD, army, compositions, even_armies, fight, ground_army,
                            load_combat, mix_label, solve_zero_sum)

EXCHANGE_RANGE = (0.5, 6.0)  # multipliers of the enemy's cost tried for the exchange rate
EXCHANGE_STEPS = 7

_units = None


def _init():
    global _units
    _units = load_combat()


def _score(a_mix: dict, b_mix: dict, budget: float) -> float:
    """Mean score of `a_mix` against `b_mix` at equal cost, tight and spread out."""
    a, b = even_armies(_units, a_mix, b_mix, budget)
    return (fight(a, b, spacing=BLOB).score + fight(a, b, spacing=SPREAD).score) / 2


def _pair(task):
    mine, enemy, budget = task
    return _score(mine, enemy, budget)


def _exchange(task):
    """How many times the enemy army's cost `mix` needs to beat it: bisection on a log scale,
    returning the actual cost ratio of the smallest winning army found. None if even the most
    tried still loses; the low end of the range if the least already wins."""
    mix, enemy_mix, budget = task
    theirs = army(_units, enemy_mix, budget)
    cost = sum(u.cost for u in theirs)

    def ratio_if_wins(k: float) -> float | None:
        mine = army(_units, mix, k * cost)
        won = (fight(mine, theirs, spacing=BLOB).score + fight(mine, theirs, spacing=SPREAD).score) / 2 > 0
        return sum(u.cost for u in mine) / cost if won else None

    lo, hi = EXCHANGE_RANGE
    if ratio_if_wins(lo) is not None:
        return lo
    best = ratio_if_wins(hi)
    if best is None:
        return None
    for _ in range(EXCHANGE_STEPS):
        mid = math.sqrt(lo * hi)
        ratio = ratio_if_wins(mid)
        if ratio is not None:
            hi, best = mid, min(best, ratio)
        else:
            lo = mid
    return best


def matrix(pool, rows: list[dict], cols: list[dict], budget: float) -> list[list[float]]:
    """Scores of each army mix in `rows` against each in `cols`."""
    flat = list(pool.map(_pair, [(r, c, budget) for r in rows for c in cols], chunksize=4))
    return [flat[i * len(cols):(i + 1) * len(cols)] for i in range(len(rows))]


def single(names: list[str]) -> list[dict]:
    return [{n: 1.0} for n in names]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--faction", default="cor", choices=sorted(FACTIONS))
    parser.add_argument("--vs", default="arm,cor,leg", help="enemy factions, comma separated")
    parser.add_argument("--budget", type=float, default=3000.0, help="army cost (metal + energy/70) per side")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--out", default="results/units")
    args = parser.parse_args()

    units = load_combat()
    vs = [f.strip() for f in args.vs.split(",")]
    mine = {t: ground_army(units, args.faction, t) for t in (1, 2)}
    enemy = {t: [n for f in vs for n in ground_army(units, f, t)] for t in (1, 2)}

    my_comps = {t: compositions(mine[t], max_types=2) for t in (1, 2)}
    their_t2 = {f: compositions(ground_army(units, f, 2), max_types=2) for f in vs}
    started = time.perf_counter()
    with ProcessPoolExecutor(args.workers, initializer=_init) as pool:
        # Each of my units against each enemy unit
        m = {(a, b): matrix(pool, single(mine[a]), single(enemy[b]), args.budget) for a in (1, 2) for b in (1, 2)}
        # My most robust army at each tech: the zero-sum game of my compositions against the
        # enemy units of the same tech, folded into one army (unit shares weighted by the game)
        robust = {}
        for t in (1, 2):
            game = matrix(pool, my_comps[t], single(enemy[t]), args.budget)
            weights, _, _ = solve_zero_sum(game)
            folded = {}
            for comp, w in zip(my_comps[t], weights):
                for n, share in comp.items():
                    folded[n] = folded.get(n, 0.0) + w * share / sum(comp.values())
            robust[t] = {n: w for n, w in sorted(folded.items(), key=lambda x: -x[1]) if w > 0.01}
        # Both armies against every T2 army each enemy faction can field
        vs_t2 = {(t, f): matrix(pool, [robust[t]], their_t2[f], args.budget)[0] for t in (1, 2) for f in vs}
        hardest = {k: min(range(len(row)), key=row.__getitem__) for k, row in vs_t2.items()}
        keys = list(hardest)
        rates = dict(zip(keys, pool.map(_exchange, [(robust[t], their_t2[f][hardest[t, f]], args.budget)
                                                      for t, f in keys])))
    took = time.perf_counter() - started

    def rate_text(r):
        if r is None:
            return f"more than {EXCHANGE_RANGE[1]:g}x"
        return f"{EXCHANGE_RANGE[0]:g}x or less" if r == EXCHANGE_RANGE[0] else f"{r:.1f}x"

    def shares(mix):
        return ", ".join(f"{units[n].display} {w:.0%}" for n, w in mix.items())

    lines = [f"# T1 against T2: {FACTIONS[args.faction]}, {args.budget:.0f}-metal armies", "",
             f"Your most robust T1 army is **{shares(robust[1])}**, and your most robust T2 army "
             f"**{shares(robust[2])}** (by metal; found as in analyze_units.py, against enemy units of the same "
             "tech). Each fights every T2 army an enemy faction can field (every single type and 50/50 pair of "
             "its T2 bot lab and vehicle plant) at equal cost, tight and spread out. Score from -1 to +1: value "
             "left alive minus the enemy's.", "",
             "- **Typical T2 army:** the median score over all of that faction's T2 armies.",
             "- **Their best T2 army:** the one that beats yours hardest, its score at equal cost, and how many "
             f"times its cost your army needs to beat it (searched from {EXCHANGE_RANGE[0]:g}x to "
             f"{EXCHANGE_RANGE[1]:g}x).", "",
             "| Your army | Enemy | Typical T2 army | Their best T2 army | Score | You need |",
             "|---|---|---|---|---|---|"]
    for t in (1, 2):
        for f in vs:
            row = vs_t2[t, f]
            best = their_t2[f][hardest[t, f]]
            lines.append(f"| T{t} | {FACTIONS[f]} | {statistics.median(row):+.2f} | {mix_label(units, best)} | "
                         f"{row[hardest[t, f]]:+.2f} | {rate_text(rates[t, f])} |")

    lines += ["", "## Your units", "",
              "Average score of each of your units against enemy T1 and enemy T2 at equal cost.", "",
              "| Unit | Tech | Cost | vs enemy T1 | vs enemy T2 | Change |", "|---|---|---|---|---|---|"]
    for a in (1, 2):
        rows = sorted(range(len(mine[a])), key=lambda i: -statistics.fmean(m[a, 2][i]))
        for i in rows:
            n = mine[a][i]
            v1, v2 = statistics.fmean(m[a, 1][i]), statistics.fmean(m[a, 2][i])
            lines.append(f"| {units[n].display} (`{n}`) | T{a} | {units[n].cost:.0f} | {v1:+.2f} | {v2:+.2f} | "
                         f"{v2 - v1:+.2f} |")
    fights = 2 * (sum(len(mine[a]) * len(enemy[b]) for a in (1, 2) for b in (1, 2))
                  + sum(len(my_comps[t]) * len(enemy[t]) for t in (1, 2))
                  + 2 * sum(len(c) for c in their_t2.values()))
    lines += ["", f"_{fights} equal-cost fights and {len(rates)} exchange-rate searches in {took:.0f} s. "
              "Not modelled: micro, terrain, air, shields, cloak, stun weapons, veterancy._"]

    os.makedirs(args.out, exist_ok=True)
    stem = os.path.join(args.out, f"tech_{args.faction}_{args.budget:.0f}")
    with open(stem + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(stem + ".json", "w", encoding="utf-8") as f:
        json.dump({"faction": args.faction, "budget": args.budget, "vs": vs,
                   "mine": {f"t{t}": mine[t] for t in (1, 2)}, "enemy": {f"t{t}": enemy[t] for t in (1, 2)},
                   "scores": {f"t{a}_vs_t{b}": m[a, b] for a in (1, 2) for b in (1, 2)},
                   "robust": {f"t{t}": robust[t] for t in (1, 2)},
                   "vs_enemy_t2": {f"t{t}_vs_{f}": {"armies": their_t2[f], "scores": vs_t2[t, f],
                                                    "hardest": their_t2[f][hardest[t, f]],
                                                    "exchange_rate": rates[t, f]}
                                   for t in (1, 2) for f in vs},
                   "exchange_range": EXCHANGE_RANGE}, f, indent=1)
    print("\n".join(lines[:lines.index("## Your units")]))
    print(f"\nFull report: {stem}.md")


if __name__ == "__main__":
    main()
