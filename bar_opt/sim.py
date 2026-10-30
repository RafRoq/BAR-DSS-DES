"""Event-driven economy simulator.

Between events every rate is constant, so the state is advanced analytically from one
event to the next: a job finishing, a resource reaching 0 or its storage cap, or energy
reaching the converter level. The simulator makes no decisions; callers start and assist
jobs with idle builders and then advance time.

Model (see README for the deviations from the real game):
- A job with build power BP progresses at BP / buildTime per second and drains
  metal and energy in proportion to progress.
- When a resource is at 0 and demand exceeds income, every job is slowed by the same
  factor s (proportional allocation), so consumption matches income.
- Converters use energy above converter_level * storage, up to their capacity, most
  efficient first (game_energy_conversion.lua); at the level they convert the surplus.
- Income above a full storage is lost and counted as overflow.
- Commanders and constructors walk to a job before they add build power (config walk_*);
  the k-th metal spot used, and the k-th extractor upgraded, is close or far by k.
"""

import math
from dataclasses import dataclass, replace
from typing import NamedTuple

from .config import EconConfig
from .data import MOBILE, UnitDef, load_units

INF = math.inf
EPS = 1e-6  # absolute tolerance for resources, which are O(1000)
MAX_ZERO_STEPS = 1000


class SimError(Exception):
    pass


@dataclass(slots=True)
class Metrics:
    stall_bp_seconds: float = 0.0  # build power lost to stalling: (1 - s) * active BP * dt
    stall_seconds: float = 0.0  # wall-clock time with s < 1
    idle_bp_seconds: float = 0.0  # build power of idle builders * dt
    # Idle build power that could have helped (builders able to assist, so not factories)
    # while income exceeded spending on both resources: the build power actually wasted.
    wasted_bp_seconds: float = 0.0
    overflow_metal: float = 0.0
    overflow_energy: float = 0.0
    metal_produced: float = 0.0
    energy_produced: float = 0.0
    metal_spent: float = 0.0
    energy_spent: float = 0.0
    energy_converted: float = 0.0
    metal_from_conversion: float = 0.0


class Job:
    __slots__ = ("id", "unit", "progress", "builders", "bp", "start_time", "upgrades", "walking")

    def __init__(self, job_id, unit, builder, bp, start_time, upgrades):
        self.id = job_id
        self.unit = unit
        self.progress = 0.0
        self.builders = [builder]  # first entry started the job
        self.bp = bp  # build power of the builders that have arrived
        self.start_time = start_time
        self.upgrades = upgrades  # name of the extractor this one replaces, or None
        self.walking: list[tuple[float, int, float]] = []  # (arrival time, builder, build power)

    def copy(self) -> "Job":
        job = Job.__new__(Job)
        job.id, job.unit, job.progress = self.id, self.unit, self.progress
        job.builders, job.bp = list(self.builders), self.bp
        job.start_time, job.upgrades = self.start_time, self.upgrades
        job.walking = list(self.walking)
        return job

    def planned_bp(self) -> float:
        """Build power once every builder sent to the job has arrived."""
        return self.bp + sum(bp for _, _, bp in self.walking)


class Segment(NamedTuple):
    """One stretch of time with constant rates."""

    t0: float
    t1: float
    metal0: float
    metal1: float
    energy0: float
    energy1: float
    net_metal: float
    net_energy: float
    speed: float  # build speed factor s (1 = no stall)
    idle_bp: float
    total_bp: float
    units: int
    max_metal: float
    max_energy: float
    metal_income: float  # extractors, commander and converters
    metal_spend: float
    energy_income: float  # production minus upkeep
    energy_spend: float
    converter_use: float


class Recorder:
    """Full history of one run, for plots and reports. Not copied with the state."""

    def __init__(self):
        self.segments: list[Segment] = []
        # job id -> {"unit", "start", "end", "joins": [(time, builder id)],
        #            "arrivals": [(time, builder id)]}  (joins are orders; arrivals end the walk)
        self.jobs: dict[int, dict] = {}
        self.builder_units: dict[int, str] = {}
        self.builder_spawn: dict[int, float] = {}
        self.unit_events: list[tuple[float, str, int]] = []  # (time, unit, +1 built / -1 removed)


