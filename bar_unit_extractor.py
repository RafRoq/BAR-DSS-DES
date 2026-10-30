"""Extracts unit economy stats, combat stats and the tech tree from a Beyond All Reason source checkout.

Writes bar_units.json with the same per-unit layout as unit_data_as_dict.json
({"definitionName", "displayName", "description", "unit": {...}}) plus "buildOptions",
all taken from one BAR commit so stats and tech tree are consistent.

Values are the raw unitdef values; gamedata/alldefs_post.lua only changes the fields
used here when non-default modoptions are enabled (multipliers, unit restrictions).
The Lua files are parsed as text only; nothing is executed.

Combat stats (health, speed, armor class, weapons with damage per armor class) come from the
same unit files, read with a small Lua table-literal parser, and armor classes from
gamedata/armordefs.lua. Expressions other than plain arithmetic are ignored, not evaluated.

Get a checkout without art assets:
    git clone --depth 1 --filter=blob:none --sparse https://github.com/beyond-all-reason/Beyond-All-Reason.git
    git -C Beyond-All-Reason sparse-checkout set units gamedata language/en
Then:
    python bar_unit_extractor.py path/to/Beyond-All-Reason
"""

import json
import os
import re
import subprocess
import sys

OUTPUT_JSON_FILE = "bar_units.json"

BLOCK_COMMENT = re.compile(r"--\[(=*)\[.*?\]\1\]", re.DOTALL)
LINE_COMMENT = re.compile(r"--[^\n]*")
UNIT_NAME = re.compile(r"^\s*return\s*\{\s*([A-Za-z0-9_]+)\s*=\s*\{", re.MULTILINE)
BUILDOPTIONS = re.compile(r"\bbuildoptions\s*=\s*\{(.*?)\}", re.DOTALL)
CUSTOMPARAMS = re.compile(r"\bcustomparams\s*=\s*\{(.*?)\n\t\t\}", re.DOTALL)
QUOTED = re.compile(r"\"([A-Za-z0-9_]+)\"")
# Unit-level keys sit at two tabs of indentation; deeper ones belong to nested tables.
TOP_LEVEL_FIELD = re.compile(r"^\t\t([a-z_]+)\s*=\s*([^,\n]+),?\s*$", re.MULTILINE)
NESTED_FIELD = re.compile(r"^\t\t\t([a-z_]+)\s*=\s*([^,\n]+),?\s*$", re.MULTILINE)

# lua key -> output key, as named in unit_data_as_dict.json where that file has it
FIELDS = {
    "metalcost": "metalCost",
    "energycost": "energyCost",
    "buildtime": "buildTime",
    "workertime": "buildPower",
    "energymake": "energyProduced",
    "metalmake": "metalProduced",
    "energyupkeep": "energyUpkeep",
    "windgenerator": "windGenerator",
    "tidalgenerator": "tidalGenerator",
    "extractsmetal": "extractsMetal",
    "metalstorage": "metalStorage",
    "energystorage": "energyStorage",
    "speed": "speed",
    "builder": "isBuilder",
    "canassist": "canAssist",
}
CUSTOM_FIELDS = {
    "energyconv_capacity": "energyConversionCapacity",
    "energyconv_efficiency": "energyConversionEfficiency",
    "techlevel": "techLevel",
}


def parse_value(raw: str):
    raw = raw.strip()
    if raw in ("true", "false"):
        return raw == "true"
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    try:
        return float(raw)
    except ValueError:
        return None


