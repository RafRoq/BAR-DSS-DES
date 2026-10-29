"""Extracts unit economy stats and the tech tree from a Beyond All Reason source checkout.

Writes bar_units.json with the same per-unit layout as unit_data_as_dict.json
({"definitionName", "displayName", "description", "unit": {...}}) plus "buildOptions",
all taken from one BAR commit so stats and tech tree are consistent.

Values are the raw unitdef values; gamedata/alldefs_post.lua only changes the fields
used here when non-default modoptions are enabled (multipliers, unit restrictions).
The Lua files are parsed as text only; nothing is executed.

Get a checkout without art assets:
    git clone --depth 1 --filter=blob:none --sparse https://github.com/beyond-all-reason/Beyond-All-Reason.git
    git -C Beyond-All-Reason sparse-checkout set units language/en
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

    units = {}
    for dirpath, _, filenames in os.walk(units_dir):
        for filename in sorted(filenames):
            if not filename.endswith(".lua"):
                continue
            with open(os.path.join(dirpath, filename), encoding="utf-8") as f:
                parsed = parse_unit_file(f.read())
            if not parsed:
                continue
            key = parsed["definitionName"]
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
