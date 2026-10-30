"""Combat analysis of units from bar_units.json: cost efficiency, simulated fights and robust mixes.

Three levels, each a better approximation and slower than the last:

1. Lanchester score. In a straight fight an army's strength grows with DPS x health x count^2,
   so for a fixed budget a unit type is worth about DPS x HP / cost^2. Quick, but blind to
   range, speed, splash and overkill.
2. Fight simulation. Two armies of equal cost start apart and close in. Every unit targets
   the nearest enemy it can damage, stops when that enemy is in range and fires whole shots
   (so overkill is wasted). Shots fly at their weapon's speed: cannon shells aim where the
   target will be if it keeps moving (a target that stops or turns is missed, and moving
   targets are only roughly predictable), missiles home in, beams hit at once. Each shot
   lands with the weapon's scatter and damages every enemy
   whose body is within the blast, less towards the edge. The score is the value left alive
   on each side.
3. Robust mixes. The fight results form a matrix of my options against the enemy's; the mixed
   strategy of a zero-sum game over that matrix (found by fictitious play) is the mix that
   does best against the enemy's best answer.

Not modelled: micro and kiting, terrain and cover, target priorities beyond "nearest",
formations spreading out, friendly fire, shields, cloak and veterancy. Treat the
results as a first filter to check in game.
"""

import itertools
import json
import math
import random
from dataclasses import dataclass, field

import numpy as np

from .data import DATA_FILE

ENERGY_PER_METAL = 70.0  # energy counted as metal at the T1 converter rate
INSTANT_TYPES = {"BeamLaser", "LightningCannon", "Rifle", "Melee"}  # hit when fired
GUIDED_TYPES = {"MissileLauncher", "StarburstLauncher", "TorpedoLauncher"}  # follow their target
FACTIONS = {"arm": "Armada", "cor": "Cortex", "leg": "Legion"}
# The factories and constructors whose armed options count as "early" (T1)
EARLY_FACTORIES = {"arm": ("armlab", "armvp"), "cor": ("corlab", "corvp"), "leg": ("leglab", "legvp")}
EARLY_BUILDERS = {"arm": "armck", "cor": "corck", "leg": "legck"}
ADVANCED_FACTORIES = {"arm": ("armalab", "armavp"), "cor": ("coralab", "coravp"), "leg": ("legalab", "legavp")}
MIN_RANGE = 50.0  # shorter "weapons" belong to crawling bombs, which explode on contact


@dataclass(frozen=True)
class Weapon:
    name: str
    range: float
    reload: float
    damage: dict  # armor class -> damage per projectile
    shots: float  # projectiles x burst per volley
    radius: float  # splash radius (areaofeffect / 2)
    edge: float  # damage share at the edge of the splash
    only: frozenset | None  # target categories it may fire at; None = any
    velocity: float = 0.0  # elmos per second; 0 = hits when fired
    scatter: float = 0.0  # radians of spread (accuracy + sprayangle)
    guided: bool = False  # missiles follow their target
    duty: float = 1.0  # share of the time it fires (sweep-fire heat rays cool down between sweeps)


@dataclass(frozen=True)
class CombatUnit:
    name: str
    display: str
    faction: str
    metal: float
    energy: float
    health: float
    speed: float
    armor: str
    categories: frozenset
    weapons: tuple = field(default=())
    radius: float = 11.0  # body radius for hits, from the footprint

    @property
    def cost(self) -> float:
        """Metal plus energy at the converter rate."""
        return self.metal + self.energy / ENERGY_PER_METAL

    @property
    def mobile(self) -> bool:
        return self.speed > 0


