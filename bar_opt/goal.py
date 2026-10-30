"""Goal specification and the weighted cost of a finished run."""

import math
from dataclasses import dataclass, field, fields

import yaml

from .config import EconConfig
from .sim import State


@dataclass(frozen=True)
class Weights:
    """Cost per unit of each penalty, in seconds of finish time.

    Defaults follow the priority stall > overflow > idle > time: one second of the
    commander's 300 BP fully stalled costs 10 s, 1 metal lost to a full storage 0.2 s,
    one wasted commander-second 2 s.

    `idle` applies to wasted build power: idle builders that could have helped (commander,
    constructors, nano turrets; not factories) while income exceeded spending on both metal
    and energy. Factories with nothing to build and builders held back because income is
    fully spent are not penalized, since more build power could not have been paid for."""

    stall: float = 1 / 30  # per lost build-power-second
    overflow_metal: float = 0.2  # per metal
    overflow_energy: float = 0.01  # per energy
    idle: float = 1 / 150  # per wasted build-power-second
    late: float = 2.0  # per second a milestone is late


@dataclass(frozen=True)
class Milestone:
    """Units to have by a deadline, e.g. 3 Pounders and a Wolverine by 2:30."""

    at: float  # game seconds
    units: dict[str, int]

    @classmethod
    def from_dict(cls, data: dict) -> "Milestone":
        return cls(at=parse_time(data["at"]), units={str(k): int(v) for k, v in data["units"].items()})


def parse_time(value) -> float:
    """Seconds from 150, 150.0 or "2:30"."""
    if isinstance(value, str) and ":" in value:
        minutes, seconds = value.split(":")
        return 60 * float(minutes) + float(seconds)
    return float(value)


@dataclass(frozen=True)
class Goal:
    """Units to have at the end (`targets`), and optionally units to have by earlier deadlines
    (`milestones`). Milestone units count towards the end too: built units stay built."""

    name: str
    targets: dict[str, int]
    max_time: float = 900.0
    weights: Weights = field(default_factory=Weights)
    config: EconConfig = field(default_factory=EconConfig)
    milestones: tuple[Milestone, ...] = ()

    def __post_init__(self):
        if self.milestones:
            merged = dict(self.targets)
            for m in self.milestones:
                for unit, n in m.units.items():
                    merged[unit] = max(merged.get(unit, 0), n)
            object.__setattr__(self, "targets", merged)

    def milestone_times(self, state: State) -> list[float | None]:
        """When each milestone was met, or None if it is not met yet."""
        out = []
        for m in self.milestones:
            times = [state.reached.get(u, ())[n - 1] if len(state.reached.get(u, ())) >= n else None
                     for u, n in m.units.items()]
            out.append(None if None in times else max(times, default=0.0))
        return out

    def lateness(self, state: State) -> float:
        """Seconds the milestones are late in total; an unmet milestone is late since its
        deadline (a lower bound on what it will end up costing)."""
        late = 0.0
        for m, met in zip(self.milestones, self.milestone_times(state)):
            late += max(0.0, (met if met is not None else state.time) - m.at)
        return late

    def satisfied(self, state: State) -> bool:
        return all(state.count(unit) >= n for unit, n in self.targets.items())

    def missing(self, state: State) -> dict[str, int]:
        return {u: n - state.count(u) for u, n in self.targets.items() if state.count(u) < n}

    def evaluate(self, state: State) -> dict:
        """Cost breakdown of a state, normally the one where the goal was just reached."""
        m, w = state.metrics, self.weights
        reached = self.satisfied(state)
        finish = state.time if reached else math.inf
        penalties = (
            w.stall * m.stall_bp_seconds
            + w.overflow_metal * m.overflow_metal
            + w.overflow_energy * m.overflow_energy
            + w.idle * m.wasted_bp_seconds
            + w.late * self.lateness(state)
        )
        return {
            "reached": reached,
            "finish_time": finish,
            "stall_bp_seconds": m.stall_bp_seconds,
            "stall_seconds": m.stall_seconds,
            "overflow_metal": m.overflow_metal,
            "overflow_energy": m.overflow_energy,
            "idle_bp_seconds": m.idle_bp_seconds,
            "wasted_bp_seconds": m.wasted_bp_seconds,
            "milestones_late": self.lateness(state),
            "milestone_times": self.milestone_times(state),
            "cost": finish + penalties,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Goal":
        known = {f.name for f in fields(Weights)}
        weights = data.get("weights") or {}
        unknown = set(weights) - known
        if unknown:
            raise ValueError(f"Unknown weight keys: {sorted(unknown)}")
        return cls(
            name=data.get("name", "goal"),
            targets={str(k): int(v) for k, v in data["targets"].items()},
            max_time=float(data.get("max_time", 900)),
            weights=Weights(**weights),
            config=EconConfig.from_dict(data.get("config")),
            milestones=tuple(Milestone.from_dict(m) for m in data.get("milestones") or ()),
        )

    @classmethod
    def from_yaml(cls, path: str) -> "Goal":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(yaml.safe_load(f))
