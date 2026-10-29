import pytest

import main
from bar_opt.goal import Goal
from bar_opt.recipe import replay
from bar_opt.search import INF, Node, Planner, SearchConfig, optimize, replay_ops

ARMADA_GOAL = Goal(name="armada", targets={"cormex": 3, "corwin": 7, "corap": 1, "corca": 1}, max_time=600)


def test_prerequisite_chain_from_tech_tree():
    planner = Planner(Goal(name="t2", targets={"coralab": 1}))
    assert planner.prerequisites("coralab") == ["corlab", "corck"]
    assert planner.prerequisites("corca") == ["corap"]
    assert {"corlab", "corck", "coralab", "cormex", "corwin"} <= planner.allowed


def test_prerequisites_by_total_chain_cost_and_shared_with_other_targets():
    planner = Planner(Goal(name="nano", targets={"cornanotc": 1}))
    # corca (115 metal) is cheaper than corck (120), but its aircraft plant is not
    assert planner.prerequisites("cornanotc") == ["corlab", "corck"]
    planner = Planner(Goal(name="t2+nano", targets={"coralab": 1, "cornanotc": 1}))
    assert planner.chains["cornanotc"] == ["corlab", "corck"]
    assert "corap" not in planner.allowed
    planner = Planner(Goal(name="air+nano", targets={"corap": 1, "corca": 1, "cornanotc": 1}))
    assert planner.chains["cornanotc"] == ["corap", "corca"]
    assert "corlab" not in planner.allowed


def test_t2_constructor_unlocks_extractor_upgrades():
    goal = Goal(name="fusion", targets={"corack": 1, "corfus": 1}, max_time=1500)
    result = optimize(goal, SearchConfig(beam_width=20, affordability="refined"))
    state = replay_ops(goal, result.ops)
    # Nobody asked for mohos; the optimizer builds them because they pay off.
    assert state.count("cormoho") >= 1
    assert state.count("cormex") + state.count("cormoho") <= goal.config.mex_spots


def test_t2_economy_is_ignored_without_a_t2_constructor():
    planner = Planner(Goal(name="t2", targets={"coralab": 1}))
    state = planner.root.copy()
    actions = planner._actions(Node(state, None, (0,), INF), 0)
    assert not any(a[0] == "start" and a[2] in ("cormoho", "corfus", "cormmkr") for a in actions)


def test_unreachable_target_is_rejected():
    with pytest.raises(ValueError):
        Planner(Goal(name="arm", targets={"armlab": 1}))


def test_search_result_replays_exactly_and_beats_hand_recipe():
    result = optimize(ARMADA_GOAL, SearchConfig(beam_width=20))
    assert result is not None and result.evaluation["reached"]
    state = replay_ops(ARMADA_GOAL, result.ops)
    assert ARMADA_GOAL.evaluate(state) == pytest.approx(result.evaluation)

    hand, _ = replay(main.ARMADA_BOT, ARMADA_GOAL.config, stop_when=ARMADA_GOAL.satisfied)
    assert result.evaluation["cost"] < ARMADA_GOAL.evaluate(hand)["cost"]
    assert result.evaluation["stall_seconds"] == 0
