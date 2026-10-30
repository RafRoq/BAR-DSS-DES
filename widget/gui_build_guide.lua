local widget = widget ---@type Widget

-- Build Order Guide: shows the next steps of a build order planned by the BAR build order
-- optimizer, and how far ahead or behind plan you are. Display only: it never gives orders.
--
-- Install: copy this file to <BAR data folder>/LuaUI/Widgets/ and the plan exported by the
-- optimizer to <BAR data folder>/LuaUI/Config/build_guide_plan.json, then enable
-- "Build Order Guide" in the widget list (F11).
--
-- During the game it writes what actually happened to LuaUI/Config/build_guide_log_<date>.json
-- (one file per game). Load it in the optimizer's web UI (In-game tab) to compare plan and game.
--
-- The panel starts at the top right, under the top bar; drag it by its title. Commands:
-- /luaui buildguide_reload (re-read the plan), /luaui buildguide_toggle (hide or show),
-- /luaui buildguide_reset (back to the default position).

function widget:GetInfo()
	return {
		name = "Build Order Guide",
		desc = "Follows a build order planned by the BAR build order optimizer and logs your real timings",
		author = "BAR build order optimizer",
		date = "2026",
		license = "GNU GPL, v2 or later",
		layer = -10, -- lower layers draw on top and get the mouse first: above the minimap (0)
		enabled = true,
	}
end

local PLAN_FILE = "LuaUI/Config/build_guide_plan.json"
local LOG_PREFIX = "LuaUI/Config/build_guide_log"
local logFile -- chosen when the first sample is taken, so each game gets its own file
local SAMPLE_EVERY = 5 -- seconds between resource samples
local SAVE_EVERY = 30 -- seconds between log writes (also written on every finished step)
local ROWS_BEFORE, ROWS_AFTER = 2, 6 -- steps shown around the next one
local ON_PLAN = 2 -- seconds of difference still shown as "on plan"

local spGetGameSeconds = Spring.GetGameSeconds
local spGetGameFrame = Spring.GetGameFrame
local spGetTeamResources = Spring.GetTeamResources
local spGetUnitIsBeingBuilt = Spring.GetUnitIsBeingBuilt
local spGetViewGeometry = Spring.GetViewGeometry

local plan, problem
local steps = {} -- plan steps plus actual_start / actual_finish / unitID
local stepOfUnit = {} -- unitID -> step
local extra = {} -- units built that are not in the plan
local samples = {}
local myTeamID = Spring.GetMyTeamID()
-- Commander -> faction, to tell the player when the plan is for another faction.
local FACTIONS = { armcom = "Armada", corcom = "Cortex", legcom = "Legion" }
local myCommander -- unit name of our commander, found on the first game frame
local hidden = false
local lastSample, lastSave = -SAMPLE_EVERY, 0

local vsx, vsy = spGetViewGeometry()
local uiScale = tonumber(Spring.GetConfigFloat("ui_scale", 1) or 1)
local font
local CONFIG_VERSION = 2 -- positions saved by older versions (top left, under the minimap) are dropped
local DEFAULT_TOP = 0.86 -- share of the screen height: just under BAR's top bar
local pos -- top-left corner as a share of the screen once dragged; nil = default, at the top right
local panel = { x1 = 0, y1 = 0, x2 = 0, y2 = 0, titleY = 0 }
local dragging = false

-- ------------------------------------------------------------------ helpers

local function clock(t)
	if not t then
		return "--:--"
	end
	local sign = t < 0 and "-" or ""
	t = math.abs(t)
	return string.format("%s%d:%02d", sign, math.floor(t / 60), math.floor(t % 60))
end

local function signed(d)
	if math.abs(d) < ON_PLAN then
		return "on plan"
	end
	return string.format("%+ds", math.floor(d + 0.5))
end

local function now()
	return spGetGameSeconds()
end

local function loadPlan()
	plan, problem, steps, stepOfUnit, extra, samples, logFile = nil, nil, {}, {}, {}, {}, nil
	myCommander = nil
	if not Json then
		problem = "Json library not available"
		return
	end
	local content = VFS.LoadFile(PLAN_FILE)
	if not content then
		problem = "No plan: put build_guide_plan.json in LuaUI/Config"
		return
	end
	local ok, decoded = pcall(Json.decode, content)
	if not ok or type(decoded) ~= "table" or decoded.format ~= "bar-build-guide" then
		problem = "build_guide_plan.json is not a build guide plan"
		return
	end
	plan = decoded
	for i, s in ipairs(plan.steps or {}) do
		steps[i] = s
	end
