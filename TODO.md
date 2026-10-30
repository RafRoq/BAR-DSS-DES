# Backlog

Open work on the optimizer, updated on 2026-10-29.

## Done (2026-10-29)

Items 1–3 of the original backlog are done:

- **T2 economy:** `cormoho`, `corfus` and `cormmkr` are in the default economy list, gated by the tech tree.
  Mohos are capped at the number of spots.
- **Expensive T3 goals:** the Juggernaut (`corkorg`) is now found.
  - The remaining-time estimate is no longer capped, and it counts factory build power.
  - Builders can pass when nothing is left to do.
  - Fusion and advanced converter caps were raised to 8 and 20.
  - The earlier diagnosis that "waiting until affordable" should account for converters was wrong. A
    converter-aware version never helped and made the sea build 60% worse, so it was dropped.
- **Better search:**
  - `exact.py`: branch-and-bound, which proves the optimum for small goals.
  - `improve.py`: step local search, suffix re-planning, and rollout (playout-guided) re-planning, with
    an early stop.
- **Cost:** the `idle` weight now applies to wasted build power only, not to idle factories or to builders
  held back while storage drains.

Benchmark (`python benchmark.py`), weighted cost with finish time in brackets:

| Goal | Start of the day | Beam only, new cost | + 30 s improvement |
|---|---|---|---|
| armada_bot | 76.9 | 74.6 (74.0 s) | 73.7 (73.7 s) |
| coralab | 405.4 | 237.6 (237.5 s) | 226.3–229.3 (226–229 s) |
| cortex_air | 363.9 | 275.6 (275.6 s) | 275.6 (stops early) |
| cortex_sea | 1435.3 | 315.4 (312.4 s) | 315.4 (stops early) |
| t2_fusion | 3985.0 | 598.1 (574.9 s) | 493.4 (493.4 s) |
| t3_gantry | 5797.9 | 754.6 (738.8 s) | 728.5–735.6 (718–733 s) |
| juggernaut | not found | 2160.8 (1390 s) | 1809.5 (1442 s) |

The start-of-day numbers used the old cost, so they aren't directly comparable.

## Toolset (2026-10-29, evening)

Built after the first in-game tests:

- **In-game guide widget** (`widget/gui_build_guide.lua`):
  - follows a plan and logs every game;
  - Mouse 4 selects the next builder with its building ready to place;
  - warns when you play another faction than the plan's.
- **Live companion** (`companion.py`): re-plans from the running game every 2 s through files in
  `LuaUI/Config`, since widgets can't run programs or open sockets. Tested against a mock of BAR's API and
  in simulation, not yet in a real game.
- **Unit analysis** (`analyze_units.py`, `bar_opt/combat.py`):
  - combat data extracted from BAR;
  - Lanchester efficiency;
  - fight simulator with projectiles, scatter and splash, run in tight and spread-out formations;
  - robust mixes from a zero-sum game.
- **Milestone goals and roles** (`goals/role_*.yaml`).

What the first games showed:
- The economy model matched the game exactly: every building took its planned time.
- The gap came from walking and giving orders: about 1 s per base building, 10–60 s per mex.
- Wind varies between about 3 and 20 on Supreme Isthmus. The companion plans with the average seen so far.

## Army against tech (2026-10-30)

- `analyze_tech.py`, `analyze_payback.py`, `analyze_army_cost.py` (see README).
- Cortex, 3000-metal armies: the most robust T1 army beats a typical T2 army (+0.5) but needs 1.3–1.7x the
  metal to beat the strongest one; the most robust T2 army needs 1.2–1.3x against its strongest counter.
- Payback: a mex 25 s, wind 224 s (at wind 14.2), moho 114 s, fusion 322 s. 1000 metal of army forgoes
  about 4.5 metal/s of income.
- Army cost: each 100 metal of army by 3:00–5:00 delays the T2 vehicle plant by 8–12 s (about what that
  metal is worth at 11 metal/s of income).

## Next

- **Long goals are unreliable.** For the T2 vehicle plant plus a fusion reactor (about 10:00), the
  optimizer's runs end up to 245 s apart even with 60 s of polishing and the best of 3 seeds, so
  `analyze_army_cost.py --target coravp,corfus` is noise. Fix before planning later tech phases: likely the
  remaining-time estimate or the economy caps for T2. Measure with `benchmark.py` on that goal.
- **Try the companion in a real game.** Compare its ETA with the outcome, and check that the plan doesn't
  jump around while it's being followed.
- **Validate the unit analysis in game.** Sandbox fights (`/cheat`, `/give`) between the top picks, logged
  by a small widget, to calibrate the simulator. Artillery's value depends a lot on how clumped the enemy
  is.
- **Air units.** Not in the analysis yet: no air or anti-air matchups.
- **Stall weight.** Your own order (the commander helping the T2 lab) beat the plan by 16 s in the model,
  at the price of 110 s of stall. Decide how much stalling should cost.
- **Juggernaut.** It still trades finish time for wasted build power while every economy cap is reached.
- **Other factions** for the optimizer's economy lists. The unit analysis already covers all three.
- **NRPA / MCTS** (former item 3.4): only if T2/T3 results stop improving with the current operators.

## Smaller gaps

- **Builders can't leave a job early.** In-game you can pull builders off; add a "leave job" action to the
  simulator and search.
- **Other factions.** The economy list and caps are Cortex-only. Add Armada and Legion lists, and let the
  commander be chosen per goal (`config.commander` already exists).
- **Weights** are guesses; tune them against builds you'd consider good.
- **Runs in the web UI aren't saved** between sessions. Add export/import of run JSON, or save to
  `results/`.
- **`legacy/interface.py`** is archived as-is; the web UI's recipe editor replaces it. Delete it once you're
  happy with the web UI.

## Model accuracy (check in-game)

These assumptions are documented in the README; they need checking against replays:

- **No travel time and no reclaim.** Real early builds are slower in the first couple of minutes, and
  reclaim adds metal.
- **How resources are split during a stall.** The model slows every job equally. The engine probably serves
  builders one at a time, in unit order. Test: stall a factory and a constructor together and see whether
  both slow down or one stops. The Builder Priority widget isn't modelled.
- **Wind** is a constant average, while the real wind varies and caps at 25. Set `wind_average` per map from
  what you observe.
- **Converter level:** set `converter_level` to your in-game slider setting (BAR's default is 75%).
