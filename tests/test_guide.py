"""The in-game guide: plan export (bar_opt.guide) and the widget (widget/gui_build_guide.lua).

The widget test runs the Lua file under Lua 5.1, BAR's Lua version, against a small mock of
the BAR widget API. It needs `pip install lupa` and is skipped without it.
"""

import json
import os

import pytest

import app
from bar_opt.goal import Goal
from bar_opt.guide import plan_json
from bar_opt.search import SearchConfig, optimize, replay_ops
from bar_opt.serialize import run_json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIDGET = os.path.join(ROOT, "widget", "gui_build_guide.lua")
GOAL = Goal(name="lab and con", targets={"corlab": 1, "corck": 2}, max_time=600)


@pytest.fixture(scope="module")
def run():
    result = optimize(GOAL, SearchConfig(beam_width=10, affordability="refined", improve_seconds=0))
    return run_json(replay_ops(GOAL, result.ops), GOAL, "optimizer", "optimizer")


def test_plan_has_one_step_per_job_in_start_order(run):
    plan = plan_json(run)
    assert plan["format"] == "bar-build-guide" and plan["name"] == "lab and con"
    assert len(plan["steps"]) == len(run["jobs"])
    starts = [s["start"] for s in plan["steps"]]
    assert starts == sorted(starts)
    assert [s["n"] for s in plan["steps"]] == list(range(1, len(starts) + 1))
    cons = [s for s in plan["steps"] if s["unit"] == "corck"]
    assert [s["occurrence"] for s in cons] == [1, 2]
    assert all(s["builder"] == "Bot Lab 1" for s in cons)
    assert plan["steps"][0]["builder"] == "Commander"
    json.dumps(plan, allow_nan=False)


def test_plan_resources_are_sampled_from_the_start(run):
    plan = plan_json(run)
    first = plan["resources"][0]
    assert first[:3] == [0.0, 1000.0, 1000.0]
    times = [r[0] for r in plan["resources"]]
    assert times[1] - times[0] == 5.0 and times[-1] <= run["end_time"]


def test_install_and_logs_in_a_bar_data_folder(run, tmp_path, monkeypatch):
    from bar_opt import guide

    (tmp_path / "LuaUI").mkdir()
    monkeypatch.setenv("BAR_DATA_DIR", str(tmp_path))
    assert guide.find_bar_data() == str(tmp_path)
    written = guide.install(plan_json(run), str(tmp_path))
    assert (tmp_path / "LuaUI" / "Widgets" / "gui_build_guide.lua").exists()
    assert json.loads((tmp_path / "LuaUI" / "Config" / "build_guide_plan.json").read_text())["steps"]
    assert len(written) == 2
    (tmp_path / "LuaUI" / "Config" / "build_guide_log_20261029_120000.json").write_text('{"steps": []}')
    (tmp_path / "LuaUI" / "Config" / "other.json").write_text("{}")
    assert [log["name"] for log in guide.list_logs(str(tmp_path))] == ["build_guide_log_20261029_120000.json"]
    assert guide.read_log(str(tmp_path), "build_guide_log_20261029_120000.json") == {"steps": []}
    for bad in ("../app.py", "other.json", "build_guide_log/../../x.json"):
        with pytest.raises(ValueError):
            guide.read_log(str(tmp_path), bad)


def test_app_serves_the_widget_file():
    name, _ = app.STATIC["/widget/gui_build_guide.lua"]
    assert os.path.exists(os.path.join(app.WEB, name))


# ---------------------------------------------------------------- the widget under Lua 5.1

