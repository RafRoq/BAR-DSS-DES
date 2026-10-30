"""Which early units and unit mixes are worth building? A numbers-first balance analysis.

    python analyze_units.py                  # Cortex, 1500-metal armies, against all factions
    python analyze_units.py --faction arm --budget 3000
    python analyze_units.py --vs cor         # only against Cortex units

Writes results/units/<faction>_<budget>.md and .json and prints a summary. See
bar_opt/combat.py for what the fight simulation models and what it leaves out; treat the
ranking as a first filter to try in game, not as a verdict.
"""

import argparse
import json
import os
import time

from bar_opt.combat import (BLOB, FACTIONS, SPREAD, army, compositions, dps, early_units, fight, lanchester,
                            load_combat, mix_label, reach, solve_zero_sum)

REFERENCE = "corak"  # damage and range are quoted against a standard T1 bot


def ground_units(units, faction):
    """Early mobile units that can hurt ground units (AA-only and builders are left out)."""
    ref = units[REFERENCE]
    return [n for n in early_units(units, faction) if dps(units[n], ref) > 0]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--faction", default="cor", choices=sorted(FACTIONS))
    parser.add_argument("--vs", default="arm,cor,leg", help="enemy factions, comma separated")
    parser.add_argument("--budget", type=float, default=1500.0, help="army cost (metal + energy/70) per side")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--out", default="results/units")
    args = parser.parse_args()

    units = load_combat()
    ref = units[REFERENCE]
    mine = ground_units(units, args.faction)
    enemies = [n for f in args.vs.split(",") for n in ground_units(units, f.strip())]
    lines = [f"# Early units: {FACTIONS[args.faction]}, {args.budget:.0f}-metal armies", ""]

    # 1. Cost efficiency
    lines += ["## Cost efficiency (Lanchester)", "",
              f"DPS and range against a standard T1 bot ({ref.display}). Cost = metal + energy/70. "
              "Lanchester = DPS x HP / cost^2 x 1000: what one unit of cost is worth in a straight fight.", "",
              "| Unit | Faction | Cost | HP | DPS | Range | Speed | Lanchester |", "|---|---|---|---|---|---|---|---|"]
    table = sorted(set(mine) | set(enemies), key=lambda n: -lanchester(units[n], ref))
    for n in table:
        u = units[n]
        lines.append(f"| {u.display} (`{n}`) | {FACTIONS[u.faction]} | {u.cost:.0f} | {u.health:.0f} | "
                     f"{dps(u, ref):.0f} | {reach(u, ref):.0f} | {u.speed:.0f} | {1000 * lanchester(u, ref):.2f} |")

    # 2. Fights: my compositions (single types and 50/50 pairs) against each enemy type, with both
    # armies in a tight block and spread out; splash weapons (artillery) swing a lot between them.
    started = time.perf_counter()
    mixes = compositions(mine, max_types=2)

    def scores(mix: dict, spacing: float) -> list[float]:
        mine_army = army(units, mix, args.budget)
        return [fight(mine_army, army(units, {e: 1.0}, args.budget), spacing=spacing).score for e in enemies]

    blob = [scores(mix, BLOB) for mix in mixes]
    spread = [scores(mix, SPREAD) for mix in mixes]
    matrix = [[(x + y) / 2 for x, y in zip(rb, rs)] for rb, rs in zip(blob, spread)]
    took = time.perf_counter() - started

    # 3. Robust mixes: the zero-sum game of my compositions against enemy unit types
    row_mix, enemy_mix, value = solve_zero_sum(matrix)
    vs_best = [sum(m * w for m, w in zip(row, enemy_mix)) for row in matrix]
    ranked = sorted(range(len(mixes)), key=lambda i: -vs_best[i])
    enemy_counters = sorted(((w, enemies[j]) for j, w in enumerate(enemy_mix) if w > 0.01), reverse=True)

    lines += ["", f"## Best {args.top} compositions", "",
              f"Every single type and every 50/50 cost split of two {FACTIONS[args.faction]} ground units, "
              f"each fought ({args.budget:.0f} vs {args.budget:.0f} metal) against every early ground unit of "
              f"{', '.join(FACTIONS[f.strip()] for f in args.vs.split(','))}, "
              f"once with both armies in a tight block ({BLOB:.0f} elmos apart) and once spread out "
              f"({SPREAD:.0f}). Score per fight: value left alive minus the enemy's, from -1 to +1; "
              "scores below are the mean of the two.",
              "",
              "- **vs best counter:** average score against the enemy's best counter-mix (the strongest "
              "enemy answer to all my options at once). This is the ranking.",
              "- **worst case:** the single enemy unit type it does worst against.",
              "- **blob / spread:** average score in each of the two situations.", "",
              "| # | Composition | vs best counter | Average | Blob | Spread | Worst case | Worst against |",
              "|---|---|---|---|---|---|---|---|"]
    for rank, i in enumerate(ranked[:args.top], 1):
        row = matrix[i]
        worst = min(range(len(enemies)), key=row.__getitem__)
        lines.append(f"| {rank} | {mix_label(units, mixes[i])} | {vs_best[i]:+.2f} | {sum(row) / len(row):+.2f} | "
                     f"{sum(blob[i]) / len(row):+.2f} | {sum(spread[i]) / len(row):+.2f} | "
                     f"{row[worst]:+.2f} | {units[enemies[worst]].display} |")
    robust = sorted(((w, i) for i, w in enumerate(row_mix) if w > 0.01), reverse=True)
    lines += ["", "**Most robust mix** (game value "
              f"{value:+.2f}): " + ", ".join(f"{mix_label(units, mixes[i])} {w:.0%}" for w, i in robust),
              "", "**Enemy units that counter you best**: "
              + ", ".join(f"{units[n].display} ({FACTIONS[units[n].faction]}) {w:.0%}" for w, n in enemy_counters)]

    # 4. Static defences: each T1 defence (same budget) holding against each enemy type
    defences = [n for n in early_units(units, args.faction, mobile=False) if dps(units[n], ref) > 0]
    lines += ["", "## Static defences", "",
              f"{args.budget:.0f} metal of one defence type holding against {args.budget:.0f} metal of each "
              "attacker; average score and the attacker that beats it most.", "",
              "| Defence | Cost | Average | Worst against |", "|---|---|---|---|"]
    defence_rows = {}
    for d in defences:
        row = [(fight(army(units, {d: 1.0}, args.budget), army(units, {e: 1.0}, args.budget), spacing=BLOB).score
                + fight(army(units, {d: 1.0}, args.budget), army(units, {e: 1.0}, args.budget), spacing=SPREAD).score) / 2
               for e in enemies]
        defence_rows[d] = row
        worst = min(range(len(enemies)), key=row.__getitem__)
        lines.append(f"| {units[d].display} (`{d}`) | {units[d].cost:.0f} | {sum(row) / len(row):+.2f} | "
                     f"{units[enemies[worst]].display} ({row[worst]:+.2f}) |")

    lines += ["", "## Not modelled", "",
              "Micro and kiting, terrain, accuracy and projectile travel time, target priorities beyond "
              "\"nearest\", formations spreading out, friendly fire, shields, cloak, air and veterancy. "
              "Armies fight as blocks walking straight at each other, so range advantages are probably "
              "overstated; splash weapons are shown for tight and spread-out armies.",
              "", f"_{len(mixes) * len(enemies) + len(defences) * len(enemies)} fights simulated in "
              f"{took:.0f} s for the compositions._"]

    os.makedirs(args.out, exist_ok=True)
    stem = os.path.join(args.out, f"{args.faction}_{args.budget:.0f}")
    with open(stem + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(stem + ".json", "w", encoding="utf-8") as f:
        json.dump({"faction": args.faction, "budget": args.budget, "enemies": enemies,
                   "compositions": [{"mix": m, "label": mix_label(units, m), "scores": row,
                                     "blob": blob[i], "spread": spread[i],
                                     "vs_best_counter": vs_best[i], "robust_weight": row_mix[i]}
                                    for i, (m, row) in enumerate(zip(mixes, matrix))],
                   "enemy_counter_mix": dict(zip(enemies, enemy_mix)), "game_value": value,
                   "defences": defence_rows}, f, indent=1)

    print("\n".join(lines[lines.index(f"## Best {args.top} compositions"):]))
    print(f"\nFull report: {stem}.md")


if __name__ == "__main__":
    main()