def load_combat(path: str = DATA_FILE) -> dict[str, CombatUnit]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)["units"]
    units = {}
    for name, entry in raw.items():
        c = entry.get("combat")
        if not c:
            continue
        weapons = []
        for w in c["weapons"]:
            if w.get("commandfire") or w.get("paralyzer") or w.get("stockpile") or not w.get("reloadtime"):
                continue  # manual (D-gun), stun-only and stockpiled weapons are not part of steady DPS
            if w.get("weapontype") == "TorpedoLauncher":
                continue  # torpedoes and depth charges only hit units in water; these fights are on land
            only = w.get("onlytargetcategory")
            fire, cool = w.get("sweepfire") or (1.0, 0.0)
            weapons.append(Weapon(
                name=w["name"], range=w.get("range", 0.0), reload=w["reloadtime"], damage=dict(w["damage"]),
                shots=(w.get("projectiles") or 1.0) * (w.get("burst") or 1.0),
                radius=(w.get("areaofeffect") or 0.0) / 2, edge=w.get("edgeeffectiveness") or 0.0,
                only=frozenset(only.split()) if only else None,
                velocity=0.0 if w.get("weapontype") in INSTANT_TYPES else (w.get("weaponvelocity") or 0.0),
                scatter=((w.get("accuracy") or 0.0) + (w.get("sprayangle") or 0.0)) * math.pi / 45055,
                guided=w.get("weapontype") in GUIDED_TYPES, duty=fire / (fire + cool)))
        u = entry["unit"]
        units[name] = CombatUnit(
            name=name, display=entry.get("displayName") or name, faction=name[:3],
            metal=u.get("metalCost") or 0.0, energy=u.get("energyCost") or 0.0,
            health=c["health"], speed=c["speed"], armor=c["armorClass"],
            categories=frozenset(c["categories"]), weapons=tuple(weapons),
            radius=(c.get("footprint") or 2.0) * 16 * 0.35)
    return units


def can_target(weapon: Weapon, target: CombatUnit) -> bool:
    return weapon.only is None or bool(weapon.only & target.categories)


def volley(weapon: Weapon, target: CombatUnit) -> float:
    per_shot = weapon.damage.get(target.armor, weapon.damage.get("default", 0.0))
    return per_shot * weapon.shots * weapon.duty  # sweep fire spread evenly over its cycle


def dps(unit: CombatUnit, target: CombatUnit) -> float:
    return sum(volley(w, target) / w.reload for w in unit.weapons if can_target(w, target))


def reach(unit: CombatUnit, target: CombatUnit) -> float:
    """Longest range at which `unit` can damage `target` (0 if it cannot)."""
    return max((w.range for w in unit.weapons if can_target(w, target) and volley(w, target) > 0), default=0.0)


def lanchester(unit: CombatUnit, target: CombatUnit) -> float:
    """DPS x HP / cost^2 against `target` (higher is better)."""
    return dps(unit, target) * unit.health / unit.cost ** 2


def early_units(units: dict[str, CombatUnit], faction: str, mobile: bool = True) -> list[str]:
    """Armed T1 units of a faction: from its bot lab and vehicle plant (mobile), or the
    defences its T1 constructor bot builds (static)."""
    with open(DATA_FILE, encoding="utf-8") as f:
        raw = json.load(f)["units"]
    if mobile:
        options = [o for factory in EARLY_FACTORIES[faction] for o in raw[factory]["buildOptions"]]
    else:
        options = list(raw[EARLY_BUILDERS[faction]]["buildOptions"])
    out = []
    for name in dict.fromkeys(options):
        u = units.get(name)
        if u and u.weapons and u.mobile == mobile and "VTOL" not in u.categories:
            out.append(name)
    return out


def ground_army(units: dict[str, CombatUnit], faction: str, tech: int = 1, reference: str = "corak") -> list[str]:
    """Mobile units from a faction's T1 (tech 1) or T2 (tech 2) bot lab and vehicle plant that
    can fight ground units. Builders, anti-air, spies, crawling bombs and decoy commanders are
    left out."""
    if tech == 1:
        candidates = early_units(units, faction)
    else:
        with open(DATA_FILE, encoding="utf-8") as f:
            raw = json.load(f)["units"]
        options = [o for factory in ADVANCED_FACTORIES[faction] for o in raw[factory]["buildOptions"]]
        candidates = [n for n in dict.fromkeys(options)
                      if n in units and units[n].mobile and "VTOL" not in units[n].categories]
    ref = units[reference]
    return [n for n in candidates
            if dps(units[n], ref) > 0 and reach(units[n], ref) >= MIN_RANGE and not n.endswith("decom")]


# ---------------------------------------------------------------- fight simulation

BLOB, SPREAD = 32.0, 96.0  # elmos between units: a tight block, and a spread-out army
START_GAP = 900.0  # distance between the two formations at the start
STEP = 0.1  # seconds per simulation step


@dataclass
class FightResult:
    left_a: float  # cost-weighted value left alive, as a share of the budget
    left_b: float
    seconds: float

    @property
    def score(self) -> float:
        """From -1 (B wins cleanly) to +1 (A wins cleanly)."""
        return self.left_a - self.left_b


