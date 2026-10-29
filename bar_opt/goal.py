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
    one idle commander-second 2 s."""

    stall: float = 1 / 30  # per lost build-power-second
    overflow_metal: float = 0.2  # per metal
    overflow_energy: float = 0.01  # per energy
    idle: float = 1 / 150  # per idle build-power-second


@dataclass(frozen=True)
class Goal:
    name: str
    targets: dict[str, int]
    max_time: float = 900.0
    weights: Weights = field(default_factory=Weights)
    config: EconConfig = field(default_factory=EconConfig)

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
            + w.idle * m.idle_bp_seconds
        )
        return {
            "reached": reached,
            "finish_time": finish,
            "stall_bp_seconds": m.stall_bp_seconds,
            "stall_seconds": m.stall_seconds,
            "overflow_metal": m.overflow_metal,
            "overflow_energy": m.overflow_energy,
            "idle_bp_seconds": m.idle_bp_seconds,
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
        )

    @classmethod
    def from_yaml(cls, path: str) -> "Goal":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(yaml.safe_load(f))
