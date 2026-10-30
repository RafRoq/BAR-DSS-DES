"""Payback times of economy buildings (bar_opt.economy)."""

import pytest

from bar_opt.config import EconConfig
from bar_opt.data import load_units
from bar_opt.economy import ENERGY_PER_METAL, payback_table


@pytest.fixture(scope="module")
def cortex():
    return {r.unit: r for r in payback_table(load_units(), EconConfig(wind_average=14.2, metal_per_spot=2.3))}


def test_payback_counts_energy_at_the_converter_rate(cortex):
    mex = cortex["cormex"]
    assert mex.cost == pytest.approx(50 + 500 / ENERGY_PER_METAL)
    assert mex.value == pytest.approx(2.3 - 3 / ENERGY_PER_METAL)  # minus its energy upkeep
    assert mex.payback == pytest.approx(mex.cost / mex.value)
    assert mex.needs == "a metal spot" and mex.builder == "Cortex Commander"
    assert cortex["corwin"].energy == pytest.approx(14.2)


def test_upgrades_count_what_they_add_and_converters_never_pay_back(cortex):
    moho = cortex["cormoho"]
    assert moho.metal == pytest.approx(2.3 * 3) and "upgrade" in moho.note
    assert cortex["cormakr"].payback is None
    assert cortex["corgeo"].needs == "a geothermal vent" and cortex["corbhmth"].needs == "a geothermal vent"
    assert cortex["corfus"].needs == "" and cortex["corfus"].builder.startswith("Advanced Construction")


def test_only_land_economy_buildings_are_listed(cortex):
    assert "coruwfus" not in cortex  # only ships build it
    assert "corkorg" not in cortex  # a T3 unit that happens to make energy
    assert "corcom" not in cortex
    paybacks = [r.payback for r in cortex.values() if r.payback is not None]
    assert paybacks == sorted(paybacks)