def army(units: dict[str, CombatUnit], mix: dict[str, float], budget: float) -> list[CombatUnit]:
    """Units bought with `budget`, split by cost share between the types in `mix`: one unit at a
    time, of the type furthest below its share among those that fit (overshooting the budget by
    at most half their cost). A type with a small share of a small budget may get no unit; the
    army always has at least one."""
    total = sum(mix.values())
    target = {name: budget * share / total for name, share in mix.items()}
    spent = dict.fromkeys(mix, 0.0)
    out = []
    while True:
        fits = [n for n in mix if sum(spent.values()) + units[n].cost / 2 <= budget] or ([] if out else list(mix))
        if not fits:
            return out
        name = max(fits, key=lambda n: target[n] - spent[n])
        out.append(units[name])
        spent[name] += units[name].cost


def even_armies(units: dict[str, CombatUnit], mix_a: dict[str, float], mix_b: dict[str, float],
                budget: float) -> tuple[list[CombatUnit], list[CombatUnit]]:
    """Armies `a` and `b` of nearly the same cost. Expensive units come in big steps (one
    3000-metal Mammoth more or less), so the side with the priciest unit is bought first and
    the other side spends what it actually cost."""
    lumpy_a = max(units[n].cost for n in mix_a) >= max(units[n].cost for n in mix_b)
    first, second = (mix_a, mix_b) if lumpy_a else (mix_b, mix_a)
    bought = army(units, first, budget)
    matched = army(units, second, sum(u.cost for u in bought))
    return (bought, matched) if lumpy_a else (matched, bought)


def _formation(n: int, x0: float, facing: float, spacing: float) -> np.ndarray:
    cols = max(1, math.ceil(math.sqrt(n)))
    pos = np.zeros((n, 2))
    for i in range(n):
        row, col = divmod(i, cols)
        pos[i] = (x0 - facing * row * spacing, (col - (cols - 1) / 2) * spacing)
    return pos


