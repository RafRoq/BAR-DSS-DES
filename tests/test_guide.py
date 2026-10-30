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
    assert all((s["builder_unit"], s["builder_n"]) == ("corlab", 1) for s in cons)
    assert plan["steps"][0]["builder"] == "Commander"
    assert (plan["steps"][0]["builder_unit"], plan["steps"][0]["builder_n"]) == ("corcom", 1)
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

class FakeBar:
    """The widget loaded under Lua 5.1 with a mock of the parts of the BAR widget API it uses."""

    def __init__(self, plan, tmp_path, monkeypatch, extra_units=()):
        lua51 = pytest.importorskip("lupa.lua51")
        self.config = tmp_path / "LuaUI" / "Config"
        self.config.mkdir(parents=True)
        (self.config / "build_guide_plan.json").write_text(json.dumps(plan))
        monkeypatch.chdir(tmp_path)

        lua = self.lua = lua51.LuaRuntime(unpack_returned_tuples=True)
        self.lua51 = lua51
        self.frame = 0
        self.printed = []
        self.actions = {}
        self.commands = []  # SendCommands
        self.selected = []
        self.picked = []  # SetActiveCommand calls
        self.queue = {}  # unitID -> number of queued commands
        self.unit_def = {}  # unitID -> unitDefID
        self.commander = "corcom"
        names = sorted({s["unit"] for s in plan["steps"]} | {"corcom", "armcom", "armmex", "corck", "corlab",
                                                              *extra_units})
        self.def_id = {u: i + 1 for i, u in enumerate(names)}
        builders = {"corcom", "armcom", "corck", "corlab"}
        unit_defs = {i: {"name": u, "id": i, "isBuilder": u in builders, "isFactory": u == "corlab"}
                     for u, i in self.def_id.items()}

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
        font, handler = g._mock(self.printed.append, self.actions.__setitem__)
        g.widgetHandler = handler
        g.Json = self.to_lua({})
        g.Json.decode = lambda s: self.to_lua(json.loads(s))
        g.Json.encode = lambda t: json.dumps(self.from_lua(t))
        g.UnitDefs = self.to_lua(unit_defs)
        g.UnitDefNames = self.to_lua({d["name"]: d for d in unit_defs.values()})
        g.Game = self.to_lua({"mapName": "Test Map"})
        g.WG = self.to_lua({"fonts": {}})
        g.WG.fonts.getFont = lambda *a: font
        g.VFS = self.to_lua({})
        g.VFS.LoadFile = lambda p: open(p).read() if os.path.exists(p) else None
        g.gl = self.to_lua({})
        g.gl.Color = g.gl.Rect = lambda *a: None
        spring = self.to_lua({})
        spring.GetGameSeconds = lambda: self.frame / 30
        spring.GetGameFrame = lambda: self.frame
        spring.GetTeamResources = lambda team, kind: (500.0, 1000.0, 0.0, 5.0, 0.0)
        spring.GetUnitIsBeingBuilt = lambda uid: (True, 0.5)
        spring.GetViewGeometry = lambda: (1920, 1080)
        spring.GetConfigFloat = lambda key, default: 1.0
        spring.GetMyTeamID = lambda: 0
        spring.GetSpectatingState = lambda: False
        spring.GetTeamUnits = lambda team: self.to_lua([1])
        spring.GetUnitDefID = lambda uid: self.def_id[self.commander] if uid == 1 else self.unit_def.get(uid)
        spring.GetSelectedUnits = lambda: self.to_lua(list(self.selected))
        spring.SelectUnitArray = lambda ids: self.select(self.from_lua(ids))
        # the build menu shows the selected builder's options; command index = -cmdID here
        spring.GetCmdDescIndex = lambda cmd: -cmd if self.selected else None
        spring.SetActiveCommand = lambda index: self.picked.append(index) or True
        spring.GetActiveCommand = lambda: None
        spring.GetUnitCommandCount = lambda uid: self.queue.get(uid, 0)
        spring.SendCommands = lambda c: self.commands.append(c)
        spring.Echo = lambda s: None
        g.Spring = spring
        g.widget = self.to_lua({})
        with open(WIDGET, encoding="utf-8") as f:
            lua.execute(f.read())
        self.w = g.widget

    def to_lua(self, o):
        if isinstance(o, dict):
            return self.lua.table_from({k: self.to_lua(v) for k, v in o.items()})
        if isinstance(o, list):
            return self.lua.table_from([self.to_lua(v) for v in o])
        return o

    def from_lua(self, t):
        if self.lua51.lua_type(t) != "table":
            return t
        keys = list(t.keys())
        if keys and all(isinstance(k, int) for k in keys):
            return [self.from_lua(t[i]) for i in range(1, len(keys) + 1)]
        return {k: self.from_lua(v) for k, v in t.items()} if keys else []

    def call(self, name, *args):
        return getattr(self.w, name)(self.w, *args)

    def select(self, ids):
        self.selected = list(ids)
        self.call("SelectionChanged", self.to_lua(list(ids)))

    def draw(self):
        self.printed.clear()
        self.call("DrawScreen")
        return list(self.printed)