def parse_unit_file(text: str) -> dict | None:
    text = LINE_COMMENT.sub("", BLOCK_COMMENT.sub("", text))
    name = UNIT_NAME.search(text)
    if not name:
        return None
    blocks = BUILDOPTIONS.findall(text)
    if len(blocks) > 1:
        raise ValueError(f"{name.group(1)}: more than one buildoptions block")

    top = dict(TOP_LEVEL_FIELD.findall(text))
    unit = {out: parse_value(top[key]) if key in top else 0 for key, out in FIELDS.items()}
    unit["isBuilder"] = bool(unit["isBuilder"])
    unit["canAssist"] = bool(unit["canAssist"])

    custom_block = CUSTOMPARAMS.search(text)
    custom = dict(NESTED_FIELD.findall(custom_block.group(1))) if custom_block else {}
    for key, out in CUSTOM_FIELDS.items():
        unit[out] = parse_value(custom[key]) if key in custom else 0
    unit["techLevel"] = unit["techLevel"] or 1

    return {
        "definitionName": name.group(1),
        "unit": unit,
        "buildOptions": list(dict.fromkeys(QUOTED.findall(blocks[0]))) if blocks else [],
    }


# ---------------------------------------------------------------- Lua table literals

LUA_TOKEN = re.compile(r"""
    (?P<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
  | (?P<number>0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_.:]*)
  | (?P<symbol>==|~=|<=|>=|\.\.|[{}\[\]=,;()+\-*/%^#<>])
  | (?P<space>\s+)
""", re.VERBOSE)
ARITHMETIC = re.compile(r"^[0-9eE.+\-*/() ]+$")


def lua_tokens(text: str) -> list[tuple[str, str]]:
    tokens, pos = [], 0
    while pos < len(text):
        m = LUA_TOKEN.match(text, pos)
        if not m:
            tokens.append(("other", text[pos]))
            pos += 1
            continue
        pos = m.end()
        if m.lastgroup != "space":
            tokens.append((m.lastgroup, m.group()))
    return tokens


class LuaTable:
    """Parses a Lua table constructor into dicts and lists. Values that are not literals
    (variables, function calls) become None; nothing is executed."""

    def __init__(self, tokens: list[tuple[str, str]], pos: int = 0):
        self.t, self.i = tokens, pos

    def peek(self, k: int = 0) -> tuple[str, str]:
        return self.t[self.i + k] if self.i + k < len(self.t) else ("eof", "")

    def table(self):
        if self.peek()[1] != "{":
            raise ValueError(f"expected a table, got {self.peek()}")
        self.i += 1
        out, n = {}, 0
        while self.peek()[1] != "}":
            if self.peek()[0] == "eof":
                raise ValueError("unterminated table")
            if self.peek()[1] == "[":
                self.i += 1
                key = self.value()
                self.i += 2  # "]" and "="
            elif self.peek()[0] == "name" and self.peek(1)[1] == "=":
                key = self.peek()[1]
                self.i += 2
            else:
                n += 1
                key = n
            out[key] = self.value()
            if self.peek()[1] in (",", ";"):
                self.i += 1
        self.i += 1
        keys = list(out)
        if keys and all(isinstance(k, (int, float)) for k in keys) and \
                sorted(int(k) for k in keys) == list(range(1, len(keys) + 1)):
            return [out[k] for k in sorted(keys)]
        return out

    def value(self):
        if self.peek()[1] == "{":
            return self.table()
        # an expression: everything up to the next "," ";" "}" or "]" outside brackets
        depth, parts = 0, []
        while True:
            kind, text = self.peek()
            if kind == "eof" or (depth == 0 and text in (",", ";", "}", "]")):
                break
            if text in ("(", "{", "["):
                depth += 1
            elif text in (")", "}", "]"):
                depth -= 1
            parts.append((kind, text))
            self.i += 1
        if len(parts) == 1:
            kind, text = parts[0]
            if kind == "string":
                return text[1:-1]
            if text in ("true", "false"):
                return text == "true"
            if kind == "number":
                return float(int(text, 16)) if text.lower().startswith("0x") else float(text)
            return None
        expr = " ".join(text for _, text in parts)
        if ARITHMETIC.match(expr):
            try:
                return float(eval(expr, {"__builtins__": {}}, {}))  # digits and + - * / ( ) only
            except (SyntaxError, ZeroDivisionError, TypeError):
                return None
        return None


def strip_comments(text: str) -> str:
    return LINE_COMMENT.sub("", BLOCK_COMMENT.sub("", text))


