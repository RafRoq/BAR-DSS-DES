"""The new core replaying the hand-written ARMADA_BOT recipe, compared with the old simulator."""

import json
import os

import pytest

import main
from bar_opt.recipe import load_yaml_recipe, replay, save_yaml, to_yaml_steps

BASELINE_FILE = os.path.join(os.path.dirname(__file__), "baseline_armada_bot.json")
# Units whose stats changed between the old API data and bar_units.json
CHANGED = {"corap", "corca"}


def test_armada_bot_matches_old_simulator_where_data_is_unchanged():
    with open(BASELINE_FILE) as f:
        baseline = json.load(f)
    state, problems = replay(main.ARMADA_BOT)
    assert problems == []
    jobs = sorted(state.recorder.jobs.values(), key=lambda j: j["start"])
    assert [j["unit"] for j in jobs] == [t["name"] for t in baseline["tasks"]]
    for job, old in zip(jobs, baseline["tasks"]):
        if old["name"] in CHANGED:
            break
        # old simulator: 1 ms steps, one step lost between tasks
        assert job["start"] == pytest.approx(old["start"], abs=0.02)
        assert job["end"] == pytest.approx(old["end"], abs=0.02)
    # corap 5380 / 300 BP, then corca 5200 / (150 + 300) BP
    assert jobs[-2]["end"] - jobs[-2]["start"] == pytest.approx(5380 / 300)
    assert jobs[-1]["end"] - jobs[-1]["start"] == pytest.approx(5200 / 450)
    assert state.metrics.stall_seconds == 0


def test_yaml_export_round_trip(tmp_path):
    state, _ = replay(main.ARMADA_BOT)
    steps = to_yaml_steps(state)
    assert steps[0] == {"name": "Metal Extractor - cormex", "repeat": 2, "builders": ["corcom"]}
    assert steps[-1]["builders"] == ["corap", "corcom"]
    path = tmp_path / "armada.yaml"
    save_yaml(steps, path)
    assert load_yaml_recipe(path) == main.ARMADA_BOT
