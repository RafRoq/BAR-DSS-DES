"""Map and game assumptions. Everything the simulator does not model explicitly lives here."""

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class EconConfig:
    # Map
    wind_average: float = 14.2  # mean wind speed; a turbine yields min(wind, its max of 25)
    tidal_strength: float = 21.0
    metal_per_spot: float = 2.3  # metal/s of a T1 extractor (extractsmetal 0.001) on one spot
    mex_spots: int = 6  # spots reachable by the base; travel time between them is not modelled

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
