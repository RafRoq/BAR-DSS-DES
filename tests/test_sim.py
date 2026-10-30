"""Sanity checks of the event-driven core against hand-computed BAR numbers."""

import pytest

from bar_opt import EconConfig, SimError, State
from bar_opt.data import FACTORY, MOBILE, NANO, load_units
from conftest import no_walk

COM = 0  # builder id of the commander


def finish(state, job_id):
    while job_id in state.jobs:
        assert state.advance(), "simulation stopped before the job finished"
    return state.time


def test_starting_state_matches_bar_defaults():
    s = State(no_walk())
    assert (s.metal, s.energy) == (1000, 1000)
    assert (s.max_metal, s.max_energy) == (1000, 1000)
    assert s.metal_income() == 2  # commander metalmake
    assert s.energy_income() == 30  # commander energymake
    assert s.idle_builders() == [COM]


def test_unit_kinds():
    units = load_units()
    assert units["corcom"].kind == MOBILE
    assert units["corlab"].kind == FACTORY
    assert units["cornanotc"].kind == NANO
    assert not units["corlab"].can_assist and units["cornanotc"].can_assist


def test_build_time_and_linear_cost_drain():
    s = State(no_walk())
    job = s.start(COM, "cormex")
    t = finish(s, job)
    assert t == pytest.approx(1870 / 300)
    assert s.metal == pytest.approx(1000 - 50 + 2 * t)
    assert s.energy == pytest.approx(1000 - 500 + 30 * t)
    assert s.metal_income() == pytest.approx(2 + 2.3)
    assert s.energy_income() == pytest.approx(30 - 3)  # extractor upkeep
    assert s.metrics.stall_bp_seconds == 0


def test_proportional_metal_stall():
    s = State(no_walk(start_metal=0))
    job = s.start(COM, "cormex")
    t = finish(s, job)
    # Metal-limited: progress is paid entirely from the 2 m/s income.
    assert t == pytest.approx(50 / 2)
    speed = 2 / (300 / 1870 * 50)
    assert s.metrics.stall_seconds == pytest.approx(t)
    assert s.metrics.stall_bp_seconds == pytest.approx((1 - speed) * 300 * t)


def test_stall_slows_all_jobs_equally():
    s = State(no_walk(start_metal=0))
    s._add_unit("corlab", +1)
    lab = s.builder_unit.index("corlab")
    a = s.start(COM, "cormex")
    b = s.start(lab, "corck")
    # Same slowdown factor s for both jobs, so progress fractions stay proportional to BP / buildTime.
    s.advance(until=5.0)
    pa = s.jobs[a].progress / (300 * 5.0)
    pb = s.jobs[b].progress / (150 * 5.0)
    assert pa == pytest.approx(pb)
    assert 0 < pa < 1


def test_storage_from_buildings():
    s = State(no_walk())
    finish(s, s.start(COM, "corlab"))
    assert (s.max_metal, s.max_energy) == (1100, 1100)


def test_overflow_when_storage_full():
    s = State(no_walk())
    s.advance(until=10.0)
    assert s.metrics.overflow_metal == pytest.approx(20)
    assert s.metrics.overflow_energy == pytest.approx(300)
    assert s.metrics.idle_bp_seconds == pytest.approx(3000)
    assert s.metrics.wasted_bp_seconds == pytest.approx(3000)  # idle commander, income unspent


def test_wasted_build_power_skips_factories_and_draining_storage():
    s = State(no_walk())
    s._add_unit("cornanotc", +1)
    # The commander's lab drains metal and energy faster than income: the idle nano could
    # only make that worse, so it wastes nothing.
    finish(s, s.start(COM, "corlab"))
    assert s.metrics.wasted_bp_seconds == 0
    assert s.metrics.idle_bp_seconds == pytest.approx(200 * s.time)
    # Now income is unspent: commander and nano waste build power, the idle lab does not.
    before = s.metrics.wasted_bp_seconds
    s.advance(until=s.time + 10)
    assert s.metrics.wasted_bp_seconds - before == pytest.approx((300 + 200) * 10)


def test_converter_uses_energy_above_level():
    s = State(no_walk())
    s._add_unit("cormakr", +1)
    # 1000 energy, level 750: converter runs at its 70 E/s capacity, net -40 E/s, for 6.25 s.
    s.advance(until=6.25)
    assert s.energy == pytest.approx(750)
    eff = 0.01429
    assert s.metrics.metal_from_conversion == pytest.approx(70 * eff * 6.25)
    # Then it holds the level, converting only the 30 E/s income.
    s.advance(until=16.25)
    assert s.energy == pytest.approx(750)
    assert s.metrics.metal_from_conversion == pytest.approx(70 * eff * 6.25 + 30 * eff * 10)


