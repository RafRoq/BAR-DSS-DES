import random

import pytest

from bar_opt.goal import Goal
from bar_opt.improve import RolloutPlanner, Step, _Moves, decode, improve, remaining_steps, steps_from_result
from bar_opt.search import Planner, SearchCancelled, SearchConfig, optimize, replay_ops
from conftest import no_walk

# Without walking, so the optimum proven by branch and bound (73.97) still applies.
ARMADA = Goal(name="armada", targets={"cormex": 3, "corwin": 7, "corap": 1, "corca": 1}, max_time=600,
              config=no_walk())


def beam(goal):
    return optimize(goal, SearchConfig(beam_width=20, affordability="refined", improve_seconds=0))


@pytest.mark.parametrize("targets", [ARMADA.targets, {"coralab": 1}])
def test_decoder_reproduces_the_build_it_came_from(targets):
    goal = Goal(name="g", targets=targets, max_time=900)
    result = beam(goal)
    decoded = decode(goal, steps_from_result(goal, result))
    assert decoded.cost == pytest.approx(result.evaluation["cost"])
    # and its ops replay to the same build
    assert goal.evaluate(replay_ops(goal, decoded.ops))["cost"] == pytest.approx(decoded.cost)


def test_decoder_handles_impossible_and_deadlocked_steps():
    goal = Goal(name="g", targets={"cormex": 1}, max_time=300, config=no_walk())
    # the commander cannot build corck: skipped; then the mex is built
    ok = decode(goal, [Step("corck", ("corcom", 0)), Step("cormex", ("corcom", 0))])
    assert ok.cost == pytest.approx(1870 / 300)
    # a step for a builder that is never built blocks everything after it
    stuck = decode(goal, [Step("cormex", ("corck", 0))])
    assert stuck.cost == float("inf")


def test_moves_keep_assist_steps_pointing_at_their_job():
    planner = Planner(ARMADA)
    steps = [Step("corap", ("corcom", 0)), Step("corca", ("corap", 0)), Step(None, ("corcom", 0), target=1)]
    moves = _Moves(planner, random.Random(1))
    for _ in range(300):
        mutated = moves.mutate(steps)
        if mutated is None:
            continue
        for s in mutated:
            if s.unit is None:
                assert mutated[s.target].unit is not None


def test_improve_never_worsens_and_reaches_the_proven_optimum():
    result = beam(ARMADA)
    improved = improve(ARMADA, result, SearchConfig(beam_width=20, seed=0), seconds=8)
    assert improved.evaluation["cost"] <= result.evaluation["cost"] + 1e-9
    assert ARMADA.evaluate(replay_ops(ARMADA, improved.ops))["cost"] == pytest.approx(improved.evaluation["cost"])
    # Branch and bound proves 73.97 optimal among the beam's decisions (benchmark.py --exact).
    # Steps can start at times the beam never tries, so the improvement may even beat it; it
    # runs on wall-clock time and stops early when stuck, so allow half a percent above it.
    assert improved.evaluation["cost"] <= 73.97 * 1.005


def test_improve_stops_when_cancelled():
    result = beam(ARMADA)

    def cancel(_info):
        raise SearchCancelled

    improved = improve(ARMADA, result, SearchConfig(beam_width=20), seconds=60, progress=cancel)
    assert improved.evaluation["cost"] <= result.evaluation["cost"]


def test_remaining_steps_drop_started_builds_and_keep_their_helpers():
    steps = [Step("cormex", ("corcom", 0)), Step("corlab", ("corcom", 0)), Step(None, ("corck", 0), target=1),
             Step("cormex", ("corcom", 0)), Step(None, ("corck", 0), target=3)]
    left = remaining_steps(steps, {"cormex": 1, "corlab": 1})
    assert [x.unit for x in left] == [None, "cormex", None]
    assert left[0].assist_unit == "corlab"  # the lab is under way: help the running one
    assert left[2].target == 1  # still aimed at the second mex, now the first build step


def test_decoder_continues_from_a_mid_build_state():
    goal = Goal(name="g", targets={"coralab": 1}, max_time=900)
    result = beam(goal)
    steps = steps_from_result(goal, result)
    cut = [i for i, op in enumerate(result.ops) if op[0] == "advance"][3]
    state = replay_ops(goal, result.ops[:cut + 1], record=False)
    started: dict[str, int] = {}
    for op in result.ops[:cut + 1]:
        if op[0] == "start":
            started[op[2]] = started.get(op[2], 0) + 1
    rest = decode(goal, remaining_steps(steps, started), state)
    assert rest.cost == pytest.approx(result.evaluation["cost"])


def test_rollout_search_never_worsens_its_template_and_replays():
    result = beam(ARMADA)
    planner = RolloutPlanner(ARMADA, SearchConfig(beam_width=3, affordability="refined"), result)
    found = planner.search()
    assert planner.playouts > 0
    assert found.evaluation["cost"] <= result.evaluation["cost"] + 1e-9
    assert ARMADA.evaluate(replay_ops(ARMADA, found.ops))["cost"] == pytest.approx(found.evaluation["cost"])