def test_widget_follows_a_game_and_logs_it(run, tmp_path, monkeypatch):
    plan = plan_json(run)
    bar = FakeBar(plan, tmp_path, monkeypatch, extra_units=["cor_hat_halloween_mask"])
    def_id, w = bar.def_id, bar.w
    bar.call("SetConfigData", bar.to_lua({"x": 0.02, "y": 0.72}))  # saved by the first version: under the minimap
    bar.call("Initialize")
    assert "bind sc_mouse4 buildguide_next" in bar.commands
    assert any("waiting for the first step" in p for p in bar.draw())
    # The old position is dropped: the panel starts at the top right, and its title can be dragged.
    assert not bar.call("MousePress", 100, 770, 1)
    assert bar.call("MousePress", 1890, 915, 1)
    bar.call("MouseMove", 1790, 865, -100, -50)
    bar.call("MouseRelease")
    saved = bar.from_lua(bar.call("GetConfigData"))
    assert saved["version"] == 2 and saved["x"] < 0.95 and saved["y"] == pytest.approx(0.86 - 50 / 1080, abs=0.01)
    bar.actions["buildguide_reset"]("buildguide_reset")
    assert "x" not in bar.from_lua(bar.call("GetConfigData"))

    # Play the plan 3 s late; the first try of step 2 is cancelled and restarted.
    events = sorted([(s["start"] + 3, 0, k) for k, s in enumerate(plan["steps"])]
                    + [(s["finish"] + 3, 1, k) for k, s in enumerate(plan["steps"])])
    unit_ids, next_id, cancelled = {}, 100, False
    bar.call("UnitCreated", 1, def_id["corcom"], 0)  # the commander spawns at frame 0: ignored
    bar.frame = 1
    bar.call("UnitCreated", 2, def_id["cor_hat_halloween_mask"], 0)  # cosmetics have no builder: ignored
    bar.frame = 0
    end = max(s["finish"] for s in plan["steps"]) + 6
    i = 0
    while bar.frame < end * 30:
        bar.frame += 1
        while i < len(events) and events[i][0] <= bar.frame / 30:
            _, finish, k = events[i]
            i += 1
            unit = def_id[plan["steps"][k]["unit"]]
            if finish:
                bar.call("UnitFinished", unit_ids[k], unit, 0)
                continue
            if k == 1 and not cancelled:
                next_id += 1
                bar.call("UnitCreated", next_id, unit, 0, 1)
                bar.call("UnitDestroyed", next_id)
                cancelled = True
            next_id += 1
            unit_ids[k] = next_id
            bar.call("UnitCreated", next_id, unit, 0, 1)
        bar.call("GameFrame", bar.frame)
    assert any("Goal reached at" in p and "(+3s)" in p for p in bar.draw())
    bar.call("Shutdown")
    assert "unbind sc_mouse4 buildguide_next" in bar.commands

    logs = list(bar.config.glob("build_guide_log*.json"))
    assert len(logs) == 1
    log = json.loads(logs[0].read_text())
    assert log["format"] == "bar-build-guide-log" and log["map"] == "Test Map"
    delays = [s["actual_start"] - s["planned_start"] for s in log["steps"]]
    assert all(d == pytest.approx(3, abs=0.05) for d in delays)
    assert all(s.get("actual_finish") for s in log["steps"])
    assert log["extra"] == [] and log["commander"] == "corcom"
    assert log["plan_targets"] == GOAL.targets
    assert log["goal_reached"] == pytest.approx(plan["finish_time"] + 3, abs=0.05)
    assert log["resources"] and log["plan_resources"] == plan["resources"]

    # Playing another faction: the panel says so.
    bar.commander = "armcom"
    bar.call("Initialize")  # fresh plan state, as after a restart
    bar.frame = 1
    bar.call("GameFrame", 1)
    bar.call("UnitCreated", 900, def_id["armmex"], 0, 1)
    assert any("This plan is for Cortex, you are playing Armada" in p for p in bar.draw())


