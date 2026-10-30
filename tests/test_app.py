"""The web UI's API layer (app.py) and JSON views (bar_opt.serialize)."""

import json

import pytest

import app
from bar_opt.goal import Goal
from bar_opt.search import SearchConfig, optimize, replay_ops
from bar_opt.serialize import catalog_json, run_json

GOAL = {"name": "armada", "targets": {"cormex": 3, "corwin": 7, "corap": 1, "corca": 1}, "max_time": 600}
RECIPE = [
    {"name": "Metal Extractor - cormex", "repeat": 3, "builders": ["corcom"]},
    {"name": "corwin", "repeat": 7, "builders": ["corcom"]},
    {"name": "corap", "repeat": 1, "builders": ["corcom"]},
    {"name": "corca", "repeat": 1, "builders": ["corap", "corcom"]},
]


def test_evaluate_recipe_returns_strict_json():
    data = app.evaluate_recipe({"goal": GOAL, "recipe": RECIPE, "label": "hand"})
    text = json.dumps(data, allow_nan=False)  # no inf/nan may reach the browser
    assert data["evaluation"]["reached"] and data["problems"] == []
    assert data["target_times"]["corca"] == pytest.approx(data["evaluation"]["finish_time"])
    # started and assisted at the same moment: the start is listed first
    assert [(s["action"], s["unit"]) for s in data["steps"][-2:]] == [("start", "corca"), ("assist", "corca")]
    assert len(data["segments"]["t0"]) == len(data["segments"]["metal1"]) > 0
    assert "Construction Aircraft" in text


def test_unreached_goal_serializes_finish_as_null():
    data = app.evaluate_recipe({"goal": {**GOAL, "targets": {"coralab": 1}}, "recipe": RECIPE})
    assert data["evaluation"]["finish_time"] is None and data["evaluation"]["cost"] is None
    assert data["problems"]
    json.dumps(data, allow_nan=False)


def test_optimizer_run_json_jobs_and_builders_line_up():
    goal = Goal.from_dict(GOAL)
    result = optimize(goal, SearchConfig(beam_width=10, affordability="refined", improve_seconds=0))
    data = run_json(replay_ops(goal, result.ops), goal, "opt", "optimizer")
    builder_ids = {b["id"] for b in data["builders"]}
    assert all(b in builder_ids for job in data["jobs"] for _, b in job["joins"])
    assert data["goal"]["config"]["converter_level"] == 0.75


def test_catalog_lists_who_builds_what():
    units = app.load_units()
    catalog = {u["name"]: u for u in catalog_json(units, "corcom", 14.2, 21, 2.3)}
    assert catalog["coralab"]["built_by"] == ["corck"] or "corck" in catalog["coralab"]["built_by"]
    assert "corcom" in catalog["cormex"]["built_by"]
    assert catalog["cornanotc"]["can_assist"] and not catalog["corlab"]["can_assist"]
    assert any("metal/s" in e for e in catalog["cormex"]["economy"])


@pytest.mark.parametrize("folder,name", [("goals", "../app.py"), ("tests", "x.yaml"), ("goals", "a/b.yaml"),
                                         ("recipes", "x.py"), ("goals", ".hidden.yaml")])
def test_file_paths_are_confined(folder, name):
    with pytest.raises(app.BadRequest):
        app.file_path(folder, name)


def test_unit_reports_are_confined_and_carry_unit_stats(tmp_path, monkeypatch):
    for name in ("../app.json", "cor_1500.md", "cor_1500.json/..", "x.json"):
        with pytest.raises(app.BadRequest):
            app.unit_report(name)
    report = {"faction": "cor", "budget": 1500, "enemies": ["armpw"], "enemy_counter_mix": {"armpw": 1.0},
              "game_value": 0.1, "defences": {"corllt": [0.5]},
              "compositions": [{"mix": {"corak": 1.0}, "label": "Grunt", "scores": [0.1], "blob": [0.1],
                                "spread": [0.1], "vs_best_counter": 0.1, "robust_weight": 1.0}]}
    (tmp_path / "cor_1500.json").write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(app, "UNIT_REPORTS", str(tmp_path))
    assert app.unit_reports() == ["cor_1500.json"]
    loaded = app.unit_report("cor_1500.json")
    assert set(loaded["units"]) == {"armpw", "corllt", "corak"}
    assert loaded["units"]["corak"]["dps"] == pytest.approx(74) and not loaded["units"]["corllt"]["mobile"]


def test_bad_requests_are_reported():
    with pytest.raises(app.BadRequest):
        app.goal_from({"name": "x", "targets": {}})
    with pytest.raises(app.BadRequest):
        app.goal_from({"name": "x", "targets": {"nosuchunit": 1}})
    with pytest.raises(app.BadRequest):
        app.recipe_from([])
    assert app.search_from({"affordability": "both", "beam_width": 20}).affordability is None