def parse_lua_return_table(text: str):
    """The table a Lua file returns (after `return`), parsed as a literal."""
    tokens = lua_tokens(strip_comments(text))
    for i, (kind, value) in enumerate(tokens):
        if kind == "name" and value == "return" and i + 1 < len(tokens) and tokens[i + 1][1] == "{":
            return LuaTable(tokens, i + 1).table()
    return None


def load_armor_classes(repo_path: str) -> dict[str, str]:
    """unit name -> armor class (gamedata/armordefs.lua); units not listed use "default"."""
    path = os.path.join(repo_path, "gamedata", "armordefs.lua")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        text = strip_comments(f.read())
    start = text.index("local armorDefs")
    defs = LuaTable(lua_tokens(text[text.index("{", start):])).table()
    return {name: cls for cls, names in defs.items() for name in (names or []) if isinstance(name, str)}


WEAPON_FIELDS = ("weapontype", "range", "reloadtime", "burst", "burstrate", "projectiles", "areaofeffect",
                 "edgeeffectiveness", "paralyzer", "commandfire", "stockpile", "energypershot",
                 "metalpershot", "waterweapon", "weaponvelocity", "accuracy", "sprayangle", "impactonly")


# gamedata/alldefs_post.lua: categories are generated from the movement class and a few fields
HOVER_CLASSES = {"HOVER2", "HOVER3", "HHOVER4", "AHOVER2"}
SHIP_CLASSES = {"BOAT3", "BOAT4", "BOAT5", "BOAT9", "EPICSHIP"}
SUB_CLASSES = {"UBOAT4", "EPICSUBMARINE"}
AMPHIB_CLASSES = {"VBOT6", "COMMANDERBOT", "SCAVCOMMANDERBOT", "ATANK3", "ABOT3", "HABOT5", "ABOTBOMB2",
                  "EPICBOT", "EPICALLTERRAIN"}
COMMANDER_CLASSES = {"COMMANDERBOT", "SCAVCOMMANDERBOT"}


def unit_categories(unit: dict) -> list[str]:
    """The target categories BAR gives a unit def (alldefs_post.lua)."""
    own = unit.get("category") if isinstance(unit.get("category"), str) else ""
    if "OBJECT" in own:
        return own.split()
    move = unit.get("movementclass") or ""
    speed = unit.get("speed") or 0
    mobile = speed > 0
    vtol = unit.get("canfly") is True
    max_depth, min_depth, waterline = unit.get("maxwaterdepth"), unit.get("minwaterdepth"), unit.get("waterline")
    hover = move in HOVER_CLASSES and (max_depth is None or max_depth < 1)
    ship = move in SHIP_CLASSES or (move in HOVER_CLASSES and max_depth is not None and max_depth >= 1)
    underwater = (min_depth is not None and waterline is None) or \
        (min_depth is not None and waterline is not None and waterline > min_depth and mobile)
    surface = not (underwater and mobile) and not vtol
    custom = unit.get("customparams") if isinstance(unit.get("customparams"), dict) else {}
    weapondefs = unit.get("weapondefs") if isinstance(unit.get("weapondefs"), dict) else {}
    rules = {
        "ALL": True, "MOBILE": mobile, "NOTMOBILE": not mobile,
        "WEAPON": bool(weapondefs), "NOWEAPON": not weapondefs,
        "VTOL": vtol, "NOTAIR": not vtol, "HOVER": hover, "NOTHOVER": not hover,
        "SHIP": ship, "NOTSHIP": not ship, "NOTSUB": move not in SUB_CLASSES,
        "CANBEUW": move in AMPHIB_CLASSES or unit.get("cansubmerge") is True,
        "UNDERWATER": underwater, "SURFACE": surface, "MINE": "minerange" in weapondefs,
        "COMMANDER": move in COMMANDER_CLASSES,
        "EMPABLE": surface and custom.get("paralyzemultiplier") != 0,
    }
    exempt = unit.get("exemptcategory") if isinstance(unit.get("exemptcategory"), str) else ""
    return own.split() + [name for name, ok in rules.items() if ok and name not in exempt]