def test_widget_follows_a_game_and_logs_it(run, tmp_path, monkeypatch):
    lua51 = pytest.importorskip("lupa.lua51")
    plan = plan_json(run)
    config = tmp_path / "LuaUI" / "Config"
    config.mkdir(parents=True)
    (config / "build_guide_plan.json").write_text(json.dumps(plan))
    monkeypatch.chdir(tmp_path)

    lua = lua51.LuaRuntime(unpack_returned_tuples=True)

    def to_lua(o):
        if isinstance(o, dict):
            return lua.table_from({k: to_lua(v) for k, v in o.items()})
        if isinstance(o, list):
            return lua.table_from([to_lua(v) for v in o])
        return o

    def from_lua(t):
        if lua51.lua_type(t) != "table":
            return t
        keys = list(t.keys())
        if keys and all(isinstance(k, int) for k in keys):
            return [from_lua(t[i]) for i in range(1, len(keys) + 1)]
        return {k: from_lua(v) for k, v in t.items()} if keys else []

    frame = {"n": 0}
    printed = []
    names = sorted({s["unit"] for s in plan["steps"]} | {"corcom", "armcom", "armmex", "cor_hat_halloween_mask"})
    def_id = {u: i + 1 for i, u in enumerate(names)}
    g = lua.globals()
    lua.execute("""
        function _mock(print_fn, add_action)
          local font = {}
          function font:Begin() end
          function font:End() end
          function font:SetTextColor() end
          function font:Print(s) print_fn(s) end
          function font:GetTextWidth(s) return #s * 0.5 end
          local handler = {}
          function handler:AddAction(name, fn) add_action(name, fn) end
          function handler:RemoveAction() end
          return font, handler
        end
    """)
    actions = {}
    font, handler = g._mock(printed.append, actions.__setitem__)
    g.widgetHandler = handler
    g.Json = to_lua({})
    g.Json.decode = lambda s: to_lua(json.loads(s))
    g.Json.encode = lambda t: json.dumps(from_lua(t))
    g.UnitDefs = to_lua({i: {"name": u} for u, i in def_id.items()})
    g.Game = to_lua({"mapName": "Test Map"})
    g.WG = to_lua({"fonts": {}})
    g.WG.fonts.getFont = lambda *a: font
    g.VFS = to_lua({})
    g.VFS.LoadFile = lambda p: open(p).read() if os.path.exists(p) else None
    g.gl = to_lua({})
    g.gl.Color = g.gl.Rect = lambda *a: None
    spring = to_lua({})
    spring.GetGameSeconds = lambda: frame["n"] / 30
    spring.GetGameFrame = lambda: frame["n"]
    spring.GetTeamResources = lambda team, kind: (500.0, 1000.0, 0.0, 5.0, 0.0)
    spring.GetUnitIsBeingBuilt = lambda uid: (True, 0.5)
    spring.GetViewGeometry = lambda: (1920, 1080)
    spring.GetConfigFloat = lambda key, default: 1.0
    spring.GetMyTeamID = lambda: 0
    spring.GetSpectatingState = lambda: False
    commander = {"name": "corcom"}
    spring.GetTeamUnits = lambda team: to_lua([1])
    spring.GetUnitDefID = lambda uid: def_id[commander["name"]]
    spring.Echo = lambda s: None
    g.Spring = spring
    g.widget = to_lua({})

    with open(WIDGET, encoding="utf-8") as f:
        lua.execute(f.read())
    w = g.widget
    w.SetConfigData(w, to_lua({"x": 0.02, "y": 0.72}))  # saved by the first version: under the minimap
    w.Initialize(w)
    w.DrawScreen(w)
    assert any("waiting for the first step" in p for p in printed)
    # The old position is dropped: the panel starts at the top right, and its title can be dragged.
    assert not w.MousePress(w, 100, 770, 1)
    assert w.MousePress(w, 1890, 915, 1)
    w.MouseMove(w, 1790, 865, -100, -50)
    w.MouseRelease(w)
    saved = from_lua(w.GetConfigData(w))
    assert saved["version"] == 2 and saved["x"] < 0.95 and saved["y"] == pytest.approx(0.86 - 50 / 1080, abs=0.01)
    actions["buildguide_reset"]("buildguide_reset")
    assert "x" not in from_lua(w.GetConfigData(w))

    # Play the plan 3 s late; the first try of step 2 is cancelled and restarted.
    events = sorted([(s["start"] + 3, 0, k) for k, s in enumerate(plan["steps"])]
                    + [(s["finish"] + 3, 1, k) for k, s in enumerate(plan["steps"])])
    unit_ids, next_id, cancelled = {}, 100, False
    w.UnitCreated(w, 1, def_id["corcom"], 0)  # the commander spawns at frame 0: ignored
    frame["n"] = 1
    w.UnitCreated(w, 2, def_id["cor_hat_halloween_mask"], 0)  # cosmetics have no builder: ignored
    frame["n"] = 0
    end = max(s["finish"] for s in plan["steps"]) + 6
    i = 0
    while frame["n"] < end * 30:
        frame["n"] += 1
        while i < len(events) and events[i][0] <= frame["n"] / 30:
            _, finish, k = events[i]
            i += 1
            unit = def_id[plan["steps"][k]["unit"]]
            if finish:
                w.UnitFinished(w, unit_ids[k], unit, 0)
                continue
            if k == 1 and not cancelled:
                next_id += 1
                w.UnitCreated(w, next_id, unit, 0, 1)
                w.UnitDestroyed(w, next_id)
                cancelled = True
            next_id += 1
            unit_ids[k] = next_id
            w.UnitCreated(w, next_id, unit, 0, 1)
        w.GameFrame(w, frame["n"])
    printed.clear()
    w.DrawScreen(w)
    assert any("Goal reached at" in p and "(+3s)" in p for p in printed)
    w.Shutdown(w)

    logs = list(config.glob("build_guide_log*.json"))
    assert len(logs) == 1
    log = json.loads(logs[0].read_text())
    assert log["format"] == "bar-build-guide-log" and log["map"] == "Test Map"
    delays = [s["actual_start"] - s["planned_start"] for s in log["steps"]]
    assert all(d == pytest.approx(3, abs=0.05) for d in delays)
    assert all(s.get("actual_finish") for s in log["steps"])
    assert log["extra"] == [] and log["commander"] == "corcom"
    assert log["plan_targets"] == GOAL.targets
    assert log["goal_reached"] == pytest.approx(plan["finish_time"] + 3, abs=0.05)

    # Playing another faction: the panel says so.
    commander["name"] = "armcom"
    w.Initialize(w)  # fresh plan state, as after a restart
    frame["n"] = 1
    w.GameFrame(w, 1)
    w.UnitCreated(w, 900, def_id["armmex"], 0, 1)
    printed.clear()
    w.DrawScreen(w)
    assert any("This plan is for Cortex, you are playing Armada" in p for p in printed)
    assert log["resources"] and log["plan_resources"] == plan["resources"]
