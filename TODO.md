# Backlog

Open work on the optimizer, recorded on 2026-10-29. Line numbers refer to commit `b11f82f` on the `optimizer`
branch. Each item lists what's wrong, the evidence, the proposed change, and how to know it's done.

## 1. Let the optimizer build T2 economy (moho, fusion, advanced converter)

**Problem.** The simulator fully supports T2 metal extractors: a moho goes on a free spot or upgrades a T1
mex, the T1 keeps producing until the moho finishes, and the T1 is then removed with a 50 metal refund. This
is tested in `test_mex_spot_limit_and_upgrade`. But the optimizer never considers a moho unless `cormoho` is
a goal target. The default economy list
[bar_opt/search.py:20](bar_opt/search.py#L20) has T1 units only, so `cormoho`, `corfus` (fusion, 850 E/s)
and `cormmkr` (advanced converter) are never candidates. Hand-written recipes can already use them.

**Change.**
- Add `cormoho`, `corfus` and `cormmkr` to `DEFAULT_ECONOMY`. The tech tree already gates them, so they only
  become options once a T2 constructor (`corack`, `coracv`, `coraca`) exists.
- Add caps to `DEFAULT_CAPS` [bar_opt/search.py:21](bar_opt/search.py#L21). Cap `cormoho` at the number of
  metal spots, next to the existing `caps["cormex"]` line [bar_opt/search.py:129](bar_opt/search.py#L129).
  Pick sensible caps for fusion and advanced converters.
- Check that `_owned_or_building` caps still behave when T1 extractors are being replaced: the
  `cormex` count drops while spots stay used.

**Done when.**
- A test goal that unlocks a T2 constructor (e.g. `corack` plus something that takes long enough to be
  worth upgrading) produces a build that upgrades mexes without being told to.
- Mohos never exceed the number of spots.
- Existing goals (`coralab`, `armada_bot`) don't get worse.

## 2. Make expensive T3 goals solvable (Juggernaut `corkorg`, Behemoth `corjugg`)

Note the names: in the data, Juggernaut is `corkorg` (29,000 M, 615,000 E, 730,000 build time). `corjugg` is
the Behemoth (20,000 M, 513,000 E). Both are built at the Experimental Gantry; the chain is
`corlab → corck → coralab → corack → corgant`.

**Evidence.** Beam width 20, goal `corkorg: 1`, `max_time` 900 or 2400 (identical results):
- The search stops at the 2000-decision limit with 20 nodes still alive and no solution, after about 6 s.
- The best node builds 8 things (3 mex, 2 wind, 1 solar, 1 converter, 1 metal storage), then only
  waits. By decision 1999 it's at t = 857 s with 69 E/s of income.
- The remaining-time estimate reads 2400 s at every point.

**Causes, most important first.**

1. **The remaining-time estimate is capped at `max_time`.** `return min(max(t_bp, t_m, t_e), self.goal.max_time)`
   at [bar_opt/search.py:166](bar_opt/search.py#L166). For huge goals the raw estimate is always above the
   cap, so building economy never lowers it. With no reward for progress, waiting looks cheaper than risking
   a stall penalty.
   *Change:* don't cap. Instead, prune nodes whose optimistic finish time (`time + estimate`) exceeds
   `max_time`. Keep the guard against tiny or negative income dividing by about zero.
2. **No T2 economy** (item 1). The best T1 economy the caps allow is about 15.8 M/s plus 12 M/s from 840 E/s
   of converters, and about 1,300 E/s gross. That leaves about 460 E/s for building, so the Juggernaut's
   energy alone would take about 1,340 s.
3. **The "wait until affordable" estimate ignores converters.**
   `_delay_until_affordable` [bar_opt/search.py:178](bar_opt/search.py#L178) assumes stored energy grows at
   the full net income. In reality converters burn everything above `converter_level × storage` (default
   75%). For anything that needs more energy in stock than that level, the predicted wait never comes true,
   so the builder keeps choosing short waits. That's the stream of 2000 "advance" operations in the trace.
   *Change:* account for converter use when energy is above the level: a lower effective income, and a stock
   ceiling at the converter level, not the full storage. Or skip the wait option when the target stock is
   above the level and nothing running will raise storage.
4. **The decision limit counts waits.** `max_layers: int = 2000`
   [bar_opt/search.py:32](bar_opt/search.py#L32) is spent on decisions, and each wait is one. Long games
   need more.
   *Change:* raise it, or scale it with `max_time`. Better, make waiting cheaper in layers: jump straight to
   the next completion instead of many short waits.
5. **The build-power part of the estimate ignores factories.** `helpers` at
   [bar_opt/search.py:160](bar_opt/search.py#L160) only counts mobile builders and nano turrets. For units
   built in a factory (the Experimental Gantry has 1,800 build power), add the factory's build power and the
   assisting builders.

**Done when.** `corkorg: 1` with `max_time` 2400 returns a build, reports a finish time, and uses T2
economy. Also add a regression test with a cheaper factory-built T2 target, so it runs in CI time.

## 3. Better search algorithms

Current state: beam search ranks nodes by cost so far plus a remaining-time estimate, and `optimize()` runs
3 beam widths × 2 affordability estimates and keeps the best. Its known weaknesses:
- The estimate ignores future stalls, idle time and what economy costs.
- Results are erratic: one setting change once moved the sea build's cost from 1435 to 3271.
- It never revisits early decisions.
- Its distance from optimal is unknown.

In recommended order:

1. **Measure the optimality gap.** Write an exact depth-first branch-and-bound solver for small goals
   (`armada_bot`, maybe `coralab`). It needs a remaining-time bound that never overestimates. Use the beam
   result as the starting best build. Reference: Churchill & Buro (2011), "Build Order Optimization in
   StarCraft", which uses depth-first branch-and-bound with macro actions.
   *Done when:* we know the beam's gap on those goals.
2. **Local improvement of the best build.** Take the beam result as an ordered list of items with builder
   choices, change part of it (swap, move, replace one eco unit with another, re-plan a time window with a
   small beam), re-simulate, and keep improvements. Do this within a time budget, by simulated annealing or
   large-neighbourhood search. It can only improve on the beam result.
   *Done when:* the sea and air builds improve, or at least stop varying between settings.
3. **Playout-guided beam (the "pilot method").** Score nodes by finishing the build with a fast greedy policy
   and using the true cost, instead of the estimate. Run playouts only for the most promising candidates of
   each layer to keep the time in check. A playout should take a few milliseconds: today a search evaluates
   about 150,000 nodes in 10–30 s.
4. **Alternative standalone search:** single-player MCTS / NRPA (Rosin 2011). Bigger change; only worth
   trying if 2 and 3 fall short.

Not recommended:
- **Plain A\*:** its memory blows up unless the estimate never overestimates and is tight.
- **MILP / CP-SAT formulations:** the economy is non-linear because of proportional stall slowdown and
  converters.

## 4. Smaller gaps

- **Builders can't leave a job early.** In-game you can pull builders off; add a "leave job" action to the
  simulator and search.
- **Other factions.** The economy list and caps are Cortex-only. Add Armada and Legion lists, and let the
  commander be chosen per goal (`config.commander` already exists).
- **Idle factories.** They count as idle build power. In the T2 lab build, idle build power (mostly the bot
  lab) is about 124 of the 405 cost. Make factory idle time optional, or weight it separately.
- **Weights** are guesses; tune them against builds you'd consider good.
- **Runs in the web UI aren't saved** between sessions. Add export/import of run JSON, or save to
  `results/`.
- **`legacy/interface.py`** is archived as-is; the web UI's recipe editor replaces it. Delete it once you're
  happy with the web UI.

## 5. Model accuracy (check in-game)

These assumptions are documented in the README; they need checking against replays:

- **No travel time and no reclaim.** Real early builds are slower in the first couple of minutes, and
  reclaim adds metal.
- **How resources are split during a stall.** The model slows every job equally. The engine probably serves
  builders one at a time, in unit order. Test: stall a factory and a constructor together and see whether
  both slow down or one stops. The Builder Priority widget isn't modelled.
- **Wind** is a constant average, while the real wind varies and caps at 25. Set `wind_average` per map from
  what you observe.
- **Converter level:** set `converter_level` to your in-game slider setting (BAR's default is 75%).
