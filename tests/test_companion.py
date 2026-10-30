"""Live re-planning from game snapshots (bar_opt.companion)."""

import pytest

from bar_opt.companion import WindEstimate, plan_live, snapshot_of, state_from_snapshot
from bar_opt.goal import Goal
from bar_opt.improve import decode
from bar_opt.search import SearchConfig, apply_ops, optimize
from bar_opt.sim import State

GOAL = Goal(name="T2 lab", targets={"coralab": 1}, max_time=900)


@pytest.fixture(scope="module")
def plan():
    return optimize(GOAL, SearchConfig(beam_width=40, affordability="refined", improve_seconds=0))


def state_at(plan, t):
    state = State(GOAL.config)
    for op in plan.ops:
        apply_ops(state, [op])
        if state.time >= t:
            break
    return state


def test_snapshot_rebuilds_the_same_state(plan):
    state = state_at(plan, 130)
    live = state_from_snapshot(snapshot_of(state), GOAL.config)
    rebuilt = live.state
    assert rebuilt.time == state.time
    assert (rebuilt.metal, rebuilt.energy) == pytest.approx((state.metal, state.energy))
    assert (rebuilt.max_metal, rebuilt.max_energy) == (state.max_metal, state.max_energy)
    assert rebuilt.metal_income() == pytest.approx(state.metal_income())
    assert rebuilt.energy_income() == pytest.approx(state.energy_income())
    assert {u: n for u, n in rebuilt.counts.items() if n} == {u: n for u, n in state.counts.items() if n}
    assert sorted((j.unit, round(j.progress, 6), j.bp) for j in rebuilt.jobs.values()) == \
        sorted((j.unit, round(j.progress, 6), j.bp) for j in state.jobs.values())
    assert rebuilt.spots_used == state.spots_used
    # simulator builder ids map back to the snapshot's unit ids
    assert live.labels[0] == "Commander" and live.game_id[0] == 1


def test_builder_walking_to_an_order_is_committed():
    state = State(GOAL.config)
    snapshot = snapshot_of(state)
    snapshot["units"][0]["order"] = "corwin"  # the commander was told to build a wind turbine
    rebuilt = state_from_snapshot(snapshot, GOAL.config).state
    assert [j.unit for j in rebuilt.jobs.values()] == ["corwin"]
    assert rebuilt.idle_builders() == []


def test_following_the_companion_reaches_the_goal_like_the_plan(plan):
    state, template, sources = State(GOAL.config), None, []
    while True:
        live, template = plan_live(GOAL, snapshot_of(state), template=template)
        if live["done"]:
            break
        sources.append(live["source"])
        target = state.time + 20  # the player follows the live plan for 20 s
        for op in decode(GOAL, template.steps, state).ops:
            apply_ops(state, [op])
            if state.time >= target:
                break
    assert GOAL.satisfied(state)
    assert state.time <= plan.evaluation["finish_time"] + 2
    assert sources.count("kept") >= len(sources) // 2  # the plan stays put while it is followed


def test_companion_adapts_when_the_game_falls_behind(plan):
    state = state_at(plan, 60)
    on_track, _ = plan_live(GOAL, snapshot_of(state))
    # The same moment, but with half the metal and energy (a slow start, a calm spell).
    snapshot = snapshot_of(state)
    snapshot["metal"][0] /= 2
    snapshot["energy"][0] /= 2
    behind, _ = plan_live(GOAL, snapshot)
    assert behind["eta"] > on_track["eta"]
    assert behind["steps"] and all(s["start"] >= state.time - 1e-6 for s in behind["steps"])


def test_goal_already_reached_and_wind_estimate(plan):
    done = state_at(plan, 10_000)
    live, template = plan_live(GOAL, snapshot_of(done))
    assert live["done"] and template is None

    wind = WindEstimate(14.0)
    for seq, now in enumerate([2.0, 2.0, 2.0, 2.0]):
        wind.update({"seq": seq, "wind": {"now": now}})
    wind.update({"seq": 3, "wind": {"now": 2.0}})  # the same snapshot twice counts once
    assert wind.value == pytest.approx((14.0 * 12 + 8.0) / 16)
