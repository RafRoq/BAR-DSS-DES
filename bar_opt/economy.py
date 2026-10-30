"""What economy buildings return for what they cost: payback times.

Everything is counted in metal, with energy at the T1 converter rate (70 energy = 1 metal):
that is what surplus energy is worth once converters turn it into metal. A building pays
back after cost / output seconds of running. Converters at exactly that rate never pay back
in this currency; they only make surplus energy spendable as metal.
"""

from dataclasses import dataclass

from .config import EconConfig
from .data import MOBILE, NONE, UnitDef, tech_depth

ENERGY_PER_METAL = 70.0
LAND_BUILDERS = ("ck", "cv", "ack", "acv")  # after the faction prefix: T1 and T2 construction bot and vehicle
NEVER = 0.01  # metal/s; less than this and a building never pays back


@dataclass(frozen=True)
class Payback:
    unit: str
    display: str
    tech: int
    metal_cost: float
    energy_cost: float
    metal: float  # net metal/s it adds (upgrades: on top of what they replace)
    energy: float  # net energy/s it adds
    builder: str  # the fastest builder that can build it
    build_seconds: float  # with that builder alone
    note: str = ""
    needs: str = ""  # "a metal spot", "a geothermal vent", "water", or "" when it can stand anywhere

    @property
    def cost(self) -> float:
        return self.metal_cost + self.energy_cost / ENERGY_PER_METAL

    @property
    def value(self) -> float:
        """Metal-equivalent per second."""
        return self.metal + self.energy / ENERGY_PER_METAL

    @property
    def payback(self) -> float | None:
        """Seconds of running until it has earned its cost; None if it never does."""
        return self.cost / self.value if self.value >= NEVER else None


def output(u: UnitDef, config: EconConfig) -> tuple[float, float]:
    """Net metal/s and energy/s of one building on this map, converters running at capacity."""
    metal = u.metal_make + u.extracts_metal * 1000 * config.metal_per_spot + u.conv_capacity * u.conv_efficiency
    energy = (u.energy_make - u.energy_upkeep + min(config.wind_average, u.wind)
              + (config.tidal_strength if u.tidal else 0.0) - u.conv_capacity)
    return metal, energy


def payback_table(units: dict[str, UnitDef], config: EconConfig) -> list[Payback]:
    """Every economy building the commander and the land construction units can build,
    slowest payback last. Metal extractors above T1 are upgrades: they replace a T1
    extractor on the same spot, so they count only what they add."""
    reachable = tech_depth(units, config.commander)
    prefix = config.commander[:3]
    builders = [units[n] for n in (config.commander, *(prefix + s for s in LAND_BUILDERS))
                if n in reachable and units[n].kind == MOBILE]
    base_mex = min((units[n] for n in reachable if units[n].is_mex and units[n].tech_level <= 1),
                   key=lambda u: u.extracts_metal, default=None)
    rows = []
    for name in reachable:
        u = units[name]
        if u.kind != NONE:
            continue
        metal, energy = output(u, config)
        able = [b for b in builders if name in b.build_options]
        if not able or (metal <= 0 and energy <= 0 and not u.conv_capacity):
            continue  # not an economy building, or only ships can build it
        note = ""
        if u.is_mex and base_mex is not None and u.extracts_metal > base_mex.extracts_metal:
            base_metal, base_energy = output(base_mex, config)
            metal, energy = metal - base_metal, energy - base_energy
            note = f"upgrade of a {base_mex.display_name}"
        elif u.conv_capacity and not u.conv_efficiency:
            continue  # conversion rate missing from the unit data (Legion's T1 converter)
        elif u.conv_capacity:
            note = f"turns {u.conv_capacity:g} energy/s into {u.conv_capacity * u.conv_efficiency:.2f} metal/s"
        fastest = max(able, key=lambda b: b.build_power)
        rows.append(Payback(
            unit=name, display=u.display_name, tech=int(u.tech_level), metal_cost=u.metal, energy_cost=u.energy,
            metal=metal, energy=energy, builder=fastest.display_name,
            build_seconds=u.build_time / fastest.build_power, note=note,
            needs="a metal spot" if u.is_mex else "a geothermal vent" if u.geothermal else "water" if u.tidal else ""))
    return sorted(rows, key=lambda r: (r.payback is None, r.payback or 0.0))
