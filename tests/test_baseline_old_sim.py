"""Regression baseline for the original fixed-step simulator in main.py.

Records what the old GameSimulation produces for the ARMADA_BOT recipe, so that
differences introduced by the new event-driven core are visible and deliberate.
Regenerate with:  BASELINE_REGEN=1 python -m pytest tests/test_baseline_old_sim.py
"""

import json
import os

import pytest

import main

BASELINE_FILE = os.path.join(os.path.dirname(__file__), "baseline_armada_bot.json")


def run_old_simulator() -> dict:
    game = main.GameSimulation(tasks=main.create_task_list_from_recipe(main.ARMADA_BOT))
    game.run(max_time=1200)
    return {
        "end_time": round(game.time, 3),
        "tasks": [
            {
                "name": t.name,
                "builders": t.builders,
                "start": round(t.start_time, 3),
                "end": round(t.completion_time, 3),
                "stalled": any(s == "STALLED" for _, s in t.status_history),
            }
            for t in game.tasks_completed
        ],
        "tasks_not_done": [t.name for t in game.tasks],
        "final_metal": round(game.metal, 3),
        "final_energy": round(game.energy, 3),
        "max_metal": game.max_metal,
        "max_energy": game.max_energy,
        "metal_spent": round(game.total_metal_spent, 3),
        "energy_spent": round(game.total_energy_spent, 3),
        "metal_lost": round(game.total_metal_lost, 3),
        "energy_lost": round(game.total_energy_lost, 3),
    }


def test_old_simulator_matches_baseline():
    result = run_old_simulator()
    if os.environ.get("BASELINE_REGEN") or not os.path.exists(BASELINE_FILE):
        with open(BASELINE_FILE, "w") as f:
            json.dump(result, f, indent=2)
        pytest.skip(f"baseline written to {BASELINE_FILE}")
    with open(BASELINE_FILE) as f:
        baseline = json.load(f)
    assert result == baseline