class State:
    __slots__ = (
        "config", "units", "time", "metal", "energy", "max_metal", "max_energy",
        "metal_make", "mex_metal", "energy_make", "energy_upkeep", "converters",
        "conv_capacity", "counts", "builder_unit", "builder_job", "idle_bp", "total_bp", "idle_helper_bp",
        "jobs", "next_job_id", "spots_used", "upgrades_started", "walking_bp", "free_mexes", "metrics", "recorder",
        "_last_dt",
    )

    def __init__(self, config: EconConfig | None = None, units: dict[str, UnitDef] | None = None,
                 record: bool = False):
        self.config = config or EconConfig()
        self.units = units or load_units()
        self.time = 0.0
        self.metal = self.config.start_metal
        self.energy = self.config.start_energy
        # BAR resets team storage to an absolute value at GameStart, commander included,
        # so the commander's own storage is not added on top.
        commander = self.units[self.config.commander]
        self.max_metal = self.config.start_metal_storage - commander.metal_storage
        self.max_energy = self.config.start_energy_storage - commander.energy_storage
        self.metal_make = 0.0
        self.mex_metal = 0.0
        self.energy_make = 0.0
        self.energy_upkeep = 0.0
        self.converters: tuple[tuple[float, float], ...] = ()  # (efficiency, capacity)
        self.conv_capacity = 0.0
        self.counts: dict[str, int] = {}
        self.builder_unit: list[str] = []
        self.builder_job: list[int] = []  # job id, or -1 when idle
        self.idle_bp = 0.0
        self.total_bp = 0.0
        self.idle_helper_bp = 0.0  # idle build power of builders that can assist
        self.jobs: dict[int, Job] = {}
        self.next_job_id = 0
        self.spots_used = 0
        self.upgrades_started = 0
        self.walking_bp = 0.0  # build power of builders walking to a job
        self.free_mexes: dict[str, int] = {}  # finished extractors not being upgraded
        self.metrics = Metrics()
        self.recorder = Recorder() if record else None
        self._last_dt = 0.0
        self._add_unit(self.config.commander, +1)

    def copy(self) -> "State":
        new = State.__new__(State)
        new.config, new.units = self.config, self.units
        new.time, new.metal, new.energy = self.time, self.metal, self.energy
        new.max_metal, new.max_energy = self.max_metal, self.max_energy
        new.metal_make, new.mex_metal = self.metal_make, self.mex_metal
        new.energy_make, new.energy_upkeep = self.energy_make, self.energy_upkeep
        new.converters, new.conv_capacity = self.converters, self.conv_capacity
        new.counts = dict(self.counts)
        new.builder_unit = list(self.builder_unit)
        new.builder_job = list(self.builder_job)
        new.idle_bp, new.total_bp, new.idle_helper_bp = self.idle_bp, self.total_bp, self.idle_helper_bp
        new.jobs = {k: j.copy() for k, j in self.jobs.items()}
        new.next_job_id = self.next_job_id
        new.spots_used = self.spots_used
        new.upgrades_started, new.walking_bp = self.upgrades_started, self.walking_bp
        new.free_mexes = dict(self.free_mexes)
        new.metrics = replace(self.metrics)
        new.recorder = None
        new._last_dt = 0.0
        return new

    # ------------------------------------------------------------------ queries

    def count(self, unit: str) -> int:
        return self.counts.get(unit, 0)

    def idle_builders(self) -> list[int]:
        return [b for b, j in enumerate(self.builder_job) if j < 0]

    def metal_income(self) -> float:
        return self.metal_make + self.mex_metal

    def energy_income(self) -> float:
        return self.energy_make - self.energy_upkeep

    def can_start(self, builder: int, unit: str) -> str | None:
        """Why `builder` cannot start `unit` now, or None if it can."""
        if self.builder_job[builder] >= 0:
            return "builder is busy"
        if unit not in self.units[self.builder_unit[builder]].build_options:
            return f"{self.builder_unit[builder]} cannot build {unit}"
        target = self.units.get(unit)
        if target is None:
            return f"no data for {unit}"
        if target.is_mex and self.spots_used >= self.config.mex_spots and self._upgradable(target) is None:
            return "no free metal spot"
        return None

    def can_assist(self, builder: int, job_id: int) -> str | None:
        if self.builder_job[builder] >= 0:
            return "builder is busy"
        if not self.units[self.builder_unit[builder]].can_assist:
            return f"{self.builder_unit[builder]} cannot assist"
        if job_id not in self.jobs:
            return "no such job"
        return None

    # ------------------------------------------------------------------ actions

    def start(self, builder: int, unit: str) -> int:
        reason = self.can_start(builder, unit)
        if reason:
            raise SimError(f"t={self.time:.2f}: cannot start {unit} with builder {builder}: {reason}")
        upgrades = None
        walk = self.config.walk_base
        if self.units[unit].is_mex:
            if self.spots_used < self.config.mex_spots:
                walk = self._spot_walk(self.spots_used)
                self.spots_used += 1
            else:
                upgrades = self._upgradable(self.units[unit])
                self.free_mexes[upgrades] -= 1
                walk = self._spot_walk(self.upgrades_started)
                self.upgrades_started += 1
        bp = self._builder_bp(builder)
        job = Job(self.next_job_id, unit, builder, 0.0, self.time, upgrades)
        self.next_job_id += 1
        self.jobs[job.id] = job
        self.builder_job[builder] = job.id
        self._set_busy(builder, bp)
        if self.recorder:
            self.recorder.jobs[job.id] = {"unit": unit, "start": self.time, "end": None,
                                          "joins": [(self.time, builder)], "arrivals": []}
        self._join(job, builder, bp, walk)
        return job.id

    def assist(self, builder: int, job_id: int) -> None:
        reason = self.can_assist(builder, job_id)
        if reason:
            raise SimError(f"t={self.time:.2f}: builder {builder} cannot assist job {job_id}: {reason}")
        job = self.jobs[job_id]
        bp = self._builder_bp(builder)
        job.builders.append(builder)
        self.builder_job[builder] = job_id
        self._set_busy(builder, bp)
        if self.recorder:
            self.recorder.jobs[job_id]["joins"].append((self.time, builder))
        self._join(job, builder, bp, self.config.walk_base)

    def advance(self, until: float = INF) -> list[int]:
        """Run until at least one job finishes or `until` is reached.

        Returns the ids of the jobs that finished. Returns an empty list when `until` was
        reached, or when until is INF and nothing will ever change again (no jobs, and
        both resources are capped or static)."""
        zero_steps = 0
        while self.time < until:
            done = self._step(until)
            if done is None:
                return []
            if done:
                return done
            zero_steps = zero_steps + 1 if self._last_dt == 0 else 0
            if zero_steps > MAX_ZERO_STEPS:
                raise SimError(f"t={self.time:.4f}: simulation is not advancing")
        return []

    # ------------------------------------------------------------------ internals

    def _builder_bp(self, builder: int) -> float:
        return self.units[self.builder_unit[builder]].build_power

    def _spot_walk(self, k: int) -> float:
        return self.config.walk_mex_near if k < self.config.mex_near else self.config.walk_mex_far

    def _join(self, job: Job, builder: int, bp: float, walk: float) -> None:
        """Add a builder's build power to a job, after it walks there if it is mobile."""
        if self.units[self.builder_unit[builder]].kind != MOBILE or walk <= 0:
            self._arrive(job, builder, bp)
            return
        job.walking.append((self.time + walk, builder, bp))
        self.walking_bp += bp

    def _arrive(self, job: Job, builder: int, bp: float) -> None:
        job.bp += bp
        if self.recorder:
            self.recorder.jobs[job.id]["arrivals"].append((self.time, builder))

    def _set_busy(self, builder: int, bp: float) -> None:
        self.idle_bp -= bp
        if self.units[self.builder_unit[builder]].can_assist:
            self.idle_helper_bp -= bp

    def _upgradable(self, target: UnitDef) -> str | None:
        for name, n in self.free_mexes.items():
            if n > 0 and self.units[name].extracts_metal < target.extracts_metal:
                return name
        return None

    def _add_unit(self, name: str, sign: int) -> None:
        u = self.units[name]
        cfg = self.config
        self.counts[name] = self.counts.get(name, 0) + sign
        if self.recorder:
            self.recorder.unit_events.append((self.time, name, sign))
        self.metal_make += sign * u.metal_make
        self.mex_metal += sign * u.extracts_metal * 1000.0 * cfg.metal_per_spot
        energy = u.energy_make
        if u.wind > 0:
            energy += min(cfg.wind_average, u.wind)
        if u.tidal:
            energy += cfg.tidal_strength
        self.energy_make += sign * energy
        self.energy_upkeep += sign * u.energy_upkeep
        self.max_metal += sign * u.metal_storage
        self.max_energy += sign * u.energy_storage
        if u.conv_capacity > 0:
            caps = dict(self.converters)
            caps[u.conv_efficiency] = caps.get(u.conv_efficiency, 0.0) + sign * u.conv_capacity
            self.converters = tuple(sorted(((e, c) for e, c in caps.items() if c > 0), reverse=True))
            self.conv_capacity = sum(c for _, c in self.converters)
        if u.is_mex:
            self.free_mexes[name] = self.free_mexes.get(name, 0) + (sign if sign > 0 else 0)
        if sign > 0 and u.is_builder:
            builder_id = len(self.builder_unit)
            self.builder_unit.append(name)
            self.builder_job.append(-1)
            self.idle_bp += u.build_power
            self.total_bp += u.build_power
            if u.can_assist:
                self.idle_helper_bp += u.build_power
            if self.recorder:
                self.recorder.builder_units[builder_id] = name
                self.recorder.builder_spawn[builder_id] = self.time
        self._clamp_storage()

    def _clamp_storage(self) -> None:
        if self.metal > self.max_metal:
            self.metrics.overflow_metal += self.metal - self.max_metal
            self.metal = self.max_metal
        if self.energy > self.max_energy:
            self.metrics.overflow_energy += self.energy - self.max_energy
            self.energy = self.max_energy

    def _conv_metal(self, energy_use: float) -> float:
        metal = 0.0
        for efficiency, capacity in self.converters:
            if energy_use <= 0:
                break
            used = min(energy_use, capacity)
            metal += used * efficiency
            energy_use -= used
        return metal

    def _rates(self):
        demand_m = demand_e = 0.0
        for job in self.jobs.values():
            u = self.units[job.unit]
            rate = job.bp / u.build_time
            demand_m += rate * u.metal
            demand_e += rate * u.energy

        capacity = self.conv_capacity
        level = self.config.converter_level * self.max_energy
        e_net = self.energy_make - self.energy_upkeep
        mex = self.mex_metal
        m_empty = self.metal <= EPS
        e_empty = self.energy <= EPS
        if e_empty and e_net < 0 and self.energy_upkeep > 0:
            # Not enough energy for extractor upkeep: extractors run partially.
            mex *= max(0.0, self.energy_make) / self.energy_upkeep
            e_net = 0.0
        metal_in = self.metal_make + mex
        above = self.energy > level + EPS
        below = self.energy < level - EPS

        def conv_use(s):
            if capacity <= 0 or below:
                return 0.0
            if above:
                return capacity
            return min(capacity, max(0.0, e_net - s * demand_e))

        def speed(use):
            s = 1.0
            if m_empty and demand_m > 0:
                s = min(s, (metal_in + self._conv_metal(use)) / demand_m)
            if e_empty and demand_e > 0:
                s = min(s, (e_net - use) / demand_e)
            return max(0.0, s)

        if capacity > 0 and not above and not below and m_empty and demand_m > 0:
            # Metal-starved while energy sits on the converter level: the build speed
            # sets the energy surplus, which sets conversion, which sets the build speed.
            if speed(conv_use(1.0)) >= 1.0:
                s = 1.0
            else:
                lo, hi = 0.0, 1.0
                for _ in range(50):
                    mid = 0.5 * (lo + hi)
                    if speed(conv_use(mid)) > mid:
                        lo = mid
                    else:
                        hi = mid
                s = lo
        else:
            s = speed(conv_use(1.0))
        use = conv_use(s)
        conv_metal = self._conv_metal(use)

        d_metal = metal_in + conv_metal - s * demand_m
        d_energy = e_net - use - s * demand_e
        if m_empty and d_metal < 0:
            d_metal = 0.0
        if e_empty and d_energy < 0:
            d_energy = 0.0
        if capacity > 0 and not above and not below and 0.0 < use < capacity:
            d_energy = 0.0
        overflow_m = overflow_e = 0.0
        if self.metal >= self.max_metal - EPS and d_metal > 0:
            overflow_m, d_metal = d_metal, 0.0
        if self.energy >= self.max_energy - EPS and d_energy > 0:
            overflow_e, d_energy = d_energy, 0.0
        return (s, use, conv_metal, metal_in, e_net, d_metal, d_energy, overflow_m, overflow_e,
                demand_m, demand_e, level, above, below)

    def _step(self, until: float) -> list[int] | None:
        (s, use, conv_metal, metal_in, e_net, d_metal, d_energy, overflow_m, overflow_e,
         demand_m, demand_e, level, above, below) = self._rates()
        has_conv = self.conv_capacity > 0

        dt = until - self.time
        for job in self.jobs.values():
            rate = s * job.bp
            if rate > 0:
                dt = min(dt, (self.units[job.unit].build_time - job.progress) / rate)
            for arrival, _, _ in job.walking:
                dt = min(dt, arrival - self.time)
        if d_metal < 0:
            dt = min(dt, self.metal / -d_metal)
        elif d_metal > 0:
            dt = min(dt, (self.max_metal - self.metal) / d_metal)
        if d_energy < 0:
            target = level if has_conv and above else 0.0
            dt = min(dt, (self.energy - target) / -d_energy)
        elif d_energy > 0:
            target = level if has_conv and below else self.max_energy
            dt = min(dt, (target - self.energy) / d_energy)
        if dt == INF:
            return None
        dt = max(0.0, dt)
        self._last_dt = dt

        t0, m0, e0 = self.time, self.metal, self.energy
        self.metal = self._snap(self.metal + d_metal * dt, self.max_metal, None)
        self.energy = self._snap(self.energy + d_energy * dt, self.max_energy, level if has_conv else None)
        self.time += dt

        m = self.metrics
        active_bp = self.total_bp - self.idle_bp - self.walking_bp
        if s < 1.0 and self.jobs:
            m.stall_bp_seconds += (1.0 - s) * active_bp * dt
            m.stall_seconds += dt
        m.idle_bp_seconds += self.idle_bp * dt
        # Idle builders only waste build power while income exceeds what is being built
        # (energy before converters): while storage drains or sits empty, more build
        # power would just stall.
        surplus_m = metal_in + conv_metal - s * demand_m
        surplus_e = e_net - s * demand_e
        if surplus_m > EPS and surplus_e > EPS:
            m.wasted_bp_seconds += self.idle_helper_bp * dt
        m.overflow_metal += overflow_m * dt
        m.overflow_energy += overflow_e * dt
        m.metal_produced += (metal_in + conv_metal) * dt
        m.energy_produced += max(0.0, self.energy_make) * dt
        m.metal_spent += s * demand_m * dt
        m.energy_spent += s * demand_e * dt
        m.energy_converted += use * dt
        m.metal_from_conversion += conv_metal * dt

        if self.recorder:
            self.recorder.segments.append(Segment(
                t0, self.time, m0, self.metal, e0, self.energy,
                metal_in + conv_metal - s * demand_m, e_net - use - s * demand_e,
                s, self.idle_bp, self.total_bp, sum(self.counts.values()),
                self.max_metal, self.max_energy,
                metal_in + conv_metal, s * demand_m, e_net, s * demand_e, use,
            ))

        finished = []
        for job in self.jobs.values():
            job.progress += s * job.bp * dt
            if job.walking:
                arrived = [w for w in job.walking if w[0] <= self.time + 1e-9]
                job.walking = [w for w in job.walking if w[0] > self.time + 1e-9]
                for _, builder, bp in arrived:
                    self.walking_bp -= bp
                    self._arrive(job, builder, bp)
            build_time = self.units[job.unit].build_time
            if job.progress >= build_time * (1.0 - 1e-9):
                job.progress = build_time
                finished.append(job.id)
        for job_id in finished:
            self._finish(job_id)
        return finished

    @staticmethod
    def _snap(value: float, cap: float, level: float | None) -> float:
        if value < EPS:
            return 0.0
        if value > cap - EPS:
            return cap
        if level is not None and abs(value - level) < EPS:
            return level
        return value

    def _finish(self, job_id: int) -> None:
        job = self.jobs.pop(job_id)
        for b in job.builders:
            self.builder_job[b] = -1
            if self.units[self.builder_unit[b]].can_assist:
                self.idle_helper_bp += self._builder_bp(b)
        still_walking = sum(bp for _, _, bp in job.walking)  # the job finished before they arrived
        self.walking_bp -= still_walking
        self.idle_bp += job.bp + still_walking
        if self.recorder:
            self.recorder.jobs[job_id]["end"] = self.time
        self._add_unit(job.unit, +1)
        if job.upgrades:
            # unit_mex_upgrade_reclaimer: the old extractor is removed and refunded on completion
            self._add_unit(job.upgrades, -1)
            self.metal += self.units[job.upgrades].metal
            self._clamp_storage()