end

-- When every target of the plan was finished in the game, or nil.
local function goalTime()
	if not plan or type(plan.targets) ~= "table" then
		return nil
	end
	local reached = 0
	for unit, count in pairs(plan.targets) do
		local times = {}
		for _, s in ipairs(steps) do
			if s.unit == unit and s.actual_finish then
				times[#times + 1] = s.actual_finish
			end
		end
		if #times < count then
			return nil
		end
		table.sort(times)
		reached = math.max(reached, times[count])
	end
	return reached
end

local function nextIndex()
	for i, s in ipairs(steps) do
		if not s.actual_start then
			return i
		end
	end
	return #steps + 1
end

-- How far behind plan you are now (positive = late), from the most recent step you started
-- and from the next step if it is already overdue.
local function delay()
	local t = now()
	local d, latest
	for _, s in ipairs(steps) do
		if s.actual_start and (not latest or s.actual_start > latest) then
			latest, d = s.actual_start, s.actual_start - s.start
		end
	end
	local nxt = steps[nextIndex()]
	if nxt and t > nxt.start then
		d = math.max(d or 0, t - nxt.start)
	end
	return d
end

local function planResources(t)
	local r = plan and plan.resources
	if not r or #r == 0 then
		return nil
	end
	local i = math.max(1, math.min(#r, math.floor(t / SAMPLE_EVERY) + 1))
	return r[i]
end

local function writeLog()
	if not plan or not Json or #samples == 0 then
		return -- nothing recorded yet: never overwrite a log with an empty one
	end
	if not logFile then
		local stamp = os and os.date and os.date("%Y%m%d_%H%M%S")
		logFile = stamp and (LOG_PREFIX .. "_" .. stamp .. ".json") or (LOG_PREFIX .. ".json")
	end
	local out = {}
	for _, s in ipairs(steps) do
		out[#out + 1] = {
			n = s.n, unit = s.unit, display = s.display, builder = s.builder,
			planned_start = s.start, planned_finish = s.finish,
			actual_start = s.actual_start, actual_finish = s.actual_finish,
		}
	end
	local recorded
	if os and os.date then
		recorded = os.date("%Y-%m-%d %H:%M")
	end
	local log = {
		format = "bar-build-guide-log",
		version = 1,
		plan_name = plan.name,
		plan_label = plan.label,
		plan_commander = plan.commander,
		plan_targets = plan.targets,
		plan_finish_time = plan.finish_time,
		goal_reached = goalTime(),
		commander = myCommander,
		map = Game and Game.mapName,
		game_version = Game and Game.gameVersion,
		recorded = recorded,
		game_time = now(),
		steps = out,
		extra = extra,
		plan_resources = plan.resources,
		resources = samples, -- [t, metal, energy, metal income, energy income]
	}
	local ok, encoded = pcall(Json.encode, log)
	if not ok then
		return
	end
	local file = io.open(logFile, "w")
	if file then
		file:write(encoded)
		file:close()
	end
	lastSave = now()
end

-- ------------------------------------------------------------------ tracking

local function stepFor(unitName)
	for _, s in ipairs(steps) do
		if s.unit == unitName and not s.unitID then
			return s
		end
	end
end

local function findCommander()
	for _, unitID in ipairs(Spring.GetTeamUnits(myTeamID) or {}) do
		local def = UnitDefs[Spring.GetUnitDefID(unitID)]
		if def and FACTIONS[def.name] then
			return def.name
		end
	end
end

local function wrongFaction()
	if plan and myCommander and plan.commander and myCommander ~= plan.commander then
		return string.format("This plan is for %s, you are playing %s",
			FACTIONS[plan.commander] or plan.commander, FACTIONS[myCommander] or myCommander)
	end
end

function widget:UnitCreated(unitID, unitDefID, unitTeam, builderID)
	-- Units nobody built (the starting commander, cosmetics) are not part of the build.
	if unitTeam ~= myTeamID or not plan or spGetGameFrame() <= 0 or not builderID then
		return
	end
	local name = UnitDefs[unitDefID] and UnitDefs[unitDefID].name
	local s = name and stepFor(name)
	if s then
		s.unitID, s.actual_start = unitID, now()
		stepOfUnit[unitID] = s
	elseif name then
		extra[#extra + 1] = { unit = name, t = now() }
	end
end

function widget:UnitFinished(unitID, unitDefID, unitTeam)
	local s = stepOfUnit[unitID]
	if s and not s.actual_finish then
		s.actual_finish = now()
		writeLog()
	end
end

function widget:UnitDestroyed(unitID)
	local s = stepOfUnit[unitID]
	if s and not s.actual_finish then
		-- cancelled or destroyed before it finished: the step is open again
		s.unitID, s.actual_start = nil, nil
	end
	stepOfUnit[unitID] = nil
end

function widget:GameFrame(frame)
	if not plan then
		return
	end
	myCommander = myCommander or findCommander()
	local t = now()
	if t - lastSample >= SAMPLE_EVERY then
		lastSample = t
		local m, _, _, mi = spGetTeamResources(myTeamID, "metal")
		local e, _, _, ei = spGetTeamResources(myTeamID, "energy")
		samples[#samples + 1] = { math.floor(t * 10 + 0.5) / 10, m or 0, e or 0, mi or 0, ei or 0 }
	end
	if t - lastSave >= SAVE_EVERY then
		writeLog()
	end
end

function widget:GameOver()
	writeLog()
end

function widget:PlayerChanged()
	myTeamID = Spring.GetMyTeamID()
end

-- ------------------------------------------------------------------ drawing

local function text(s, x, y, size, r, g, b, a)
	if font then
		font:SetTextColor(r, g, b, a or 1)
		font:Print(s, x, y, size, "o")
	else
		gl.Color(r, g, b, a or 1)
		gl.Text(s, x, y, size, "o")
	end
end

function widget:ViewResize()
	vsx, vsy = spGetViewGeometry()
	uiScale = tonumber(Spring.GetConfigFloat("ui_scale", 1) or 1)
	if WG.fonts and WG.fonts.getFont then
		font = WG.fonts.getFont()
	end
end

function widget:DrawScreen()
	if hidden then
		return
	end
	local spec = Spring.GetSpectatingState()
	if spec then
		return
	end
	local size = 13 * uiScale
	local row = size * 1.45
	local width = 300 * uiScale -- grows to fit the text
	local pad = 8 * uiScale

	local lines = {}
	local function add(s, r, g, b) lines[#lines + 1] = { s, r, g, b } end

	local t = now()
	if not plan then
		add(problem or "No plan loaded", 1, 0.6, 0.4)
	else
		local d = delay()
		local status = d and signed(d) or "waiting for the first step"
		local late = d and d >= ON_PLAN
		add(string.format("%s   %s   %s", plan.name or "Build guide", clock(t), status),
			late and 1 or 0.6, late and 0.6 or 1, late and 0.4 or 0.6)
		local mismatch = wrongFaction()
		if mismatch then
			add(mismatch .. ": your units will not match its steps.", 1, 0.45, 0.4)
		end
		local pr = planResources(t)
		if pr then
			local m = spGetTeamResources(myTeamID, "metal") or 0
			local e = spGetTeamResources(myTeamID, "energy") or 0
			add(string.format("Metal %d (plan %d)   Energy %d (plan %d)",
				math.floor(m), math.floor(pr[2]), math.floor(e), math.floor(pr[3])), 0.8, 0.8, 0.8)
		end
		local nxt = nextIndex()
		local reached = goalTime()
		if reached and plan.finish_time then
			add(string.format("Goal reached at %s, plan %s (%s)", clock(reached), clock(plan.finish_time),
				signed(reached - plan.finish_time)), 0.6, 1, 0.6)
		elseif nxt > #steps then
			add(string.format("All %d steps started. Plan reaches the goal at %s", #steps, clock(plan.finish_time)), 0.6, 1, 0.6)
		end
		-- Rows: the last few finished steps, every step under construction, the next steps.
		local show, finished = {}, {}
		for i, s in ipairs(steps) do
			if s.actual_finish then
				finished[#finished + 1] = i
			elseif s.actual_start or (i >= nxt and i < nxt + ROWS_AFTER) then
				show[i] = true
			end
		end
		table.sort(finished, function(a, b) return steps[a].actual_finish < steps[b].actual_finish end)
		for k = math.max(1, #finished - ROWS_BEFORE + 1), #finished do
			show[finished[k]] = true
		end
		for i, s in ipairs(steps) do
			if show[i] then
				local who = s.builder
				for _, h in ipairs(s.helpers or {}) do
					who = who .. " + " .. h.builder
				end
				local label = string.format("%2d  %s  (%s)", s.n, s.display, who)
				if s.actual_finish then
					add(string.format("ok  %s   done %s, %s", label, clock(s.actual_finish), signed(s.actual_finish - s.finish)), 0.5, 0.8, 0.5)
				elseif s.actual_start then
					local _, progress = spGetUnitIsBeingBuilt(s.unitID)
					add(string.format(">>  %s   %d%%, plan done %s", label, math.floor((progress or 0) * 100), clock(s.finish)), 1, 0.9, 0.4)
				elseif i == nxt then
					local r, g, b = 1, 1, 1
					if t - s.start >= ON_PLAN then
						r, g, b = 1, 0.55, 0.45 -- overdue
					end
					add(string.format("->  %s   at %s", label, clock(s.start)), r, g, b)
				else
					add(string.format("    %s   at %s", label, clock(s.start)), 0.75, 0.75, 0.75)
				end
			end
		end
	end

	if font and font.GetTextWidth then
		for _, l in ipairs(lines) do
			width = math.max(width, font:GetTextWidth(l[1]) * size + pad * 2)
		end
	end
	local height = #lines * row + pad * 2
	local margin = math.floor(vsx * 0.01)
	local x1 = pos and math.floor(pos.x * vsx) or (vsx - width - margin)
	local top = math.floor((pos and pos.y or DEFAULT_TOP) * vsy)
	x1 = math.max(0, math.min(vsx - width, x1)) -- always fully on screen
	top = math.max(height, math.min(vsy, top))
	panel.x1, panel.y2 = x1, top
	panel.x2, panel.y1 = x1 + width, top - height
	panel.titleY = top - pad - row

	gl.Color(0, 0, 0, 0.55)
	gl.Rect(panel.x1, panel.y1, panel.x2, panel.y2)
	if font then
		font:Begin()
	end
	for i, l in ipairs(lines) do
		text(l[1], x1 + pad, top - pad - i * row + (row - size) / 2, size, l[2], l[3], l[4])
	end
	if font then
		font:End()
	end
	gl.Color(1, 1, 1, 1)
end

function widget:MousePress(x, y, button)
	if hidden or button ~= 1 then
		return false
	end
	if x >= panel.x1 and x <= panel.x2 and y >= panel.titleY and y <= panel.y2 then
		dragging = true
		return true
	end
	return false
end

function widget:MouseMove(x, y, dx, dy)
	if dragging then
		pos = pos or { x = panel.x1 / vsx, y = panel.y2 / vsy }
		pos.x = math.max(0, math.min(1, pos.x + dx / vsx))
		pos.y = math.max(0, math.min(1, pos.y + dy / vsy))
	end
end

function widget:MouseRelease()
	dragging = false
end

-- ------------------------------------------------------------------ setup

function widget:GetConfigData()
	return { version = CONFIG_VERSION, x = pos and pos.x, y = pos and pos.y, hidden = hidden }
end

function widget:SetConfigData(data)
	if data.version == CONFIG_VERSION and data.x and data.y then
		pos = { x = data.x, y = data.y }
	end
	hidden = data.hidden == true
end

function widget:Initialize()
	widget:ViewResize()
	loadPlan()
	widgetHandler:AddAction("buildguide_reload", function()
		loadPlan()
		Spring.Echo("Build Order Guide: " .. (plan and ("loaded " .. #steps .. " steps") or problem))
		return true
	end, nil, "t")
	widgetHandler:AddAction("buildguide_toggle", function()
		hidden = not hidden
		return true
	end, nil, "t")
	widgetHandler:AddAction("buildguide_reset", function()
		pos, hidden = nil, false
		return true
	end, nil, "t")
end

function widget:Shutdown()
	writeLog()
	widgetHandler:RemoveAction("buildguide_reload")
	widgetHandler:RemoveAction("buildguide_toggle")
	widgetHandler:RemoveAction("buildguide_reset")
end
