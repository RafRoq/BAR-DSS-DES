"""How fast does each economy building pay for itself, and what does army metal cost in income?

    python analyze_payback.py                     # Cortex, on the default map settings
    python analyze_payback.py --commander armcom --wind 8

Writes results/economy/payback_<faction>.md and .json. Energy is counted in metal at the
T1 converter rate (70:1); see bar_opt/economy.py.
"""

import argparse
import json
import os
from dataclasses import asdict

from bar_opt.config import EconConfig
from bar_opt.data import load_units
from bar_opt.economy import ENERGY_PER_METAL, payback_table

FACTIONS = {"arm": "Armada", "cor": "Cortex", "leg": "Legion"}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commander", default="corcom")
    parser.add_argument("--wind", type=float, help="average wind (default: config)")
    parser.add_argument("--metal-per-spot", type=float, help="metal/s of a T1 extractor (default: config)")
    parser.add_argument("--out", default="results/economy")
    args = parser.parse_args()

    overrides = {"commander": args.commander}
    if args.wind is not None:
        overrides["wind_average"] = args.wind
    if args.metal_per_spot is not None:
        overrides["metal_per_spot"] = args.metal_per_spot
    config = EconConfig(**overrides)
    rows = payback_table(load_units(), config)
    faction = FACTIONS.get(config.commander[:3], config.commander)

    lines = [f"# Economy payback: {faction}", "",
             f"Map: wind {config.wind_average:g}, tidal {config.tidal_strength:g}, "
             f"{config.metal_per_spot:g} metal/s per T1 extractor. Energy counted as metal at "
             f"{ENERGY_PER_METAL:g}:1 (the T1 converter rate). **Payback** = cost / what it adds per second: "
             "how long it must run to earn its cost back. **From order** adds the build time with the "
             "fastest land builder that can build it alone; assisting shortens that part.", "",
             "| Building | Tech | Cost (metal) | Adds | Payback | Build | From order | Notes |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        adds = " ".join(part for part in (f"{r.metal:+.2f} M/s" if abs(r.metal) > 1e-9 else "",
                                          f"{r.energy:+.1f} E/s" if abs(r.energy) > 1e-9 else "") if part)
        payback = f"{r.payback:.0f} s" if r.payback is not None else "never"
        total = f"{r.build_seconds + r.payback:.0f} s" if r.payback is not None else "—"
        notes = "; ".join(n for n in (f"needs {r.needs}" if r.needs else "", r.note) if n)
        lines.append(f"| {r.display} (`{r.unit}`) | T{r.tech} | {r.cost:.0f} | {adds} | {payback} | "
                     f"{r.build_seconds:.0f} s ({r.builder}) | {total} | {notes} |")

    # What army metal costs: the income the same metal would have bought in the best building
    # you can put anywhere (no metal spot, vent or water needed), at each tech level.
    anywhere = [r for r in rows if r.payback is not None and not r.needs]
    lines += ["", "## What army metal costs in income", "",
              "A unit never pays back. The same metal in the best economy building you can put anywhere "
              "(no metal spot, geothermal vent or water needed) would have added this much income for the "
              "rest of the game:", "",
              "| Instead of 1000 metal of army | Pays back in | Income forgone | Over 5 minutes |",
              "|---|---|---|---|"]
    for tech in (1, 2):
        options = [r for r in anywhere if r.tech == tech]
        if not options:
            continue
        best = min(options, key=lambda r: r.payback)
        per_second = 1000 / best.payback
        lines.append(f"| T{tech}: {best.display} | {best.payback:.0f} s | {per_second:.1f} metal/s | "
                     f"{per_second * 300:.0f} metal |")
    lines += ["", "Metal spots come first (extractors pay back in under half a minute); this is the price "
              "once they are taken. It ignores what the army protects or kills, which is the reason to "
              "build one."]

    os.makedirs(args.out, exist_ok=True)
    stem = os.path.join(args.out, f"payback_{config.commander[:3]}")
    with open(stem + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(stem + ".json", "w", encoding="utf-8") as f:
        json.dump({"config": asdict(config), "energy_per_metal": ENERGY_PER_METAL,
                   "rows": [{**asdict(r), "cost": r.cost, "value": r.value, "payback": r.payback} for r in rows]},
                  f, indent=1)
    print("\n".join(lines))
    print(f"\nWritten to {stem}.md")


if __name__ == "__main__":
    main()
