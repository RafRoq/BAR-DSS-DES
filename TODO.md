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

## Next

- **Juggernaut still trades finish time for wasted build power.** The gantry saves up for long stretches
  while every economy cap is reached, so builders idle with resources piling up. The improvement phase
  cuts that waste but finishes about 50 s later (1442 s against 1390 s). Options:
  - let caps grow with the goal's cost;
  - add more economy kinds (storage, more nanos);
  - or start the next expensive job with a deliberate, partial stall.
- **"Proven optimal" is narrower than it sounds.** Branch-and-bound only searches the beam's decisions,
  which start jobs at events or at "affordable from" times. The improvement phase already beat its
  `armada_bot` optimum (73.97) with 73.7, so the bound isn't a true lower bound for the game.
- **Consistency.** Two 30 s runs now differ by about 1–3% on `coralab` and gantry, where earlier versions
  differed by up to 25%. Longer budgets help on T2/T3 goals.
- **NRPA / MCTS** (former item 3.4): only worth trying if T2/T3 results stop improving with the current
  operators.

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
