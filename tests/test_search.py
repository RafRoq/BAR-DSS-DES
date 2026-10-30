import pytest

import main
from bar_opt.config import EconConfig
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
    planner = Planner(Goal(name="gantry", targets={"corgant": 1}, max_time=1800))
    assert planner.caps["cormoho"] == planner.goal.config.mex_spots
    state = planner.root.copy()
    state._add_unit("cormex", +1)
    state.spots_used = planner.goal.config.mex_spots  # every spot taken: only upgrades are left
    state._add_unit("corack", +1)
    ack = state.builder_unit.index("corack")
    actions = planner._actions(Node(state, None, (ack,), INF), ack)
    # Nobody asked for mohos; they are on offer because a T2 constructor exists.
    assert ("start", ack, "cormoho") in actions
    job = state.start(ack, "cormoho")
    assert state.jobs[job].upgrades == "cormex"


def test_search_can_choose_extractor_upgrades():
    goal = Goal(name="gantry", targets={"corgant": 1}, max_time=1800)
    result = optimize(goal, SearchConfig(beam_width=20, affordability="refined", improve_seconds=0))
    state = replay_ops(goal, result.ops)
    assert state.count("cormoho") >= 1
    assert state.count("cormex") + state.count("cormoho") <= goal.config.mex_spots


def test_t2_economy_is_ignored_without_a_t2_constructor():
    planner = Planner(Goal(name="t2", targets={"coralab": 1}))
    state = planner.root.copy()
    actions = planner._actions(Node(state, None, (0,), INF), 0)
    assert not any(a[0] == "start" and a[2] in ("cormoho", "corfus", "cormmkr") for a in actions)


def test_remaining_estimate_is_not_capped_at_max_time():
    # A capped estimate is flat for huge goals, so building economy looked pointless.
    planner = Planner(Goal(name="juggernaut", targets={"corkorg": 1}, max_time=2400))
    start = planner.remaining_estimate(planner.root)
    assert start > planner.goal.max_time
    richer = planner.root.copy()
    richer._add_unit("corwin", +1)
    assert planner.remaining_estimate(richer) < start


def test_builder_can_pass_while_others_still_decide():
    # Without this, a builder with nothing to do killed the branch before a later builder
    # (the Experimental Gantry, for the Juggernaut) could start its job.
    planner = Planner(Goal(name="armada", targets={"corap": 1}))
    state = planner.root.copy()
    state._add_unit("corlab", +1)
    lab = state.builder_unit.index("corlab")
    assert not state.jobs
    assert ("wait", 0, INF) in planner._actions(Node(state, None, (0, lab), INF), 0)
    assert ("wait", lab, INF) not in planner._actions(Node(state, None, (lab,), INF), lab)


def test_estimate_counts_factory_build_power_for_factory_built_targets():
    rich = EconConfig(start_metal=1e6, start_energy=1e6, start_metal_storage=1e6, start_energy_storage=1e6)
    planner = Planner(Goal(name="corca", targets={"corca": 5}, config=rich))
    state = planner.root.copy()
    state._add_unit("corap", +1)
    state._add_unit("corlab", +1)  # a factory that cannot build the target adds nothing
    # Only build power limits this goal: 5 x 5200 build time over commander 300 + plant 150.
    assert planner.remaining_estimate(state) == pytest.approx(5 * 5200 / (300 + 150))


def test_unreachable_target_is_rejected():
    with pytest.raises(ValueError):
        Planner(Goal(name="arm", targets={"armlab": 1}))


def test_search_result_replays_exactly_and_beats_hand_recipe():
    result = optimize(ARMADA_GOAL, SearchConfig(beam_width=20, improve_seconds=0))
    assert result is not None and result.evaluation["reached"]
    state = replay_ops(ARMADA_GOAL, result.ops)
    assert ARMADA_GOAL.evaluate(state) == pytest.approx(result.evaluation)

    hand, _ = replay(main.ARMADA_BOT, ARMADA_GOAL.config, stop_when=ARMADA_GOAL.satisfied)
    assert result.evaluation["cost"] < ARMADA_GOAL.evaluate(hand)["cost"]
    assert result.evaluation["stall_seconds"] == 0


def test_milestones_are_planned_for_and_late_ones_cost():
    from bar_opt.goal import Goal

    goal = Goal.from_dict({"name": "front", "targets": {"corlab": 1}, "max_time": 600,
                           "milestones": [{"at": "1:30", "units": {"corak": 3}}]})
    assert goal.targets == {"corlab": 1, "corak": 3}  # milestone units count at the end too
    assert goal.milestones[0].at == 90
    result = optimize(goal, SearchConfig(beam_width=20, affordability="refined", improve_seconds=0))
    times = result.evaluation["milestone_times"]
    assert times[0] is not None and times[0] <= 90 + 1e-6 and result.evaluation["milestones_late"] == 0

    # The same build with an impossible deadline: every second late costs weights.late.
    strict = Goal.from_dict({"name": "front", "targets": {"corlab": 1}, "max_time": 600,
                             "milestones": [{"at": 10, "units": {"corak": 3}}]})
    state = replay_ops(strict, result.ops)
    ev = strict.evaluate(state)
    assert ev["milestones_late"] == pytest.approx(times[0] - 10)
    assert ev["cost"] == pytest.approx(result.evaluation["cost"] + strict.weights.late * (times[0] - 10))