def test_metal_stall_with_energy_on_converter_level():
    s = State(no_walk(start_metal=0, start_energy=750))
    s._add_unit("cormakr", +1)
    job = s.start(COM, "cormex")
    s.advance(until=1.0)
    # Energy surplus not spent on building is converted: u = 30 - speed * De, and
    # speed = (2 + u * eff) / Dm. Solve for speed.
    dm, de, eff = 300 / 1870 * 50, 300 / 1870 * 500, 0.01429
    speed = (2 + 30 * eff) / (dm + de * eff)
    assert s.jobs[job].progress == pytest.approx(speed * 300)
    assert s.energy == pytest.approx(750)
    assert s.metal == pytest.approx(0)


def test_mex_spot_limit_and_upgrade():
    s = State(no_walk(mex_spots=1, start_energy=10000, start_energy_storage=10000))
    finish(s, s.start(COM, "cormex"))
    assert s.can_start(COM, "cormex") == "no free metal spot"
    s._add_unit("corack", +1)
    ack = s.builder_unit.index("corack")
    metal_before = s.metal
    job = s.start(ack, "cormoho")
    assert s.count("cormex") == 1  # T1 keeps producing while the T2 is built
    t = finish(s, job) - (1870 / 300)
    assert t == pytest.approx(14100 / 220)
    assert s.count("cormex") == 0 and s.count("cormoho") == 1
    assert s.metal_income() == pytest.approx(2 + 4 * 2.3)
    # 50 metal refunded for the replaced extractor
    assert s.metal == pytest.approx(metal_before + (2 + 2.3) * t - 640 + 50)
    assert s.max_metal == 1000 + 600


def test_moho_needs_spot_or_t1_extractor():
    s = State(no_walk(mex_spots=0))
    s._add_unit("corack", +1)
    ack = s.builder_unit.index("corack")
    assert s.can_start(ack, "cormoho") == "no free metal spot"


def test_tech_tree_enforced():
    s = State(no_walk())
    assert "cannot build" in s.can_start(COM, "coralab")
    with pytest.raises(SimError):
        s.start(COM, "coralab")


def test_assist_adds_build_power():
    s = State(no_walk(start_energy=5000, start_energy_storage=5000))
    s._add_unit("corlab", +1)
    lab = s.builder_unit.index("corlab")
    job = s.start(lab, "corck")
    s.assist(COM, job)
    assert finish(s, job) == pytest.approx(3550 / (150 + 300))
    assert sorted(s.idle_builders()) == [COM, lab, s.builder_unit.index("corck")]


def test_assisted_job_limited_by_energy():
    s = State(no_walk())
    s._add_unit("corlab", +1)
    lab = s.builder_unit.index("corlab")
    job = s.start(lab, "corck")
    s.assist(COM, job)
    # 1750 energy needed, 1000 stored, 30 E/s income
    assert finish(s, job) == pytest.approx((1750 - 1000) / 30)


def test_energy_stall():
    s = State(no_walk(start_energy=0))
    job = s.start(COM, "cormex")
    t = finish(s, job)
    assert t == pytest.approx(500 / 30)


def test_copy_is_independent():
    s = State(no_walk())
    job = s.start(COM, "cormex")
    c = s.copy()
    c.advance()
    assert job in s.jobs and job not in c.jobs
    assert s.time == 0 and c.time > 0


# ---------------------------------------------------------------- walking

def test_mobile_builders_walk_before_building():
    s = State(EconConfig(walk_base=2.0))
    job = s.start(COM, "corwin")
    s.advance(until=2.0)
    assert s.jobs[job].progress == 0  # still walking: no progress, nothing spent
    assert s.metrics.metal_spent == 0 and s.metrics.energy_spent == 0
    assert s.metrics.idle_bp_seconds == 0 and s.metrics.wasted_bp_seconds == 0
    assert finish(s, job) == pytest.approx(2.0 + 1680 / 300)


def test_close_and_far_metal_spots():
    s = State(EconConfig(mex_near=1, walk_mex_near=10.0, walk_mex_far=30.0))
    first = finish(s, s.start(COM, "cormex"))
    assert first == pytest.approx(10 + 1870 / 300)
    second = finish(s, s.start(COM, "cormex"))
    assert second - first == pytest.approx(30 + 1870 / 300)


def test_factories_do_not_walk_and_late_helpers_go_idle():
    def lab_builds_a_constructor(assist):
        s = State(EconConfig(walk_base=100.0))
        s._add_unit("corlab", +1)
        job = s.start(s.idle_builders()[-1], "corck")
        if assist:
            s.assist(COM, job)  # the commander would arrive long after the lab is done
        return s, finish(s, job)

    alone, alone_end = lab_builds_a_constructor(False)
    s, end = lab_builds_a_constructor(True)
    assert end == pytest.approx(alone_end) and alone_end < 30  # the lab did not walk
    assert s.walking_bp == 0 and COM in s.idle_builders()
    assert s.idle_bp == s.total_bp
