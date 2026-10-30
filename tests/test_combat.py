"""Combat data extraction (bar_unit_extractor) and the analysis in bar_opt.combat."""

import pytest

import bar_unit_extractor as extractor
from bar_opt.combat import (army, can_target, compositions, dps, early_units, even_armies, fight, ground_army,
                            lanchester, load_combat, reach, solve_zero_sum)


@pytest.fixture(scope="module")
def units():
    return load_combat()


def test_lua_table_literals_are_parsed_not_executed():
    text = """
    local x = 5
    return {
        unit = {
            health = 280, speed = 45 * 2, name = "Grunt", canfly = false,
            weapons = { [1] = { def = "LASER" }, [2] = { def = "GUN" } },
            list = { "a", "b" },
            damage = { default = 37, vtol = 7 },
            dangerous = os.execute("rm -rf /"),  -- a call: ignored, never run
            -- a comment = 3,
        },
    }
    """
    unit = extractor.parse_lua_return_table(text)["unit"]
    assert unit["health"] == 280 and unit["speed"] == 90 and unit["name"] == "Grunt"
    assert unit["canfly"] is False and unit["dangerous"] is None
    assert [w["def"] for w in unit["weapons"]] == ["LASER", "GUN"]
    assert unit["list"] == ["a", "b"]
    assert unit["damage"] == {"default": 37.0, "vtol": 7.0}


def test_categories_follow_bars_rules():
    plane = extractor.unit_categories({"canfly": True, "speed": 200, "weapondefs": {}})
    assert "VTOL" in plane and "NOTAIR" not in plane and "SURFACE" not in plane
    sub = extractor.unit_categories({"movementclass": "UBOAT4", "speed": 60, "minwaterdepth": 15,
                                     "weapondefs": {"torp": {}}})
    assert "NOTSUB" not in sub and "UNDERWATER" in sub and "WEAPON" in sub
    tower = extractor.unit_categories({"speed": 0, "weapondefs": {"laser": {}}})
    assert {"NOTMOBILE", "SURFACE", "NOTSUB", "NOTAIR"} <= set(tower)


def test_combat_stats_from_bar_data(units):
    grunt, pawn = units["corak"], units["armpw"]
    assert dps(grunt, pawn) == pytest.approx(37 / 0.5)
    assert dps(pawn, grunt) == pytest.approx(9 * 3 / 0.3)  # 3-shot bursts
    assert reach(grunt, pawn) == 215
    assert dps(units["corcom"], grunt) < 1000  # the D-gun is manual: not part of steady DPS
    assert dps(units["corrl"], grunt) == 0  # an anti-air turret cannot shoot ground units
    assert not can_target(units["corrl"].weapons[0], grunt)
    assert lanchester(grunt, pawn) > 0


def test_early_units_come_from_the_t1_factories(units):
    cortex = early_units(units, "cor")
    assert "corak" in cortex and "corgator" in cortex and "corllt" not in cortex
    assert "corllt" in early_units(units, "cor", mobile=False)
    assert all(units[n].mobile for n in cortex)


def test_fights_are_plausible_and_repeatable(units):
    grunt, pawn = units["corak"], units["armpw"]
    duel = fight([grunt], [pawn])
    assert duel.left_a == 0 and 0.2 < duel.left_b < 0.6  # the pawn wins: 90 DPS and 370 HP vs 74 and 280
    mirror = fight(army(units, {"corak": 1}, 1000), army(units, {"corak": 1}, 1000))
    assert abs(mirror.score) < 0.3
    again = fight(army(units, {"corwolv": 1}, 1000), army(units, {"armpw": 1}, 1000), seed=3)
    assert again.score == fight(army(units, {"corwolv": 1}, 1000), army(units, {"armpw": 1}, 1000), seed=3).score
    tower = fight(army(units, {"corllt": 1}, 1000), army(units, {"armpw": 1}, 1000))
    assert tower.score > 0.5  # equal-cost raiders walking into laser towers lose


def test_weapon_details_from_bar_data(units):
    # Prometheus' heat ray fires for 1.8 s, then cools down for 3 s
    assert dps(units["legaheattank"], units["corak"]) == pytest.approx(25 / 0.033 * 1.8 / 4.8)
    # amphibious units' torpedoes cannot hit a bot on land; their other weapon can
    assert len(units["coramph"].weapons) == 1 and len(units["legamph"].weapons) == 1


def test_t2_armies_leave_out_crawling_bombs_and_decoys(units):
    armada = ground_army(units, "arm", tech=2)
    assert "armbull" in armada and "armvader" not in armada and "armdecom" not in armada
    assert "armack" not in armada and "armaak" not in armada  # builders and anti-air
    cortex = ground_army(units, "cor", tech=2)
    assert "corsumo" in cortex and "corroach" not in cortex and "cordecom" not in cortex
    assert ground_army(units, "cor", tech=1) == [n for n in early_units(units, "cor")
                                                 if dps(units[n], units["corak"]) > 0]


def test_armies_follow_the_budget_and_the_shares(units):
    grunts = army(units, {"corak": 1}, 1000)
    assert len(grunts) == round(1000 / units["corak"].cost)
    assert len(army(units, {"corsumo": 1}, 500)) == 1  # always at least one unit
    mixed = army(units, {"correap": 0.5, "cormart": 0.3, "corpyro": 0.2}, 6000)
    spent = {n: sum(u.cost for u in mixed if u.name == n) for n in ("correap", "cormart", "corpyro")}
    assert abs(sum(spent.values()) - 6000) <= units["correap"].cost / 2
    assert spent["correap"] > spent["cormart"] > spent["corpyro"] > 0
    # a small share of a small budget gets no unit instead of a whole expensive one
    assert {u.name for u in army(units, {"corak": 0.95, "corsumo": 0.05}, 1000)} == {"corak"}


def test_even_armies_cost_about_the_same(units):
    a, b = even_armies(units, {"corak": 1.0}, {"corsumo": 1.0}, 3000)
    cost_a, cost_b = sum(u.cost for u in a), sum(u.cost for u in b)
    assert len(b) == 1 and abs(cost_a - cost_b) <= units["corak"].cost / 2


def test_zero_sum_solver_and_compositions():
    rock_paper_scissors = [[0, -1, 1], [1, 0, -1], [-1, 1, 0]]
    rows, cols, value = solve_zero_sum(rock_paper_scissors)
    assert rows == pytest.approx([1 / 3] * 3, abs=0.02) and value == pytest.approx(0, abs=0.02)
    dominant = [[1, 1], [0, -1]]
    rows, _, value = solve_zero_sum(dominant)
    assert rows[0] > 0.99 and value == pytest.approx(1, abs=0.01)
    assert len(compositions(["a", "b", "c", "d"])) == 4 + 6
