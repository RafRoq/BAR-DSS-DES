"""Extracts the tech tree (unit buildoptions) from a Beyond All Reason source checkout.

The unit stats come from the gex.honu.pw API (unit_scraper.py), but that data has no
buildoptions. BAR's unit definitions (units/**/*.lua) do. The Lua files are parsed as
text only; nothing is executed.

Get a checkout without art assets:
    git clone --depth 1 --filter=blob:none --sparse https://github.com/beyond-all-reason/Beyond-All-Reason.git
    git -C Beyond-All-Reason sparse-checkout set units
Then:
    python buildoptions_scraper.py path/to/Beyond-All-Reason
"""

import json
import os
import re
import subprocess
import sys

OUTPUT_JSON_FILE = "unit_buildoptions.json"

BLOCK_COMMENT = re.compile(r"--\[(=*)\[.*?\]\1\]", re.DOTALL)
LINE_COMMENT = re.compile(r"--[^\n]*")
UNIT_NAME = re.compile(r"^\s*return\s*\{\s*([A-Za-z0-9_]+)\s*=\s*\{", re.MULTILINE)
BUILDOPTIONS = re.compile(r"\bbuildoptions\s*=\s*\{(.*?)\}", re.DOTALL)
QUOTED = re.compile(r"\"([A-Za-z0-9_]+)\"")


def parse_unit_file(text: str) -> tuple[str, list[str]] | None:
    text = LINE_COMMENT.sub("", BLOCK_COMMENT.sub("", text))
    name = UNIT_NAME.search(text)
    if not name:
        return None
    blocks = BUILDOPTIONS.findall(text)
    if len(blocks) > 1:
        raise ValueError(f"{name.group(1)}: more than one buildoptions block")
    options = QUOTED.findall(blocks[0]) if blocks else []
    return name.group(1), list(dict.fromkeys(options))


def extract_buildoptions(repo_path: str) -> dict:
    units_dir = os.path.join(repo_path, "units")
    if not os.path.isdir(units_dir):
        raise FileNotFoundError(f"No units/ directory in '{repo_path}'.")

    buildoptions = {}
    for dirpath, _, filenames in os.walk(units_dir):
        for filename in sorted(filenames):
            if not filename.endswith(".lua"):
                continue
            with open(os.path.join(dirpath, filename), encoding="utf-8") as f:
                parsed = parse_unit_file(f.read())
            if parsed and parsed[1]:
                buildoptions[parsed[0]] = parsed[1]

    commit = subprocess.run(
        ["git", "-C", repo_path, "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    return {
        "source": {
            "repo": "https://github.com/beyond-all-reason/Beyond-All-Reason",
            "commit": commit or "unknown",
        },
        "buildoptions": dict(sorted(buildoptions.items())),
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    result = extract_buildoptions(sys.argv[1])
    with open(OUTPUT_JSON_FILE, "w") as f:
        json.dump(result, f, indent=4)
    print(
        f"Saved buildoptions for {len(result['buildoptions'])} units "
        f"(BAR commit {result['source']['commit'][:10]}) to '{OUTPUT_JSON_FILE}'."
    )