def fight(a: list[CombatUnit], b: list[CombatUnit], spacing: float = BLOB, max_seconds: float = 150.0,
          seed: int = 0) -> FightResult:
    """Simulate army `a` against army `b`, each in a block with `spacing` elmos between units;
    see the module docstring for what is modelled. Splash weapons depend a lot on spacing."""
    rng = random.Random(seed)
    sides = [a, b]
    pos = [_formation(len(a), 0.0, 1.0, spacing), _formation(len(b), START_GAP, -1.0, spacing)]
    vel = [np.zeros((len(a), 2)), np.zeros((len(b), 2))]
    hp = [np.array([u.health for u in s], dtype=float) for s in sides]
    body = [np.array([u.radius for u in s]) for s in sides]
    reload_left = [[np.zeros(len(u.weapons)) for u in s] for s in sides]
    value = [sum(u.cost for u in s) for s in sides]
    flying: list[tuple] = []  # (impact time, target side, landing point or None for a missile, target, weapon)
    cache: dict[tuple[str, str], tuple] = {}

    def pair(u: CombatUnit, v: CombatUnit):
        key = (u.name, v.name)
        if key not in cache:
            vol = [volley(w, v) if can_target(w, v) else 0.0 for w in u.weapons]
            cache[key] = (vol, max([w.range for w, d in zip(u.weapons, vol) if d > 0], default=0.0))
        return cache[key]

    def impact(side: int, point: np.ndarray, w: Weapon, damage: np.ndarray) -> None:
        """Damage every living unit of `side` whose body is within the blast at `point`."""
        edge_dist = np.maximum(0.0, np.sqrt(((pos[side] - point) ** 2).sum(axis=1)) - body[side])
        blast = max(w.radius, 1.0)
        for j in np.flatnonzero((hp[side] > 0) & (edge_dist < blast)):
            target = sides[side][j]
            if can_target(w, target):
                damage[j] += volley(w, target) * (1 - (1 - w.edge) * edge_dist[j] / blast)

    t = 0.0
    while t < max_seconds:
        alive = [hp[0] > 0, hp[1] > 0]
        if not alive[0].any() or not alive[1].any():
            break
        damage = [np.zeros(len(a)), np.zeros(len(b))]
        landed = [shot for shot in flying if shot[0] <= t]
        flying = [shot for shot in flying if shot[0] > t]
        for _, side, point, j, w in landed:
            if point is None:  # a missile lands on its target if that is still alive
                if hp[side][j] <= 0:
                    continue
                point = pos[side][j].copy()
            impact(side, point, w, damage[side])
        moves = []
        for s, other in ((0, 1), (1, 0)):
            if not alive[s].any():
                continue
            enemy_idx = np.flatnonzero(alive[other])
            delta = pos[other][enemy_idx][None, :, :] - pos[s][:, None, :]
            dist = np.sqrt((delta ** 2).sum(axis=2))  # mine x living enemies
            for i in np.flatnonzero(alive[s]):
                u = sides[s][i]
                best, best_d, best_reach = -1, math.inf, 0.0
                for k, j in enumerate(enemy_idx):
                    reach_ = pair(u, sides[other][j])[1]
                    if reach_ > 0 and dist[i, k] < best_d:
                        best, best_d, best_reach = k, dist[i, k], reach_
                if best < 0:
                    continue  # nothing it can hurt
                target = enemy_idx[best]
                if best_d > best_reach and u.speed > 0:
                    step = min(u.speed * STEP, best_d - best_reach + 1e-6)
                    moves.append((s, i, delta[i, best] / best_d * step))
                vol = pair(u, sides[other][target])[0]
                timers = reload_left[s][i]
                for w_i, w in enumerate(u.weapons):
                    timers[w_i] -= STEP
                    if timers[w_i] > 0 or vol[w_i] <= 0 or best_d > w.range:
                        continue
                    timers[w_i] = w.reload
                    flight = best_d / w.velocity if w.velocity > 0 else 0.0
                    if w.guided:
                        flying.append((t + flight, other, None, target, w))
                        continue
                    aim = pos[other][target] + vel[other][target] * flight  # lead a moving target
                    # Units do not walk in perfectly straight lines (pathing, jostling, speed
                    # changes): a moving target can be off the lead by up to half the distance
                    # it covers while the shot flies.
                    dodge = 0.5 * float(np.hypot(*vel[other][target])) * flight
                    spread = (best_d * w.scatter + dodge) * math.sqrt(rng.random())
                    angle = rng.random() * 2 * math.pi
                    point = aim + spread * np.array([math.cos(angle), math.sin(angle)])
                    if flight < STEP:
                        impact(other, point, w, damage[other])
                    else:
                        flying.append((t + flight, other, point, target, w))
        vel[0][:] = 0.0
        vel[1][:] = 0.0
        for s, i, d in moves:
            pos[s][i] += d
            vel[s][i] = d / STEP
        hp[0] -= damage[0]
        hp[1] -= damage[1]
        t += STEP

    def left(s):
        return sum(u.cost * max(0.0, h) / u.health for u, h in zip(sides[s], hp[s])) / value[s]

    return FightResult(left(0), left(1), t)


# ---------------------------------------------------------------- robust mixes

def solve_zero_sum(matrix: list[list[float]], iterations: int = 20000) -> tuple[list[float], list[float], float]:
    """Mixed strategies (rows, columns) and value of the zero-sum game where the row player
    gets matrix[i][j], by fictitious play: each side keeps answering the other's history."""
    rows, cols = len(matrix), len(matrix[0])
    row_counts, col_counts = [0] * rows, [0] * cols
    row_payoff, col_payoff = [0.0] * rows, [0.0] * cols  # cumulative payoff of each pure option
    r, c = 0, 0
    for _ in range(iterations):
        row_counts[r] += 1
        col_counts[c] += 1
        for i in range(rows):
            row_payoff[i] += matrix[i][c]
        for j in range(cols):
            col_payoff[j] += matrix[r][j]
        r = max(range(rows), key=row_payoff.__getitem__)
        c = min(range(cols), key=col_payoff.__getitem__)
    row_mix = [n / iterations for n in row_counts]
    col_mix = [n / iterations for n in col_counts]
    value = sum(row_mix[i] * matrix[i][j] * col_mix[j] for i in range(rows) for j in range(cols))
    return row_mix, col_mix, value


def compositions(names: list[str], max_types: int = 2) -> list[dict[str, float]]:
    """Every single type and every even split of `max_types` types."""
    out = [{n: 1.0} for n in names]
    for k in range(2, max_types + 1):
        out += [{n: 1.0 for n in combo} for combo in itertools.combinations(names, k)]
    return out


def mix_label(units: dict[str, CombatUnit], mix: dict[str, float]) -> str:
    return " + ".join(units[n].display for n in mix)
