import random

import pytest

from bar_opt.goal import Goal
from bar_opt.improve import Step, _Moves, decode, improve, steps_from_result
from bar_opt.search import Planner, SearchCancelled, SearchConfig, optimize, replay_ops

ARMADA = Goal(name="armada", targets={"cormex": 3, "corwin": 7, "corap": 1, "corca": 1}, max_time=600)


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
    goal = Goal(name="g", targets={"cormex": 1}, max_time=300)
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
    # Branch and bound proves 76.04 is optimal for this goal (see test_exact / benchmark).
    assert improved.evaluation["cost"] == pytest.approx(76.04, abs=0.01)


def test_improve_stops_when_cancelled():
    result = beam(ARMADA)

    def cancel(_info):
        raise SearchCancelled

    improved = improve(ARMADA, result, SearchConfig(beam_width=20), seconds=60, progress=cancel)
    assert improved.evaluation["cost"] <= result.evaluation["cost"]