def test_place_next_picks_the_next_building_but_never_orders(run, tmp_path, monkeypatch):
    plan = plan_json(run)
    bar = FakeBar(plan, tmp_path, monkeypatch)
    def_id = bar.def_id
    bar.call("Initialize")
    bar.frame = 1
    bar.call("GameFrame", 1)  # finds the commander (unit 1)
    first = plan["steps"][0]
    assert first["builder_unit"] == "corcom"
    assert any("Mouse 4: place " + first["display"] + " with Commander" in p for p in bar.draw())

    # Mouse 4 with nothing selected: selects the commander, then picks the building once the
    # build menu has caught up with the selection.
    bar.actions["buildguide_next"]("buildguide_next")
    assert bar.selected == [1] and bar.picked == []
    bar.call("Update")
    assert bar.picked == [def_id[first["unit"]]]

    # The player places it: the step is taken, so the next pick is the commander's next step.
    bar.call("UnitCommand", 1, def_id["corcom"], 0, -def_id[first["unit"]])
    bar.queue[1] = 1
    bar.actions["buildguide_next"]("buildguide_next")
    bar.call("Update")
    second = next(s for s in plan["steps"][1:] if s["builder_unit"] == "corcom")
    assert bar.picked[-1] == def_id[second["unit"]]

    # Auto-pick: the selected commander goes idle without building it (order dropped): picked again.
    bar.picked.clear()
    bar.queue[1] = 0
    bar.call("UnitIdle", 1, def_id["corcom"], 0)
    bar.call("Update")
    assert bar.picked == [def_id[first["unit"]]]
    # ... but not while the commander still has orders queued.
    bar.picked.clear()
    bar.queue[1] = 2
    bar.call("UnitIdle", 1, def_id["corcom"], 0)
    bar.call("Update")
    assert bar.picked == []

    # Play every commander step so the Bot Lab exists; its step only selects the factory.
    bar.queue[1] = 0
    next_id = 100
    for s in plan["steps"]:
        if s["builder_unit"] != "corcom":
            break
        next_id += 1
        bar.unit_def[next_id] = def_id[s["unit"]]
        bar.call("UnitCreated", next_id, def_id[s["unit"]], 0, 1)
        bar.call("UnitFinished", next_id, def_id[s["unit"]], 0)
    factory_step = next(s for s in plan["steps"] if s["builder_unit"] == "corlab")
    lab = next(uid for uid, d in bar.unit_def.items() if d == def_id["corlab"])
    assert any(f"select Bot Lab 1, then queue {factory_step['display']}" in p for p in bar.draw())
    bar.picked.clear()
    bar.selected = []
    bar.actions["buildguide_next"]("buildguide_next")
    bar.call("Update")
    assert bar.selected == [lab] and bar.picked == []

    # Off switch for auto-pick, kept in the config.
    bar.actions["buildguide_autopick"]("buildguide_autopick")
    assert bar.from_lua(bar.call("GetConfigData"))["autopick"] is False

    # Moving place-next to another key: rebinds and is remembered.
    bar.actions["buildguide_key"]("buildguide_key", "Ctrl+sc_t")
    assert bar.commands[-2:] == ["unbind sc_mouse4 buildguide_next", "bind Ctrl+sc_t buildguide_next"]
    assert bar.from_lua(bar.call("GetConfigData"))["key"] == "Ctrl+sc_t"
    assert any("Ctrl+T: select Bot Lab 1" in p for p in bar.draw())
