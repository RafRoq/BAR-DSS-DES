"""Map and game assumptions. Everything the simulator does not model explicitly lives here."""

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class EconConfig:
    # Map
    wind_average: float = 14.2  # mean wind speed; a turbine yields min(wind, its max of 25)
    tidal_strength: float = 21.0
    metal_per_spot: float = 2.3  # metal/s of a T1 extractor (extractsmetal 0.001) on one spot
    mex_spots: int = 4  # metal spots you take: usually 3-4 lie close together, the rest far away

    # Walking (off by default): a commander or constructor walks before it starts or assists a
    # job, and adds no build power until it arrives. Factories and nano turrets never walk.
    # Walking depends on the map, start position and role, so the optimizer plans the economy
    # and the in-game guide measures the gaps between jobs instead. One game on Supreme
    # Isthmus measured about 1 s between base buildings, 12 s to a close spot, 35 s to a far one.
    walk_base: float = 0.0  # seconds to reach a job in the base
    mex_near: int = 4  # spots close to the base; the others are far
    walk_mex_near: float = 0.0  # seconds to reach a close spot
    walk_mex_far: float = 0.0  # seconds to reach a far spot

    # Game rules (BAR defaults, see README)
    start_metal: float = 1000.0
    start_energy: float = 1000.0
    start_metal_storage: float = 1000.0  # total team storage at game start, commander included
    start_energy_storage: float = 1000.0
    converter_level: float = 0.75  # converters use energy above this fraction of storage
    commander: str = "corcom"

    @classmethod
    def from_dict(cls, values: dict | None) -> "EconConfig":
        values = values or {}
        known = {f.name for f in fields(cls)}
        unknown = set(values) - known
        if unknown:
            raise ValueError(f"Unknown config keys: {sorted(unknown)}")
        return cls(**values)
