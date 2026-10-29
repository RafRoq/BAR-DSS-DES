import pytest

from bar_opt.exact import BranchAndBound, _time_to_do
from bar_opt.goal import Goal
from bar_opt.search import SearchConfig, optimize
from bar_opt.sim import State

ARMADA = {"cormex": 3, "corwin": 7, "corap": 1, "corca": 1}


def test_lower_bound_never_exceeds_the_cost_of_a_real_build():
    """Proofs of optimality rely on this: at every point of a complete build, the bound on
    its final cost must not be above what the build actually costs."""
    for targets in (ARMADA, {"corlab": 1, "corck": 1}, {"coralab": 1}):
        goal = Goal(name="g", targets=targets, max_time=900)
        result = optimize(goal, SearchConfig(beam_width=20, affordability="refined"))
        final = result.evaluation["cost"]
        bb = BranchAndBound(goal)
        state = State(goal.config)
        for op in result.ops:
            bound = state.time + bb.planner._penalties(state) + bb.remaining_lower_bound(state)
            assert bound <= final + 1e-6, (targets, op, bound, final)
            if op[0] == "start":
                state.start(op[1], op[2])
            elif op[0] == "assist":
                state.assist(op[1], op[2])
            else:
                state.advance(until=op[1])


def test_small_goal_is_proven_optimal():
    goal = Goal(name="g", targets={"cormex": 2, "corwin": 2}, max_time=600)
    result = BranchAndBound(goal).solve(time_limit=30)
    assert result.proven
    assert result.lower_bound == pytest.approx(result.result.evaluation["cost"])
    # 2 mex + 2 wind by the commander alone: 2*1870/300 + 2*1680/300 s, no penalties
    assert result.result.evaluation["cost"] == pytest.approx((2 * 1870 + 2 * 1680) / 300)


def test_budget_stop_reports_an_interval():
    goal = Goal(name="g", targets=ARMADA, max_time=600)
    incumbent = optimize(goal, SearchConfig(beam_width=20, affordability="refined"))
    result = BranchAndBound(goal).solve(incumbent, node_limit=500)
    assert not result.proven
    assert result.lower_bound <= result.result.evaluation["cost"]
    assert 0 <= result.gap < 1


def test_time_to_do_with_build_power_ramp():
    assert _time_to_do(300, 300, []) == pytest.approx(1)
    # 300 BP from the start, +100 BP from t=2: 600 done at t=2, then 400/s
    assert _time_to_do(1000, 300, [(2.0, 100.0)]) == pytest.approx(3)