def parse_combat(unit: dict, armor_class: str) -> dict:
    """Health, speed, categories and weapons of a parsed unit def."""
    weapondefs = {str(k).lower(): v for k, v in (unit.get("weapondefs") or {}).items() if isinstance(v, dict)}
    slots = unit.get("weapons") or []
    if isinstance(slots, dict):
        slots = [slots[k] for k in sorted(slots, key=str)]
    weapons = []
    for slot in slots:
        if not isinstance(slot, dict) or not isinstance(slot.get("def"), str):
            continue
        wd = weapondefs.get(slot["def"].lower())
        if wd is None:
            continue
        weapon = {k: wd[k] for k in WEAPON_FIELDS if wd.get(k) is not None}
        weapon["name"] = slot["def"].lower()
        damage = wd.get("damage") if isinstance(wd.get("damage"), dict) else {}
        weapon["damage"] = {k: v for k, v in damage.items() if isinstance(v, float)}
        for key in ("onlytargetcategory", "badtargetcategory"):
            if isinstance(slot.get(key), str):
                weapon[key] = slot[key]
        if wd.get("weapontype") == "BeamLaser" and wd.get("impactonly") == 1:
            weapon["areaofeffect"], weapon["edgeeffectiveness"] = 11.0, 1.0  # as alldefs_post.lua sets it
        weapons.append(weapon)
    return {
        "health": unit.get("health") or 0.0,
        "speed": unit.get("speed") or 0.0,
        "sightDistance": unit.get("sightdistance") or 0.0,
        "categories": unit_categories(unit),
        "canFly": bool(unit.get("canfly")),
        "footprint": unit.get("footprintx") or 2.0,
        "armorClass": armor_class,
        "weapons": weapons,
    }


def load_names(repo_path: str) -> tuple[dict, dict]:
    path = os.path.join(repo_path, "language", "en", "units.json")
    if not os.path.exists(path):
        return {}, {}
    with open(path, encoding="utf-8") as f:
        units = json.load(f)["units"]
    return units.get("names", {}), units.get("descriptions", {})


def extract_units(repo_path: str) -> dict:
    units_dir = os.path.join(repo_path, "units")
    if not os.path.isdir(units_dir):
        raise FileNotFoundError(f"No units/ directory in '{repo_path}'.")
    names, descriptions = load_names(repo_path)
    armor = load_armor_classes(repo_path)

    units = {}
    for dirpath, _, filenames in os.walk(units_dir):
        for filename in sorted(filenames):
            if not filename.endswith(".lua"):
                continue
            with open(os.path.join(dirpath, filename), encoding="utf-8") as f:
                text = f.read()
            parsed = parse_unit_file(text)
            if not parsed:
                continue
            key = parsed["definitionName"]
            table = parse_lua_return_table(text)
            unit_table = table.get(key) if isinstance(table, dict) else None
            if isinstance(unit_table, dict):
                custom = unit_table.get("customparams") if isinstance(unit_table.get("customparams"), dict) else {}
                armor_class = str(custom.get("armordef") or "").lower() or armor.get(key, "default")
                parsed["combat"] = parse_combat(unit_table, armor_class)
            parsed["displayName"] = names.get(key, key)
            parsed["description"] = descriptions.get(key, "")
            units[key] = parsed

    commit = subprocess.run(
        ["git", "-C", repo_path, "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    return {
        "source": {
            "repo": "https://github.com/beyond-all-reason/Beyond-All-Reason",
            "commit": commit or "unknown",
        },
        "units": dict(sorted(units.items())),
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    result = extract_units(sys.argv[1])
    with open(OUTPUT_JSON_FILE, "w") as f:
        json.dump(result, f, indent=4)
    print(
        f"Saved {len(result['units'])} units "
        f"(BAR commit {result['source']['commit'][:10]}) to '{OUTPUT_JSON_FILE}'."
    )
