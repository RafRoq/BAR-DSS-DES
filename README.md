# BAR Build Order Optimizer

![Python Version](https://img.shields.io/badge/python-3.12%2B-blue)

Finds build orders for *Beyond All Reason* (BAR). You describe a goal ("a T2 bot lab",
"this set of units"). The optimizer searches for the order, timing and builder assignments
that reach it as fast as possible without stalling, overflowing storage or leaving build
power idle. It can also score your own hand-written build orders the same way.

BAR DSS DES stands for Beyond All Reason Decision Support System Discrete Event Simulator.

## Quick start

```sh
pip install -r requirements.txt
python app.py                                                               # web UI, opens your browser
python optimize.py goals/coralab.yaml                                       # fastest T2 bot lab
python optimize.py goals/armada_bot.yaml --compare recipes/armada_bot.yaml  # vs. a hand build
python optimize.py --from-recipe recipes/cortex_air.yaml                    # same end state as a hand build
python -m pytest tests
```

## Web UI

`python app.py` starts a local server at http://127.0.0.1:8765 and opens it in your browser. Use `--port` to
pick another port and `--no-browser` to skip opening the browser. It runs only on your machine, needs no
internet, and reads and writes only `goals/*.yaml` and `recipes/*.yaml`.

- **Request panel (left):**
  - Build the goal by searching the unit catalog: the 187 units the commander can reach, with costs,
    economy effects and who builds them.
  - Tune the penalty weights, map, rules and search with sliders. Each weight shows what it means
    in seconds.
  - Load or save goal files, and pick a hand-written build to compare against.
  - **Optimize** shows live progress for each of the six search runs (best cost so far), with a button to
    stop and keep the best build found.
- **Runs:** every optimizer run and every scored recipe from the session. Click one to inspect it; tick up
  to four to compare.
- **Timeline:**
  - Headline figures, then metal and energy stored with their caps and the converter level.
  - Metal and energy flows (income, building, converters) and build power (busy and idle).
  - Stall and overflow periods are shaded.
  - All charts zoom together. Clicking a moment shows the exact state at that time: resources, rates,
    jobs in progress with their %, idle builders and units owned.
  - A data table holds every value.
- **Builders & steps:** one Gantt row per builder, with jobs started and jobs assisted in different colours,
  stalls underlined and idle time in grey. It's linked to a numbered step list you can follow in-game.
  Export as a YAML recipe, copy as text, save to `recipes/`, or open in the recipe editor.
- **Compare:** a metric table with the best value marked, the time each target was reached, and overlaid
  charts of storage, busy build power and goal progress.
- **Recipe editor:** write or load a hand-written build. Add steps from the catalog, reorder them, and set
  builders; invalid builders are flagged. Score it against the goal, or use its end state as the goal.

- **In-game:** take a build into BAR and compare the game with the plan (see below).

The charts use [ECharts](https://echarts.apache.org) 5.5.1 (Apache-2.0), included in `web/vendor/`.

## In-game guide

`widget/gui_build_guide.lua` is a BAR widget that guides you through a planned build while you play. It
only displays; it never gives orders.

1. **Send a build to BAR.** In the web UI, select a run, open **In-game**, and press **Send to BAR**. This
   copies the widget to `LuaUI/Widgets/` and the plan to `LuaUI/Config/build_guide_plan.json` in your BAR
   data folder.
   - The folder is found at `%LOCALAPPDATA%\Programs\Beyond-All-Reason\data`; set `BAR_DATA_DIR` to use
     another.
   - Without the web UI: `optimize.py` writes `build_guide_plan.json` to `results/<name>/`, or use
     **Download plan** and **Download widget** and copy the files yourself.
2. **Play.** Enable **Build Order Guide** in the widget list (F11).
   - The panel shows the last steps finished, everything under construction, and the next steps, with
     builders and planned times.
   - It also shows how many seconds ahead or behind plan you are, and your metal and energy next to the
     plan's.
   - It starts at the top right, under the top bar. Drag it by its title; `/luaui buildguide_reset` puts it
     back. `/luaui buildguide_reload` reloads the plan; `/luaui buildguide_toggle` hides it.
3. **Compare.** The widget logs every game to `LuaUI/Config/build_guide_log_<date>.json`: when each step
   really started and finished, plus your resources every 5 s. The **In-game** tab lists these logs.
   Open one to see what you control apart from what you don't:
   - **When the goal was reached**, against the plan.
   - **Build times against the plan.** Do the buildings go up as fast as planned? If so, the economy
     plan held.
   - **Waiting before steps:** each time you fell further behind than ever before a step started, usually
     from walking or giving orders. Steps built in another order aren't counted twice.
   - Plan against game for metal, energy and income, and a step-by-step table.

   This is how to check the model's assumptions, such as walking time and reclaim (see
   "Where the model differs from the real game").

The widget matches steps by unit type and order: the nth unit of a kind you start is the plan's nth
step of that kind. A build cancelled before it finishes reopens its step; units not in the plan are
listed separately. `tests/test_guide.py` runs the widget under Lua 5.1 against a mock of the BAR widget
API when `lupa` is installed (`pip install lupa`); it hasn't been tested in a real game yet.

## Command line

Each `optimize.py` run writes the following to `results/<name>/`:

| File | Contents |
|---|---|
| `graph_optimizer.png` (`graph_hand.png`) | Storage, net income, unit count, build power, task Gantt (stalls in yellow), builder Gantt |
| `cookbook.txt` | Timed plan: which builder starts or assists what, and when, with builder ids |
| `optimized.yaml` | The build in the recipe format of `legacy/interface.py`. This loses timing information: it records order and builder types only |
| `comparison.txt` | Finish time, stall, overflow, idle build power and weighted cost per build |

## Goals

```yaml
name: Fastest T2 bot lab
targets:            # unit name: count. The goal is reached when all are finished.
  coralab: 1
max_time: 900
weights:            # cost = finish_time + stall*lost BP-s + overflow_metal*metal
  stall: 0.0333     #        + overflow_energy*energy + idle*wasted BP-s
  overflow_metal: 0.2
  overflow_energy: 0.01
  idle: 0.0067
config:             # map and game assumptions, see "Assumptions"
  wind_average: 14.2
  mex_spots: 4
search:             # optional
  beam_width: 150
  improve_seconds: 30 # time spent polishing the best build after the beam searches
  constructor_cap: 3
  caps: {cormakr: 12}
  economy_units: [cormex, corwin, corsolar, cormakr, corestor, cormstor, cornanotc, coradvsol,
                  cormoho, corfus, cormmkr]
```

All penalty terms are reported separately as well as combined in the weighted cost:
- **Stall:** build power lost to stalling, `(1 - speed) × active build power × time`. Seconds with any stall are reported too.
- **Overflow:** income lost to full storage.
- **Wasted build power:** build power of idle builders that could have helped (commander, constructors,
  nano turrets) while income exceeded spending on both metal and energy, times that time. This is what the
  `idle` weight applies to.
  - Factories don't count: they can't assist, so an idle factory isn't a decision the build could have made better.
  - Builders held back while storage drains or sits empty don't count either: more build power would only stall.
  - Total idle build power, factories included, is still reported and charted.

Everything is measured until the goal is reached.

## How it works

`bar_opt/` contains:

- **`data.py`**: unit stats and the tech tree (which unit can build which) from `bar_units.json`.
- **`sim.py`**: an event-driven simulator. Between events every rate is constant, so it jumps
  from event to event and the results are exact. Events are: a job finishes, a resource reaches 0 or its cap, or energy
  reaches the converter level. The simulator only answers "what happens if I do X now".
- **`search.py`**: beam search. Whenever a builder is idle, it branches over:
  - start a unit now;
  - wait until that unit can be paid for without stalling;
  - assist a running job;
  - wait for the next event.

  Nodes are ranked by cost so far plus an estimate of the remaining time, based on build power and on the metal and energy still missing compared with income.
  - **Candidates:** the goal, its cheapest tech-tree prerequisites, and an economy list, each capped. The
    list includes T2 economy (moho, fusion, advanced converter); the tech tree keeps it out of reach until a
    T2 constructor exists, so mexes get upgraded as soon as one does.
  - **Pruning:** duplicate states, unaffordable actions, and anything that wouldn't be affordable within 90 s.
  - **Robustness:** beam search depends on its width and on the exact set of candidate actions, so `optimize()` runs three widths with two affordability estimates and keeps the cheapest result.
- **`improve.py`**: the improvement phase, run after the beam searches for up to `improve_seconds`.
  It stops early once nothing better has turned up for half that time. Three operators share the time:
  - **Step local search:** the build becomes an ordered list of steps ("build U with builder B, assisted
    by H, not before T"). A decoder runs them on the simulator in a few milliseconds. Simulated annealing
    then tries edits: reorder, change builder or assistants, shift waits, add or remove an economy
    building. This finds what the beam's estimate ranks badly, such as a different opening order.
  - **Suffix re-planning:** cut the build at a random decision point and re-plan the rest with a fresh beam
    search using varied settings.
  - **Rollout re-planning:** the same cut, re-planned by a narrow beam search that ranks each state by
    actually finishing the build from it (following the best build's remaining steps) instead of by the
    estimate. That sees what the estimate misses, such as economy paying off later and penalties still to
    come. Any playout that beats the best build becomes the new best and the new template.

  The result is never worse than the beam's.
- **`exact.py`**: branch and bound over the same decisions as the beam, with a lower bound that never
  overestimates. It proves a build optimal, or gives an interval the optimum must lie in.
  `python benchmark.py --exact 120` reports this for every benchmark goal. "Optimal" means optimal within
  the search's decision space: waits happen only at the computed "affordable from" times or at the next
  event.
- **`recipe.py`**: replays hand-written recipes in strict order (each step starts once its builders are free; stalls are allowed, nothing is skipped) and exports results as YAML.
- **`report.py`, `plots.py`**: graphs and cookbook.

### Model

| Rule | Model | Source |
|---|---|---|
| Build time | `buildtime / build power` of all builders on the job | engine |
| Cost | Metal and energy drained in proportion to progress | engine |
| Start | 1000 metal, 1000 energy, storage 1000/1000 *including* the commander's 500 | `game_team_resources.lua` resets storage at GameStart |
| Storage | Each building adds its own storage | unitdefs |
| Commander | +2 metal/s, +30 energy/s, 300 build power | `corcom.lua` |
| Converters | Use energy above `converter_level × storage` (default 0.75), up to capacity, most efficient first. At the level they convert only the surplus | `game_energy_conversion.lua` |
| Extractors | `extractsmetal × 1000 × metal_per_spot`; 3 E/s upkeep (T1); limited to `mex_spots` | unitdefs |
| T2 extractor | Uses a free spot, else upgrades a T1. The T1 keeps producing until the T2 finishes, then is removed and its metal refunded | `unit_mex_upgrade_reclaimer.lua` |
| Wind / tidal | `min(wind_average, 25)` / `tidal_strength`, constant | |
| Stalling | When a resource is at 0 and demand exceeds income, every job slows by the same factor | approximation, see below |
| Factories / nanos | Factories build their options and can't assist. Nano turrets only assist. Mobile builders do both | unitdefs |

### Assumptions (`config:`)

Defaults:
- **Map:** `wind_average 14.2`, `tidal_strength 21`, `metal_per_spot 2.3` (T1 extractor income),
  `mex_spots 4`. Usually 3–4 spots are close together; the rest are far, so they aren't counted by default.
- **Rules:** `converter_level 0.75`, `start_metal/energy 1000`, `start_*_storage 1000`, `commander corcom`.
- **Walking:** off (`walk_base`, `walk_mex_near`, `walk_mex_far` all 0, plus `mex_near 4`). When set, a
  commander or constructor walks that many seconds before it starts or assists a job, and adds no build
  power until it arrives. The near/far time applies to the first `mex_near` spots and to the rest.

### Where the model differs from the real game

These are worth checking in-game or in a replay:

1. **No travel time by default.** Walking depends on the map, the start position and your role, so the
   optimizer plans the economy and the in-game guide measures the rest.
   - In the first test game (Supreme Isthmus) every building took exactly its planned build time. The goal
     was still 96 s late, because of waits before steps: about 1 s between base buildings, 10–24 s to reach
     the 3rd–5th mex, and 58 s to the 6th.
   - The `walk_*` settings can model a specific map if you want plans that include walking.
2. **No reclaim.** Trees, rocks and wrecks are ignored. On many maps they're significant early metal.
3. **How resources are split during a stall.** As far as I know, the engine takes resources builder by builder each frame, in unit order. Here they're split proportionally across jobs. Total spending equals income either way, but which job finishes first can differ. BAR's Builder Priority setting (low-priority builders only use leftovers) isn't modelled. *Check:* stall a factory and a constructor together and see whether both slow down or one stops.
4. **Income timing.** Income arrives continuously here; in BAR it's credited periodically (converters run every 15 frames). This affects timings by under a second.
5. **Wind is constant** at `min(average, 25)`. The mean of a varying wind capped at 25 is lower than that. Set `wind_average` to what you observe on your map.
6. **Converter level.** BAR's default is 75%, and players can change it with a slider. Set `converter_level` to match your setting.
7. **One base, no combat, no unit upkeep** apart from extractors. Overflow is counted as lost; in team games it goes to allies.
8. **Builders don't leave jobs.** Once assisting, a builder stays until the job finishes. In-game you can pull builders off.
9. **Quick Start** (the pre-game instant build) is off, which is BAR's default outside the "dominion" mode.

To check a result: replay the cookbook in a skirmish or the in-game sandbox. Compare when each building finishes, and the storage and income values at a few timestamps, with the graph.

## Data

`bar_units.json` is generated from BAR's own unit definitions by `bar_unit_extractor.py`. It contains stats, display names
and build options, all from the same BAR commit (recorded in the file). To refresh it after a balance patch:

```sh
git clone --depth 1 --filter=blob:none --sparse https://github.com/beyond-all-reason/Beyond-All-Reason.git
git -C Beyond-All-Reason sparse-checkout set units language/en
python bar_unit_extractor.py Beyond-All-Reason
```

Only Cortex economy is set up by default (the default economy list), but any unit in the data can be a target.

## Recipes

`recipes/` holds hand-written builds in the YAML format used by `legacy/interface.py`:

```yaml
- name: Metal Extractor - cormex
  repeat: 2
  builders: [corcom]          # first builder able to build it starts the job, the others assist
```

## Legacy

`legacy/` holds the original fixed-step simulator (`main.py`), the Tk build-order editor (`interface.py`), the API
scraper and the September 2025 unit data. It still runs from that folder: `cd legacy && python main.py`.
`tests/test_baseline_old_sim.py` keeps its output for the `ARMADA_BOT` recipe as a regression baseline. The new
core reproduces it to within 0.02 s on every T1 step; `corap` and `corca` differ only because their stats changed in BAR.
