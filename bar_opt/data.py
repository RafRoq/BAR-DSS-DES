"""Unit definitions for the simulator, loaded from bar_units.json (see bar_unit_extractor.py)."""

import json
import os
from dataclasses import dataclass
from functools import lru_cache

DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bar_units.json")

FACTORY = "factory"  # static builder with buildoptions; produces units, cannot assist
MOBILE = "mobile"  # constructor or commander; builds and assists
NANO = "nano"  # static builder without buildoptions (construction turret); assists only
NONE = "none"


@dataclass(frozen=True, slots=True)
class UnitDef:
    name: str
    display_name: str
    metal: float
    energy: float
    build_time: float
    build_power: float
    metal_make: float
    energy_make: float  # includes negative upkeep (solar collectors)
    energy_upkeep: float  # positive upkeep only (metal extractors)
    wind: float  # max wind output, 0 if not a wind generator
    tidal: bool
    extracts_metal: float
    metal_storage: float
    energy_storage: float
    conv_capacity: float  # energy/s a converter can consume
    conv_efficiency: float  # metal per energy
    tech_level: float
    kind: str
    build_options: tuple[str, ...]
    description: str = ""

    @property
    def is_builder(self) -> bool:
        return self.kind != NONE

    @property
    def can_assist(self) -> bool:
        return self.kind in (MOBILE, NANO)

    @property
    def is_mex(self) -> bool:
        return self.extracts_metal > 0


def _num(value) -> float:
    return float(value or 0)


def _unit_def(name: str, entry: dict) -> UnitDef:
    u = entry["unit"]
    upkeep = _num(u["energyUpkeep"])
    options = tuple(entry.get("buildOptions", ()))
    build_power = _num(u["buildPower"])
    if not u["isBuilder"] or build_power <= 0:
        kind = NONE
    elif _num(u["speed"]) > 0:
        kind = MOBILE
    elif options:
        kind = FACTORY
    else:
        kind = NANO
    return UnitDef(
        name=name,
        display_name=entry.get("displayName", name),
        metal=_num(u["metalCost"]),
        energy=_num(u["energyCost"]),
        build_time=_num(u["buildTime"]),
        build_power=build_power,
        metal_make=_num(u["metalProduced"]),
        energy_make=_num(u["energyProduced"]) + max(0.0, -upkeep),
        energy_upkeep=max(0.0, upkeep),
        wind=_num(u["windGenerator"]),
        tidal=_num(u["tidalGenerator"]) > 0,
        extracts_metal=_num(u["extractsMetal"]),
        metal_storage=_num(u["metalStorage"]),
        energy_storage=_num(u["energyStorage"]),
        conv_capacity=_num(u["energyConversionCapacity"]),
        conv_efficiency=_num(u["energyConversionEfficiency"]),
        tech_level=_num(u["techLevel"]) or 1.0,
        kind=kind,
        build_options=options,
        description=entry.get("description", ""),
    )


def tech_depth(units: dict[str, UnitDef], commander: str) -> dict[str, int]:
    """Number of build steps from the commander to each unit it can eventually build."""
    depth = {commander: 0}
    frontier = [commander]
    while frontier:
        nxt = []
        for name in frontier:
            for option in units[name].build_options:
                if option in units and option not in depth:
                    depth[option] = depth[name] + 1
                    nxt.append(option)
        frontier = nxt
    return depth


@lru_cache(maxsize=4)
def load_units(path: str = DATA_FILE) -> dict[str, UnitDef]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)["units"]
    return {name: _unit_def(name, entry) for name, entry in raw.items()}
