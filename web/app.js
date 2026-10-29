"use strict";

/* ====================================================================== helpers */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** Create an element: h("div", {class: "x", onclick: fn}, "text", child, ...) */
function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "class") node.className = value;
    else if (key === "style") node.style.cssText = value;
    else if (key in node && key !== "list") node[key] = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function api(path, options = {}) {
  const init = { ...options, headers: { "Content-Type": "application/json" } };
  if (init.body && typeof init.body !== "string") init.body = JSON.stringify(init.body);
  const response = await fetch(path, init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
  return data;
}

const fmt = (v, digits = 0) =>
  v === null || v === undefined || !Number.isFinite(Number(v))
    ? "—"
    : Number(v).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });

function clock(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "—";
  const m = Math.floor(seconds / 60);
  const s = seconds - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
}

const tok = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

let toastTimer = null;
function toast(message, isError = false) {
  const box = $("#toast");
  box.textContent = message;
  box.className = "toast" + (isError ? " error" : "");
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (box.hidden = true), isError ? 6000 : 3500);
}

function storageGet(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function storageSet(key, value) {
  try { localStorage.setItem(key, value); } catch { /* private mode: ignore */ }
}

function download(filename, text, type = "text/yaml") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = h("a", { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

const slug = (text) => text.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || "build";

function yamlScalar(value) {
  const s = String(value);
  return /^[\w .()'-]+$/.test(s) && !/^\d/.test(s) ? s : JSON.stringify(s);
}

/** YAML in the recipe format of interface.py / recipes/*.yaml. */
function recipeToYaml(steps) {
  return steps.map((step) =>
    `- name: ${yamlScalar(step.name)}\n  repeat: ${step.repeat}\n  builders:\n` +
    step.builders.map((b) => `  - ${yamlScalar(b)}`).join("\n")
  ).join("\n") + "\n";
}

/* ====================================================================== state */

const S = {
  defaults: null,
  catalog: [],
  units: {},              // name -> catalog entry
  targets: [],            // [{unit, count}]
  configExtra: {},        // config keys not shown in the form
  runs: [],               // [{id, data, slot}]
  selected: null,         // run id
  compare: [],            // run ids
  cursor: null,           // seconds, shared by timeline and builders views
  selectedJob: null,
  stepsFilter: "",
  tab: "timeline",
  job: null,
  recipe: [],             // [{unit, repeat, builders: []}]
  gameLog: null,          // in-game guide log loaded in the In-game tab
  gameLogName: null,      // its file name in the BAR data folder, if loaded from there
  barData: null,          // BAR data folder found by the server
  nextRunId: 1,
};

const COMPARE_SLOTS = ["--series-1", "--series-2", "--series-3", "--series-4"];

const WEIGHT_FIELDS = [
  { key: "stall", label: "Stall", min: 0, max: 0.3, step: 0.0001,
    hint: (w) => `Commander fully stalled for 1 s adds ${fmt(w * 300, 1)} s` },
  { key: "overflow_metal", label: "Metal overflow", min: 0, max: 2, step: 0.01,
    hint: (w) => `100 metal lost to full storage adds ${fmt(w * 100, 1)} s` },
  { key: "overflow_energy", label: "Energy overflow", min: 0, max: 0.1, step: 0.001,
    hint: (w) => `1000 energy lost to full storage adds ${fmt(w * 1000, 1)} s` },
  { key: "idle", label: "Wasted build power", min: 0, max: 0.05, step: 0.0001,
    hint: (w) => `Commander idle for 1 s while resources pile up adds ${fmt(w * 300, 2)} s` },
];
const CONFIG_FIELDS = [
  { key: "wind_average", label: "Average wind", min: 0, max: 25, step: 0.1, hint: () => "Energy/s per wind turbine (turbines cap at 25)" },
  { key: "tidal_strength", label: "Tidal strength", min: 0, max: 30, step: 0.5, hint: () => "Energy/s per tidal generator" },
  { key: "metal_per_spot", label: "Metal per spot", min: 0.5, max: 5, step: 0.1, hint: () => "Metal/s of a T1 extractor on one spot" },
  { key: "mex_spots", label: "Metal spots", min: 0, max: 20, step: 1, int: true, hint: () => "Spots your builders can use (no travel time)" },
  { key: "converter_level", label: "Converter level", min: 0, max: 1, step: 0.05, hint: (v) => `Converters use energy above ${fmt(v * 100)}% of storage` },
  { key: "start_metal", label: "Starting metal", min: 0, max: 5000, step: 50, int: true },
  { key: "start_energy", label: "Starting energy", min: 0, max: 5000, step: 50, int: true },
];
const SEARCH_FIELDS = [
  { key: "beam_width", label: "Beam width", min: 10, max: 500, step: 10, int: true,
    hint: (v) => `Runs widths ${Math.max(5, Math.floor(v / 4))}, ${Math.max(5, Math.floor(v / 2))} and ${v}; wider is slower, usually better` },
  { key: "constructor_cap", label: "Max constructors per type", min: 1, max: 8, step: 1, int: true },
  { key: "max_delay", label: "Longest wait considered", min: 10, max: 180, step: 5, int: true, hint: () => "Seconds a builder may wait to start something without stalling" },
  { key: "improve_seconds", label: "Improvement time", min: 0, max: 180, step: 5, int: true,
    hint: (v) => (v ? `Then spends ${v} s polishing the best build (reordering steps, re-planning the rest)` : "Off: keep the best beam result as is") },
];

const sliders = { weights: {}, config: {}, search: {} };

/* ====================================================================== form: sliders */

function makeSlider(container, field, value, group) {
  const range = h("input", { type: "range", min: field.min, max: field.max, step: field.step, value, "aria-label": field.label });
  const number = h("input", { type: "number", min: field.min, step: field.step, value, "aria-label": `${field.label} value` });
  const hint = h("span", { class: "hint" });
  const decimals = (String(field.step).split(".")[1] || "").length;
  const sync = (raw) => {
    const v = Number(Number(raw).toFixed(decimals));
    range.value = v;
    number.value = v;
    hint.textContent = field.hint ? field.hint(Number(v)) : "";
  };
  range.addEventListener("input", () => sync(range.value));
  number.addEventListener("input", () => {
    range.value = number.value;
    hint.textContent = field.hint ? field.hint(Number(number.value)) : "";
  });
  container.append(h("div", { class: "slider" },
    h("div", { class: "slider-top" }, h("span", {}, field.label), number), range, hint));
  sync(value);
  sliders[group][field.key] = {
    get: () => (field.int ? Math.round(Number(number.value)) : Number(number.value)),
    set: sync,
  };
}

function buildForm() {
  const d = S.defaults;
  for (const f of WEIGHT_FIELDS) makeSlider($("#weights"), f, d.weights[f.key], "weights");
  for (const f of CONFIG_FIELDS) makeSlider($("#config"), f, d.config[f.key], "config");
  for (const f of SEARCH_FIELDS) makeSlider($("#search"), f, d.search[f.key], "search");
  S.configExtra = Object.fromEntries(
    Object.entries(d.config).filter(([k]) => !CONFIG_FIELDS.some((f) => f.key === k)));
  $("#weights-reset").addEventListener("click", () => {
    for (const f of WEIGHT_FIELDS) sliders.weights[f.key].set(d.weights[f.key]);
  });
}

function readGoal() {
  const pick = (group) => Object.fromEntries(Object.entries(sliders[group]).map(([k, s]) => [k, s.get()]));
  return {
    name: $("#goal-name").value.trim() || "goal",
    targets: Object.fromEntries(S.targets.filter((t) => t.count > 0).map((t) => [t.unit, t.count])),
    max_time: Number($("#max-time").value) || 900,
    weights: pick("weights"),
    config: { ...S.configExtra, ...pick("config") },
  };
}

function readSearch() {
  const search = Object.fromEntries(Object.entries(sliders.search).map(([k, s]) => [k, s.get()]));
  search.affordability = $("#affordability").value;
  return search;
}

function writeGoal(goal) {
  const d = S.defaults;
  $("#goal-name").value = goal.name || "goal";
  $("#max-time").value = goal.max_time || 900;
  S.targets = Object.entries(goal.targets || {}).map(([unit, count]) => ({ unit, count: Number(count) }));
  const weights = { ...d.weights, ...(goal.weights || {}) };
  for (const f of WEIGHT_FIELDS) sliders.weights[f.key].set(weights[f.key]);
  const config = { ...d.config, ...(goal.config || {}) };
  for (const f of CONFIG_FIELDS) sliders.config[f.key].set(config[f.key]);
  S.configExtra = Object.fromEntries(Object.entries(config).filter(([k]) => !CONFIG_FIELDS.some((f) => f.key === k)));
  const search = { ...d.search, ...(goal.search || {}) };
  for (const f of SEARCH_FIELDS) sliders.search[f.key].set(search[f.key]);
  $("#affordability").value = search.affordability || "both";
  renderTargets();
}

/* ====================================================================== form: targets */

function unitLabel(name) {
  const u = S.units[name];
  return u ? u.display : name;
}

function renderTargets() {
  const list = $("#targets");
  list.replaceChildren();
  if (!S.targets.length) {
    list.append(h("li", { class: "empty" }, "No targets yet."));
    return;
  }
  S.targets.forEach((t, i) => {
    list.append(h("li", {},
      h("span", { class: "unit-name", title: S.units[t.unit]?.description || "" },
        unitLabel(t.unit), h("span", { class: "unit-code" }, t.unit)),
      h("input", { type: "number", min: 1, step: 1, value: t.count, "aria-label": `Count of ${unitLabel(t.unit)}`,
        oninput: (e) => (t.count = Math.max(0, Math.round(Number(e.target.value) || 0))) }),
      h("button", { type: "button", class: "icon", "aria-label": `Remove ${unitLabel(t.unit)}`,
        onclick: () => { S.targets.splice(i, 1); renderTargets(); } }, "✕")));
  });
}

function addTarget(name) {
  const existing = S.targets.find((t) => t.unit === name);
  if (existing) existing.count += 1;
  else S.targets.push({ unit: name, count: 1 });
  renderTargets();
}

/* ====================================================================== combobox */

function unitMeta(u) {
  const parts = [`T${fmt(u.tech_level)}`, `${fmt(u.metal)} M`, `${fmt(u.energy)} E`, `${fmt(u.build_time)} build time`];
  if (u.economy.length) parts.push(u.economy[0]);
  return parts.join(" · ");
}

function searchUnits(query, filter) {
  const q = query.trim().toLowerCase();
  const scored = [];
  for (const u of S.catalog) {
    if (filter && !filter(u)) continue;
    let score;
    if (!q) score = u.depth;
    else if (u.name === q) score = -100;
    else if (u.name.startsWith(q)) score = -50;
    else if (u.display.toLowerCase().startsWith(q)) score = -40;
    else if (u.display.toLowerCase().includes(q)) score = -20;
    else if (u.description.toLowerCase().includes(q) || u.name.includes(q)) score = -5;
    else continue;
    scored.push([score + u.depth * 0.01, u]);
  }
  return scored.sort((a, b) => a[0] - b[0]).slice(0, 40).map(([, u]) => u);
}

function makeCombo(input, list, onPick, filter) {
  let items = [];
  let active = -1;
  const close = () => {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    active = -1;
  };
  const highlight = () => {
    $$("li", list).forEach((li, i) => li.setAttribute("aria-selected", String(i === active)));
    list.children[active]?.scrollIntoView({ block: "nearest" });
  };
  const pick = (u) => {
    onPick(u.name);
    input.value = "";
    close();
  };
  const open = () => {
    items = searchUnits(input.value, filter);
    list.replaceChildren(...items.map((u, i) =>
      h("li", { role: "option", id: `${list.id}-${i}`, onmousedown: (e) => { e.preventDefault(); pick(u); } },
        h("span", {}, u.display, h("span", { class: "unit-code" }, u.name)),
        h("span", { class: "meta" }, unitMeta(u)))));
    if (!items.length) list.append(h("li", { class: "meta" }, "No matching unit the commander can reach."));
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    active = items.length ? 0 : -1;
    highlight();
  };
  input.addEventListener("input", open);
  input.addEventListener("focus", open);
  input.addEventListener("blur", () => setTimeout(close, 120));
  input.addEventListener("keydown", (e) => {
    if (list.hidden && e.key === "ArrowDown") return open();
    if (e.key === "ArrowDown") active = Math.min(items.length - 1, active + 1);
    else if (e.key === "ArrowUp") active = Math.max(0, active - 1);
    else if (e.key === "Enter" && items[active]) { e.preventDefault(); return pick(items[active]); }
    else if (e.key === "Escape") return close();
    else return;
    e.preventDefault();
    highlight();
  });
}

/* ====================================================================== files */

async function refreshFiles() {
  const files = await api("/api/files");
  const fill = (select, names, extra = []) => {
    const keep = select.value;
    select.replaceChildren(...extra, ...names.map((n) => h("option", { value: n }, n)));
    if ([...select.options].some((o) => o.value === keep)) select.value = keep;
  };
  fill($("#goal-file"), files.goals || []);
  fill($("#recipe-file"), files.recipes || []);
  fill($("#compare-with"), files.recipes || [], [
    h("option", { value: "" }, "Nothing"),
    h("option", { value: "@editor" }, "The recipe editor's build"),
  ]);
}

function askFileName(kind, suggestion) {
  const name = prompt(`Save to ${kind}/ as:`, `${suggestion}.yaml`);
  if (!name) return null;
  const clean = name.trim().endsWith(".yaml") || name.trim().endsWith(".yml") ? name.trim() : `${name.trim()}.yaml`;
  if (!/^[A-Za-z0-9][A-Za-z0-9 _.-]{0,80}\.ya?ml$/.test(clean)) {
    toast("Use letters, digits, spaces, dots, dashes or underscores.", true);
    return null;
  }
  return clean;
}

async function saveFile(kind, name, content) {
  const existing = [...$(kind === "goals" ? "#goal-file" : "#recipe-file").options].map((o) => o.value);
  if (existing.includes(name) && !confirm(`${kind}/${name} exists. Overwrite it?`)) return false;
  await api(`/api/files/${kind}/${encodeURIComponent(name)}`, { method: "PUT", body: { content } });
  await refreshFiles();
  toast(`Saved ${kind}/${name}`);
  return true;
}

async function loadGoalFile() {
  const name = $("#goal-file").value;
  if (!name) return;
  try {
    const { content } = await api(`/api/files/goals/${encodeURIComponent(name)}`);
    writeGoal(content || {});
    toast(`Loaded goals/${name}`);
  } catch (e) { toast(e.message, true); }
}

async function saveGoalFile() {
  const goal = readGoal();
  const name = askFileName("goals", slug(goal.name));
  if (!name) return;
  const search = readSearch();
  if (search.affordability === "both") delete search.affordability;
  try { await saveFile("goals", name, { ...goal, search }); } catch (e) { toast(e.message, true); }
}

/* ====================================================================== runs */

function runById(id) {
  return S.runs.find((r) => r.id === id);
}

function addRun(data) {
  const run = { id: S.nextRunId++, data, slot: null };
  run.label = data.label;
  S.runs.push(run);
  S.selected = run.id;
  S.cursor = null;
  S.selectedJob = null;
  renderRuns();
  renderTab();
  return run;
}

function setCompare(id, on) {
  const run = runById(id);
  if (on) {
    if (S.compare.includes(id)) return;
    if (S.compare.length >= 4) {
      toast("Compare up to 4 runs at a time.", true);
      renderRuns();
      return;
    }
    const used = new Set(S.compare.map((cid) => runById(cid).slot));
    run.slot = [0, 1, 2, 3].find((s) => !used.has(s));
    S.compare.push(id);
  } else {
    S.compare = S.compare.filter((c) => c !== id);
    run.slot = null;
  }
  renderRuns();
  if (S.tab === "compare") renderTab();
}

function removeRun(id) {
  setCompare(id, false);
  S.runs = S.runs.filter((r) => r.id !== id);
  if (S.selected === id) S.selected = S.runs.at(-1)?.id ?? null;
  renderRuns();
  renderTab();
}

function statusLine(ev) {
  const items = [];
  if (!ev.reached) items.push(h("span", { class: "status status-critical" }, "goal not reached"));
  else if (ev.stall_seconds > 0.05) items.push(h("span", { class: "status status-critical" }, `stall ${fmt(ev.stall_seconds, 1)} s`));
  else items.push(h("span", { class: "status status-good" }, "no stall"));
  if ((ev.overflow_metal || 0) > 1 || (ev.overflow_energy || 0) > 50)
    items.push(h("span", { class: "status status-warning" }, "overflow"));
  return items;
}

function renderRuns() {
  const strip = $("#runs-strip");
  strip.replaceChildren();
  if (!S.runs.length) {
    strip.append(h("p", { class: "empty" }, "No runs yet. Set a goal on the left and press ",
      h("strong", {}, "Optimize"), ", or score a hand-written build in the Recipe editor."));
    return;
  }
  for (const run of S.runs) {
    const ev = run.data.evaluation;
    const compareBox = h("input", { type: "checkbox", checked: S.compare.includes(run.id),
      "aria-label": `Compare ${run.label}`,
      onclick: (e) => e.stopPropagation(),
      onchange: (e) => setCompare(run.id, e.target.checked) });
    strip.append(h("div", { class: "run-chip", role: "button", tabindex: 0,
      "aria-pressed": String(S.selected === run.id),
      onclick: () => selectRun(run.id),
      onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectRun(run.id); } } },
      h("span", { class: "run-kind" }, run.data.kind === "optimizer" ? "Optimizer" : "Hand-written"),
      h("span", { class: "run-label", title: run.label }, run.label),
      h("span", { class: "run-stats" },
        ev.reached ? `Goal at ${clock(ev.finish_time)} · cost ${fmt(ev.cost, 1)}` : `Stopped at ${clock(run.data.end_time)}`),
      h("span", { class: "run-foot" }, ...statusLine(ev)),
      h("label", { class: "run-foot", onclick: (e) => e.stopPropagation() }, compareBox, "Compare",
        run.slot !== null ? h("span", { class: "swatch-line", style: `background: var(${COMPARE_SLOTS[run.slot]})` }) : null),
      h("button", { type: "button", class: "remove", "aria-label": `Remove ${run.label}`,
        onclick: (e) => { e.stopPropagation(); removeRun(run.id); } }, "✕")));
  }
}

function selectRun(id) {
  if (S.selected !== id) {
    S.selected = id;
    S.cursor = null;
    S.selectedJob = null;
    S.stepsFilter = "";
  }
  renderRuns();
  if (S.tab === "compare" || S.tab === "recipe") setTab("timeline");
  else renderTab();
}

/* ====================================================================== optimize + progress */

async function runOptimize() {
  const goal = readGoal();
  if (!Object.keys(goal.targets).length) return toast("Add at least one target to the goal.", true);
  const compareWith = $("#compare-with").value;
  $("#run-btn").disabled = true;
  try {
    let handRun = null;
    if (compareWith) {
      try {
        const steps = compareWith === "@editor"
          ? editorSteps()
          : (await api(`/api/files/recipes/${encodeURIComponent(compareWith)}`)).content;
        const label = compareWith === "@editor" ? "Recipe editor build" : `Hand: ${compareWith}`;
        handRun = addRun(await api("/api/evaluate", { method: "POST", body: { goal, recipe: steps, label } }));
      } catch (e) {
        toast(`Could not score the comparison build: ${e.message}`, true);
      }
    }
    const job = await api("/api/optimize", {
      method: "POST", body: { goal, search: readSearch(), label: `Optimizer: ${goal.name}` } });
    S.job = { id: job.id, handRun };
    showProgress(job);
    setTimeout(pollJob, 400);
  } catch (e) {
    toast(e.message, true);
    $("#run-btn").disabled = false;
  }
}

function showProgress(job) {
  $("#progress").hidden = false;
  $("#cancel-btn").disabled = job.status !== "running";
  $("#progress-elapsed").textContent = `${fmt(job.elapsed, 0)} s`;
  const runs = job.progress;
  const doneCosts = runs.filter((r) => r.status === "done" && r.best_cost !== null).map((r) => r.best_cost);
  const bestCost = doneCosts.length ? Math.min(...doneCosts) : null;
  const current = runs.findIndex((r) => r.status === "running");
  $("#progress-title").textContent = job.status === "running"
    ? `Searching… run ${current < 0 ? runs.filter((r) => r.status === "done").length : current + 1} of ${runs.length}`
    : { done: "Search finished", cancelled: "Stopped", failed: "Search failed" }[job.status] || job.status;
  $("#progress-runs").replaceChildren(...runs.map((r) => {
    const pct = r.status === "done" ? 100 : Math.round(Math.min(1, Math.max(0, r.fraction)) * 100);
    const isBest = r.status === "done" && r.best_cost !== null && r.best_cost === bestCost;
    const label = r.mode === "improve" ? `improving best build · ${r.width} s` : `${r.mode} · beam ${r.width}`;
    return h("li", { class: `${r.status === "done" ? "done" : ""} ${isBest ? "best" : ""}` },
      h("div", { class: "progress-row" },
        h("span", {}, label),
        r.best_cost !== null ? h(isBest ? "strong" : "span", {}, `${isBest ? "best " : ""}cost ${fmt(r.best_cost, 1)}`)
          : h("span", {}, r.status === "pending" ? "waiting" : `${fmt(r.nodes)} ${r.mode === "improve" ? "tries" : "nodes"}`)),
      h("div", { class: "bar", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": pct,
        "aria-label": label }, h("span", { style: `width: ${pct}%` })));
  }));
}

async function pollJob() {
  if (!S.job) return;
  let job;
  try {
    job = await api(`/api/jobs/${S.job.id}`);
  } catch (e) {
    toast(`Lost contact with the optimizer: ${e.message}`, true);
    $("#run-btn").disabled = false;
    return;
  }
  showProgress(job);
  if (job.status === "running") {
    setTimeout(pollJob, 500);
    return;
  }
  const { handRun } = S.job;
  S.job = null;
  $("#run-btn").disabled = false;
  if (job.result) {
    const run = addRun(job.result);
    if (handRun) {
      setCompare(handRun.id, true);
      setCompare(run.id, true);
    }
    const took = fmt(job.elapsed, job.elapsed < 10 ? 1 : 0);
    toast(job.status === "cancelled"
      ? `Stopped after ${took} s; showing the best build found.`
      : `Search finished in ${took} s.`);
  } else {
    toast(job.error || "The search found no build.", true);
  }
}

/* ====================================================================== charts: shared */

const charts = {};

function chartFor(id) {
  const dom = document.getElementById(id);
  let c = charts[id];
  if (c && c.getDom() !== dom) { c.dispose(); c = null; }
  if (!c) {
    c = echarts.init(dom, null, { renderer: "canvas" });
    charts[id] = c;
  }
  return c;
}

function disposeCharts() {
  for (const [id, c] of Object.entries(charts)) { c.dispose(); delete charts[id]; }
}

function axisStyle() {
  return {
    axisLine: { lineStyle: { color: tok("--axis") } },
    axisTick: { show: false },
    axisLabel: { color: tok("--text-muted"), fontSize: 11 },
    splitLine: { lineStyle: { color: tok("--grid") } },
  };
}

function baseOption(maxTime) {
  const axis = axisStyle();
  return {
    animation: false,
    textStyle: { fontFamily: tok("--font") },
    grid: { left: 52, right: 14, top: 12, bottom: 24 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", lineStyle: { color: tok("--text-muted") } },
      backgroundColor: tok("--surface-raised"),
      borderColor: tok("--border"),
      textStyle: { color: tok("--text-primary"), fontSize: 12 },
      confine: true,
    },
    xAxis: { type: "value", min: 0, max: maxTime, ...axis, splitLine: { show: false },
      axisLabel: { ...axis.axisLabel, formatter: (v) => clock(v).replace(/\.\d$/, "") } },
    yAxis: { type: "value", ...axis },
    dataZoom: [{ type: "inside", xAxisIndex: 0, filterMode: "none", zoomOnMouseWheel: "ctrl", moveOnMouseMove: false }],
  };
}

function legend(containerId, items) {
  $(`#${containerId}`).replaceChildren(...items.map(({ label, color, kind }) =>
    h("span", { class: "item" },
      h("span", { class: kind === "line" ? "swatch-line" : "swatch", style: `background: ${color}` }), label)));
}

function tooltipRows(params, unit = "") {
  const list = Array.isArray(params) ? params : [params];
  const t = list[0]?.value?.[0];
  const rows = list.filter((p) => p.seriesName && !p.seriesName.startsWith("_")).map((p) =>
    `<div style="display:flex;justify-content:space-between;gap:16px"><span>${p.marker}${p.seriesName}</span>` +
    `<strong>${fmt(p.value[1], Math.abs(p.value[1]) < 10 ? 1 : 0)}${unit}</strong></div>`);
  return `<div style="margin-bottom:4px;color:${tok("--text-secondary")}">${clock(t)} (${fmt(t, 1)} s)</div>${rows.join("")}`;
}

/* ====================================================================== derived run data */

function derived(run) {
  if (run.derived) return run.derived;
  const s = run.data.segments;
  const n = s.t0.length;
  const pts = (a, b) => {
    const out = [];
    for (let i = 0; i < n; i++) { out.push([s.t0[i], a[i]]); out.push([s.t1[i], b[i]]); }
    return out;
  };
  const steps = (values, minDur = 0) => {
    const out = [];
    for (let i = 0; i < n; i++) {
      if (s.t1[i] - s.t0[i] < minDur && n > 1) continue;
      out.push([s.t0[i], values[i]], [s.t1[i], values[i]]);
    }
    return out;
  };
  const intervals = (test) => {
    const out = [];
    for (let i = 0; i < n; i++) {
      if (s.t1[i] <= s.t0[i] || !test(i)) continue;
      const last = out.at(-1);
      if (last && Math.abs(last[1] - s.t0[i]) < 1e-6) last[1] = s.t1[i];
      else out.push([s.t0[i], s.t1[i]]);
    }
    return out;
  };
  const busy = s.total_bp.map((t, i) => t - s.idle_bp[i]);
  const level = run.data.goal.config?.converter_level ?? 0.75;
  run.derived = {
    n,
    metal: pts(s.metal0, s.metal1),
    energy: pts(s.energy0, s.energy1),
    metalCap: steps(s.max_metal),
    energyCap: steps(s.max_energy),
    convLevel: steps(s.max_energy.map((m) => m * level)),
    metalIncome: steps(s.metal_income, 0.05),
    metalSpend: steps(s.metal_spend, 0.05),
    energyIncome: steps(s.energy_income, 0.05),
    energySpend: steps(s.energy_spend, 0.05),
    converterUse: steps(s.converter_use, 0.05),
    busy: steps(busy),
    total: steps(s.total_bp),
    stalls: intervals((i) => s.speed[i] < 1 - 1e-9),
    metalOverflow: intervals((i) => s.metal0[i] >= s.max_metal[i] - 1e-6 && s.metal_income[i] - s.metal_spend[i] > 1e-6),
    energyOverflow: intervals((i) => s.energy0[i] >= s.max_energy[i] - 1e-6 &&
      s.energy_income[i] - s.converter_use[i] - s.energy_spend[i] > 1e-6),
    end: run.data.end_time,
  };
  return run.derived;
}

function bands(list, color, label) {
  return list.map(([a, b], i) => [
    { xAxis: a, itemStyle: { color }, label: i === 0 && label ? { show: true, formatter: label, color: tok("--text-secondary"), fontSize: 11, position: "insideTopLeft" } : { show: false } },
    { xAxis: b },
  ]);
}

const lineSeries = (name, data, color, extra = {}) => ({
  name, type: "line", data, showSymbol: false, lineStyle: { width: 2, color }, itemStyle: { color },
  emphasis: { disabled: true }, ...extra,
});

function cursorSeries() {
  return {
    id: "_cursor", name: "_cursor", type: "line", data: [], silent: true,
    markLine: { symbol: "none", silent: true, animation: false, label: { show: false },
      lineStyle: { color: tok("--text-secondary"), width: 1, type: "solid" },
      data: S.cursor === null ? [] : [{ xAxis: S.cursor }] },
  };
}

/* ====================================================================== timeline tab */

const TIMELINE_CHARTS = ["ch-metal", "ch-energy", "ch-mflow", "ch-eflow", "ch-bp"];

function renderTimeline(run) {
  const d = derived(run);
  const ev = run.data.evaluation;
  $("#timeline-title").replaceChildren(run.label,
    h("span", { class: "hint" }, `${run.data.kind === "optimizer" ? "optimizer" : "hand-written"} · goal: ${Object.entries(run.data.goal.targets).map(([u, n]) => `${n}× ${unitLabel(u)}`).join(", ")}`));
  renderTiles(run);

  const max = Math.ceil(d.end);
  const critical = tok("--critical");
  const stallColor = colorMix(critical, 0.16);
  const overflowColor = colorMix(tok("--warning"), 0.28);
  const c1 = tok("--series-1"), c2 = tok("--series-2"), c3 = tok("--series-3"), c4 = tok("--series-4");
  const muted = tok("--text-muted"), idleColor = tok("--idle");

  const stallMark = () => ({ silent: true, data: bands(d.stalls, stallColor, "stall") });

  const opts = {
    "ch-metal": {
      series: [
        lineSeries("Metal", d.metal, c1, { markArea: { silent: true, data: [...bands(d.stalls, stallColor, "stall"), ...bands(d.metalOverflow, overflowColor, "overflow")] } }),
        lineSeries("Storage", d.metalCap, muted, { lineStyle: { width: 1, color: muted } }),
      ],
      legend: [["lg-metal", [{ label: "Metal", color: c1, kind: "line" }, { label: "Storage", color: muted, kind: "line" }]]],
    },
    "ch-energy": {
      series: [
        lineSeries("Energy", d.energy, c2, { markArea: { silent: true, data: [...bands(d.stalls, stallColor, "stall"), ...bands(d.energyOverflow, overflowColor, "overflow")] } }),
        lineSeries("Storage", d.energyCap, muted, { lineStyle: { width: 1, color: muted } }),
        lineSeries("Converter level", d.convLevel, muted, { lineStyle: { width: 1, color: muted, type: [4, 3] } }),
      ],
      legend: [["lg-energy", [{ label: "Energy", color: c2, kind: "line" }, { label: "Storage", color: muted, kind: "line" }, { label: "Converter level (dashed)", color: muted, kind: "line" }]]],
    },
    "ch-mflow": {
      series: [
        lineSeries("Income", d.metalIncome, c1, { markArea: stallMark() }),
        lineSeries("Spent on building", d.metalSpend, c3),
      ],
      legend: [["lg-mflow", [{ label: "Income", color: c1, kind: "line" }, { label: "Spent on building", color: c3, kind: "line" }]]],
    },
    "ch-eflow": {
      series: [
        lineSeries("Production", d.energyIncome, c2, { markArea: stallMark() }),
        lineSeries("Spent on building", d.energySpend, c3),
        lineSeries("Converters", d.converterUse, c4),
      ],
      legend: [["lg-eflow", [{ label: "Production", color: c2, kind: "line" }, { label: "Spent on building", color: c3, kind: "line" }, { label: "Into converters", color: c4, kind: "line" }]]],
    },
    "ch-bp": {
      // Total drawn first in the idle colour, busy on top: the visible grey is the idle part.
      series: [
        lineSeries("Total", d.total, idleColor, { areaStyle: { color: idleColor, opacity: 1 }, lineStyle: { width: 0 }, markArea: stallMark() }),
        lineSeries("Busy", d.busy, c3, { areaStyle: { color: c3, opacity: 1 }, lineStyle: { width: 0 } }),
      ],
      legend: [["lg-bp", [{ label: "Busy", color: c3 }, { label: "Idle (rest of total)", color: idleColor }]]],
      slider: true,
    },
  };

  for (const id of TIMELINE_CHARTS) {
    const o = opts[id];
    const option = baseOption(max);
    option.series = [...o.series, cursorSeries()];
    if (o.slider) {
      option.grid.bottom = 56;
      option.dataZoom.push({ type: "slider", xAxisIndex: 0, filterMode: "none", height: 18, bottom: 6,
        borderColor: tok("--border"), fillerColor: colorMix(c1, 0.15), handleStyle: { color: tok("--surface-raised"), borderColor: tok("--axis") },
        textStyle: { color: muted }, labelFormatter: (v) => clock(v), dataBackground: { lineStyle: { color: tok("--axis") }, areaStyle: { color: tok("--grid") } } });
    }
    option.tooltip.formatter = (p) => tooltipRows(p, id.includes("flow") ? "/s" : "");
    const chart = chartFor(id);
    chart.setOption(option, true);
    chart.group = "timeline";
    for (const [lg, items] of o.legend) legend(lg, items);
    chart.getZr().off("click");
    chart.getZr().on("click", (e) => {
      const pt = [e.offsetX, e.offsetY];
      if (!chart.containPixel("grid", pt)) return;
      setCursor(Math.max(0, Math.min(d.end, chart.convertFromPixel("grid", pt)[0])));
    });
  }
  echarts.connect("timeline");
  renderInspector(run);
  renderSegmentTable(run);
}

function colorMix(hex, alpha) {
  const m = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(m.slice(i, i + 2), 16));
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function setCursor(t) {
  S.cursor = t;
  for (const id of [...TIMELINE_CHARTS, "ch-gantt"]) {
    if (charts[id]) charts[id].setOption({ series: [{ id: "_cursor", markLine: { data: t === null ? [] : [{ xAxis: t }] } }] });
  }
  const run = runById(S.selected);
  if (run && S.tab === "timeline") renderInspector(run);
}

function renderTiles(run) {
  const ev = run.data.evaluation;
  const tile = (label, value, sub, extra = "", status = null) =>
    h("div", { class: `tile ${extra}` }, h("div", { class: "tile-label" }, label),
      h("div", { class: "tile-value" }, value), sub ? h("div", { class: "tile-sub" }, sub) : null, status);
  const stalled = (ev.stall_seconds || 0) > 0.05;
  const overflow = (ev.overflow_metal || 0) > 1 || (ev.overflow_energy || 0) > 50;
  $("#tiles").replaceChildren(
    tile("Goal reached", ev.reached ? clock(ev.finish_time) : "not reached",
      ev.reached ? `${fmt(ev.finish_time, 1)} s` : `stopped at ${clock(run.data.end_time)}`, "tile-hero"),
    tile("Stall", `${fmt(ev.stall_seconds, 1)} s`, `${fmt(ev.stall_bp_seconds)} build-power-seconds lost`, "",
      h("span", { class: `status ${stalled ? "status-critical" : "status-good"}` }, stalled ? "stalled" : "no stall")),
    tile("Overflow", `${fmt(ev.overflow_metal)} M · ${fmt(ev.overflow_energy)} E`, "lost to full storage", "",
      overflow ? h("span", { class: "status status-warning" }, "resources wasted") : h("span", { class: "status status-good" }, "none to speak of")),
    tile("Wasted build power", fmt(ev.wasted_bp_seconds), "idle while resources piled up, build-power-seconds",
      "", h("div", { class: "tile-sub" }, `${fmt(ev.idle_bp_seconds)} idle in total, factories included`)),
    tile("Weighted cost", fmt(ev.cost, 1), "finish time + penalties, lower is better"),
  );
}

/** Interpolated state of a run at time t. */
function stateAt(run, t) {
  const s = run.data.segments;
  const n = s.t0.length;
  let lo = 0, hi = n - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (s.t0[mid] <= t) lo = mid; else hi = mid - 1;
  }
  const i = lo;
  const span = s.t1[i] - s.t0[i];
  const f = span > 0 ? Math.min(1, Math.max(0, (t - s.t0[i]) / span)) : 0;
  return {
    i,
    metal: s.metal0[i] + (s.metal1[i] - s.metal0[i]) * f,
    energy: s.energy0[i] + (s.energy1[i] - s.energy0[i]) * f,
    maxMetal: s.max_metal[i], maxEnergy: s.max_energy[i],
    netMetal: s.net_metal[i], netEnergy: s.net_energy[i],
    metalIncome: s.metal_income[i], energyIncome: s.energy_income[i],
    converterUse: s.converter_use[i],
    speed: s.speed[i], idle: s.idle_bp[i], total: s.total_bp[i],
  };
}

function builderName(run, id) {
  const b = run.data.builders.find((x) => x.id === id);
  return b ? `#${id} ${run.data.units[b.unit]?.display || b.unit}` : `#${id}`;
}

function jobProgressAt(run, job, t) {
  const s = run.data.segments;
  const bpOf = (time) => job.joins.filter(([jt]) => jt <= time + 1e-9)
    .reduce((sum, [, b]) => sum + (run.data.units[run.data.builders.find((x) => x.id === b)?.unit]?.build_power || 0), 0);
  let work = 0;
  for (let i = 0; i < s.t0.length; i++) {
    const a = Math.max(s.t0[i], job.start), b = Math.min(s.t1[i], t);
    if (b <= a) continue;
    work += s.speed[i] * bpOf(a) * (b - a);
  }
  return Math.min(1, work / run.data.units[job.unit].build_time);
}

function renderInspector(run) {
  const box = $("#inspector");
  const t = S.cursor;
  if (t === null) {
    box.replaceChildren(h("h3", {}, "Inspect a moment"),
      h("p", { class: "hint" }, "Click any chart to see the exact state at that time: resources, income, what each builder is doing and what you own."));
    return;
  }
  const st = stateAt(run, t);
  const active = run.data.jobs.filter((j) => j.start <= t && (j.end === null || j.end > t));
  const busyBuilders = new Set(active.flatMap((j) => j.joins.filter(([jt]) => jt <= t).map(([, b]) => b)));
  const idle = run.data.builders.filter((b) => b.spawn <= t && !busyBuilders.has(b.id));
  const owned = {};
  for (const [et, unit, delta] of run.data.unit_events) if (et <= t) owned[unit] = (owned[unit] || 0) + delta;

  const signed = (v) => `${v >= 0 ? "+" : ""}${fmt(v, 1)}/s`;
  box.replaceChildren(
    h("h3", {}, `At ${clock(t)} `, h("span", { class: "hint" }, `${fmt(t, 1)} s`)),
    h("dl", {},
      h("dt", {}, "Metal"), h("dd", {}, `${fmt(st.metal)} / ${fmt(st.maxMetal)}`),
      h("dt", {}, "Net metal"), h("dd", {}, signed(st.netMetal)),
      h("dt", {}, "Metal income"), h("dd", {}, `${fmt(st.metalIncome, 1)}/s`),
      h("dt", {}, "Energy"), h("dd", {}, `${fmt(st.energy)} / ${fmt(st.maxEnergy)}`),
      h("dt", {}, "Net energy"), h("dd", {}, signed(st.netEnergy)),
      h("dt", {}, "Energy production"), h("dd", {}, `${fmt(st.energyIncome, 1)}/s`),
      h("dt", {}, "Into converters"), h("dd", {}, `${fmt(st.converterUse, 1)}/s`),
      h("dt", {}, "Build speed"), h("dd", {},
        st.speed < 1 - 1e-9 ? h("span", { class: "status status-critical" }, `stalled, ${fmt(st.speed * 100)}%`)
          : h("span", { class: "status status-good" }, "100%")),
      h("dt", {}, "Build power"), h("dd", {}, `${fmt(st.total - st.idle)} busy · ${fmt(st.idle)} idle`)),
    h("h4", {}, `Being built (${active.length})`),
    active.length ? h("ul", {}, ...active.map((j) => {
      const p = jobProgressAt(run, j, t);
      return h("li", { class: "job" },
        h("div", {}, h("strong", {}, run.data.units[j.unit]?.display || j.unit), ` ${fmt(p * 100)}%`),
        h("div", { class: "hint" }, j.joins.filter(([jt]) => jt <= t).map(([, b]) => builderName(run, b)).join(", ")),
        h("div", { class: "bar" }, h("span", { style: `width:${p * 100}%` })));
    })) : h("p", { class: "hint" }, "Nothing."),
    h("h4", {}, `Idle builders (${idle.length})`),
    idle.length ? h("div", { class: "chips" }, ...idle.map((b) => h("span", { class: "chip" }, builderName(run, b.id))))
      : h("p", { class: "hint" }, "None."),
    h("h4", {}, "Owned"),
    h("div", { class: "chips" }, ...Object.entries(owned).filter(([, c]) => c > 0)
      .map(([u, c]) => h("span", { class: "chip" }, `${c}× ${run.data.units[u]?.display || u}`))),
  );
}

function renderSegmentTable(run) {
  const s = run.data.segments;
  const rows = [];
  for (let i = 0; i < s.t0.length && rows.length < 3000; i++) {
    if (s.t1[i] - s.t0[i] < 0.05 && s.t0.length > 1) continue;
    rows.push(h("tr", {},
      h("td", {}, `${fmt(s.t0[i], 1)}–${fmt(s.t1[i], 1)}`),
      h("td", {}, fmt(s.metal1[i])), h("td", {}, fmt(s.energy1[i])),
      h("td", {}, fmt(s.metal_income[i], 2)), h("td", {}, fmt(s.metal_spend[i], 2)),
      h("td", {}, fmt(s.energy_income[i], 1)), h("td", {}, fmt(s.energy_spend[i], 1)), h("td", {}, fmt(s.converter_use[i], 1)),
      h("td", {}, `${fmt(s.speed[i] * 100)}%`), h("td", {}, fmt(s.total_bp[i] - s.idle_bp[i])), h("td", {}, fmt(s.idle_bp[i]))));
  }
  $("#segment-table").replaceChildren(h("table", {},
    h("thead", {}, h("tr", {}, ...["Time (s)", "Metal", "Energy", "Metal in /s", "Metal spent /s", "Energy in /s",
      "Energy spent /s", "Converters /s", "Build speed", "Busy BP", "Idle BP"].map((c) => h("th", {}, c)))),
    h("tbody", {}, ...rows)));
}

/* ====================================================================== builders tab */

function ganttData(run) {
  const builders = run.data.builders;
  const row = Object.fromEntries(builders.map((b, i) => [b.id, i]));
  const end = run.data.end_time;
  const work = [], stalls = [], idle = [];
  const busy = Object.fromEntries(builders.map((b) => [b.id, []]));
  for (const job of run.data.jobs) {
    const jobEnd = job.end ?? end;
    job.joins.forEach(([t, b], k) => {
      work.push([row[b], t, jobEnd, k === 0 ? 0 : 1, job.id, job.unit]);
      busy[b].push([t, jobEnd]);
      for (const [a, z] of job.stalls) {
        const s0 = Math.max(a, t), s1 = Math.min(z, jobEnd);
        if (s1 > s0) stalls.push([row[b], s0, s1]);
      }
    });
  }
  for (const b of builders) {
    let cursor = b.spawn;
    for (const [a, z] of busy[b.id].sort((x, y) => x[0] - y[0])) {
      if (a - cursor > 0.05) idle.push([row[b.id], cursor, a]);
      cursor = Math.max(cursor, z);
    }
    if (end - cursor > 0.05) idle.push([row[b.id], cursor, end]);
  }
  return { work, stalls, idle, labels: builders.map((b) => builderName(run, b.id)) };
}

function renderBuilders(run) {
  $("#builders-title").replaceChildren(run.label, h("span", { class: "hint" },
    "Click a bar or a step to follow one job; the timeline cursor follows."));
  const g = ganttData(run);
  const c1 = tok("--series-1"), c3 = tok("--series-3"), idleColor = tok("--idle"), critical = tok("--critical");
  const ink = tok("--text-primary"), onFill = "#ffffff";
  const dom = $("#ch-gantt");
  dom.style.height = `${Math.max(180, g.labels.length * 32 + 90)}px`;
  const chart = chartFor("ch-gantt");
  chart.resize();

  const clip = (params, x0, x1, y, frac) => {
    const a = params.coordSys;
    const start = params.api.coord([x0, y]);
    const stop = params.api.coord([x1, y]);
    const height = params.api.size([0, 1])[1] * frac;
    return echarts.graphic.clipRectByRect(
      { x: start[0], y: start[1] - height / 2, width: Math.max(stop[0] - start[0], 1), height },
      { x: a.x, y: a.y, width: a.width, height: a.height });
  };
  const custom = (name, data, render, extra = {}) => ({
    name, type: "custom", data, encode: { x: [1, 2], y: 0 }, renderItem: render, ...extra,
  });

  const option = baseOption(Math.ceil(run.data.end_time));
  option.grid = { left: 150, right: 18, top: 10, bottom: 56 };
  option.yAxis = { type: "category", data: g.labels, inverse: true, ...axisStyle(), splitLine: { show: false },
    axisLabel: { color: tok("--text-secondary"), fontSize: 12 } };
  option.tooltip = { ...option.tooltip, trigger: "item", formatter: (p) => ganttTooltip(run, p) };
  option.dataZoom = [
    { type: "inside", xAxisIndex: 0, filterMode: "weakFilter", zoomOnMouseWheel: "ctrl", moveOnMouseMove: false },
    { type: "slider", xAxisIndex: 0, filterMode: "weakFilter", height: 18, bottom: 6, borderColor: tok("--border"),
      fillerColor: colorMix(c1, 0.15), textStyle: { color: tok("--text-muted") }, labelFormatter: (v) => clock(v),
      handleStyle: { color: tok("--surface-raised"), borderColor: tok("--axis") } },
  ];
  option.series = [
    custom("Idle", g.idle, (params, api) => {
      const rect = clip({ api, coordSys: params.coordSys }, api.value(1), api.value(2), api.value(0), 0.22);
      return rect && { type: "rect", shape: { ...rect, r: 2 }, style: { fill: idleColor } };
    }),
    custom("Work", g.work, (params, api) => {
      const rect = clip({ api, coordSys: params.coordSys }, api.value(1), api.value(2), api.value(0), 0.62);
      if (!rect) return null;
      const kind = api.value(3);
      const selected = api.value(4) === S.selectedJob;
      const fill = kind === 0 ? c1 : c3;
      if (rect.width > 4) rect.width -= 2; // surface gap between consecutive jobs
      const children = [{ type: "rect", shape: { ...rect, r: 3 },
        style: { fill, stroke: selected ? ink : null, lineWidth: selected ? 2 : 0 } }];
      const name = run.data.units[g.work[params.dataIndex][5]]?.display || g.work[params.dataIndex][5];
      if (rect.width > name.length * 6.2 + 10) {
        children.push({ type: "text", style: { text: name, x: rect.x + 5, y: rect.y + rect.height / 2,
          verticalAlign: "middle", fill: onFill, font: `11px ${tok("--font")}` }, silent: true });
      }
      return { type: "group", children };
    }),
    custom("Stalled", g.stalls, (params, api) => {
      const a = params.coordSys;
      const start = api.coord([api.value(1), api.value(0)]);
      const stop = api.coord([api.value(2), api.value(0)]);
      const bar = api.size([0, 1])[1] * 0.62;
      const rect = echarts.graphic.clipRectByRect(
        { x: start[0], y: start[1] + bar / 2 - 3, width: Math.max(stop[0] - start[0], 1), height: 3 },
        { x: a.x, y: a.y, width: a.width, height: a.height });
      return rect && { type: "rect", shape: rect, style: { fill: critical } };
    }),
    cursorSeries(),
  ];
  chart.setOption(option, true);
  chart.off("click");
  chart.on("click", (p) => {
    if (p.seriesName === "Work") selectJob(g.work[p.dataIndex][4], g.work[p.dataIndex][1]);
  });
  chart.getZr().off("click");
  chart.getZr().on("click", (e) => {
    if (e.target) return;
    const pt = [e.offsetX, e.offsetY];
    if (chart.containPixel("grid", pt)) setCursor(Math.max(0, chart.convertFromPixel("grid", pt)[0]));
  });
  legend("lg-gantt", [
    { label: "Started the job", color: c1 }, { label: "Assisting", color: c3 },
    { label: "Stalled (thin red underline)", color: critical }, { label: "Idle", color: idleColor },
  ]);
  renderSteps(run);
}

function ganttTooltip(run, p) {
  const d = p.data;
  if (p.seriesName === "Idle") return `Idle for ${fmt(d[2] - d[1], 1)} s<br>${clock(d[1])} → ${clock(d[2])}`;
  if (p.seriesName === "Stalled") return `Stalled (resources at 0) for ${fmt(d[2] - d[1], 1)} s`;
  if (p.seriesName !== "Work") return "";
  const job = run.data.jobs.find((j) => j.id === d[4]);
  const unit = run.data.units[d[5]];
  const builders = job.joins.map(([t, b]) => `${builderName(run, b)} from ${clock(t)}`).join("<br>");
  return `<strong>${unit?.display || d[5]}</strong> <span style="color:${tok("--text-muted")}">${d[5]}</span><br>` +
    `${d[3] === 0 ? "Started" : "Assisted"} ${clock(d[1])} → ${clock(d[2])} (${fmt(d[2] - d[1], 1)} s)<br>` +
    `<span style="color:${tok("--text-secondary")}">${builders}</span>`;
}

function renderSteps(run) {
  const filter = $("#steps-filter");
  filter.replaceChildren(h("option", { value: "" }, "All builders"),
    ...run.data.builders.map((b) => h("option", { value: String(b.id) }, builderName(run, b.id))));
  filter.value = S.stepsFilter;
  const steps = run.data.steps.filter((s) => S.stepsFilter === "" || String(s.builder) === S.stepsFilter);
  $("#steps").replaceChildren(...steps.map((s) =>
    h("li", { tabindex: 0, "data-job": s.job, "aria-current": String(s.job === S.selectedJob),
      onclick: () => selectJob(s.job, s.t),
      onkeydown: (e) => { if (e.key === "Enter") selectJob(s.job, s.t); } },
      h("span", { class: "t" }, clock(s.t)),
      h("span", { class: s.action === "assist" ? "assist" : "" },
        `${builderName(run, s.builder)} ${s.action === "start" ? "starts" : "assists"} `,
        h("strong", {}, run.data.units[s.unit]?.display || s.unit)))));
}

function selectJob(jobId, t) {
  S.selectedJob = jobId;
  const run = runById(S.selected);
  if (!run) return;
  $$("#steps li").forEach((li) => li.setAttribute("aria-current", String(Number(li.dataset.job) === jobId)));
  $(`#steps li[data-job="${jobId}"]`)?.scrollIntoView({ block: "nearest" });
  renderBuildersChartOnly(run);
  setCursor(t);
}

function renderBuildersChartOnly(run) {
  // The selected-job ring is drawn in renderItem, so re-render the chart keeping its zoom.
  const chart = charts["ch-gantt"];
  if (!chart) return;
  const zoom = chart.getOption().dataZoom?.[0];
  renderBuilders(run);
  if (zoom) chart.dispatchAction({ type: "dataZoom", start: zoom.start, end: zoom.end });
}

function stepsText(run) {
  return run.data.steps.map((s) =>
    `${clock(s.t).padStart(7)}  ${builderName(run, s.builder)}: ${s.action === "start" ? "start" : "assist"} ` +
    `${run.data.units[s.unit]?.display || s.unit}`).join("\n");
}

/* ====================================================================== compare tab */

const COMPARE_ROWS = [
  ["Goal reached", (ev) => ev.finish_time, (v) => (v === null ? "not reached" : `${clock(v)} (${fmt(v, 1)} s)`)],
  ["Stall (seconds)", (ev) => ev.stall_seconds, (v) => fmt(v, 1)],
  ["Stall (lost build-power-seconds)", (ev) => ev.stall_bp_seconds, (v) => fmt(v)],
  ["Overflow metal", (ev) => ev.overflow_metal, (v) => fmt(v)],
  ["Overflow energy", (ev) => ev.overflow_energy, (v) => fmt(v)],
  ["Wasted build power (BP-s)", (ev) => ev.wasted_bp_seconds, (v) => fmt(v)],
  ["Idle build power, all (BP-s)", (ev) => ev.idle_bp_seconds, (v) => fmt(v)],
  ["Weighted cost", (ev) => ev.cost, (v) => fmt(v, 1)],
];

function goalProgress(run) {
  const targets = run.data.goal.targets;
  const value = (u) => (run.data.units[u]?.metal || 0) + (run.data.units[u]?.energy || 0) / 70;
  const total = Object.entries(targets).reduce((s, [u, n]) => s + n * value(u), 0) || 1;
  const counts = {};
  const out = [[0, 0]];
  let done = 0;
  for (const [t, u, delta] of run.data.unit_events) {
    if (!(u in targets)) continue;
    const before = Math.min(counts[u] || 0, targets[u]);
    counts[u] = (counts[u] || 0) + delta;
    const after = Math.min(Math.max(counts[u], 0), targets[u]);
    if (after === before) continue;
    out.push([t, (done / total) * 100]);
    done += (after - before) * value(u);
    out.push([t, (done / total) * 100]);
  }
  out.push([run.data.end_time, (done / total) * 100]);
  return out;
}

function renderCompare() {
  const runs = S.compare.map(runById).filter(Boolean);
  $("#compare-empty").hidden = runs.length >= 2;
  $("#compare-view").hidden = runs.length < 2;
  if (runs.length < 2) return;
  const color = (run) => tok(COMPARE_SLOTS[run.slot]);
  legend("lg-compare", runs.map((r) => ({ label: r.label, color: color(r), kind: "line" })));

  const header = h("tr", {}, h("th", {}, "Metric"), ...runs.map((r) =>
    h("th", {}, h("span", { class: "swatch-line", style: `background:${color(r)}` }), " ", r.label)));
  const body = [];
  const addRow = (label, values, format) => {
    const finite = values.filter((v) => v !== null && Number.isFinite(v));
    const best = finite.length > 1 ? Math.min(...finite) : null;
    const allEqual = finite.length > 1 && finite.every((v) => Math.abs(v - finite[0]) < 1e-6);
    body.push(h("tr", {}, h("td", {}, label), ...values.map((v) => {
      const isBest = best !== null && !allEqual && v !== null && Math.abs(v - best) < 1e-6;
      return h("td", { class: isBest ? "best" : "" }, format(v), isBest ? h("span", { class: "best-mark" }, "✓ best") : null);
    })));
  };
  for (const [label, get, format] of COMPARE_ROWS) addRow(label, runs.map((r) => get(r.data.evaluation)), format);
  const targetUnits = [...new Set(runs.flatMap((r) => Object.keys(r.data.goal.targets)))];
  for (const u of targetUnits) {
    addRow(`${unitLabel(u)} done at`, runs.map((r) => (u in r.data.target_times ? r.data.target_times[u] : null)),
      (v) => (v === null ? "—" : clock(v)));
  }
  $("#compare-table").replaceChildren(h("thead", {}, header), h("tbody", {}, ...body));

  const max = Math.ceil(Math.max(...runs.map((r) => r.data.end_time)));
  const finishLines = (run) => ({
    symbol: "none", silent: true, label: { show: false }, lineStyle: { color: color(run), width: 1.5, type: "solid" },
    data: run.data.evaluation.reached ? [{ xAxis: run.data.evaluation.finish_time }] : [],
  });
  const charts4 = {
    "cmp-metal": (r) => derived(r).metal,
    "cmp-energy": (r) => derived(r).energy,
    "cmp-bp": (r) => derived(r).busy,
    "cmp-progress": (r) => goalProgress(r),
  };
  for (const [id, pick] of Object.entries(charts4)) {
    const option = baseOption(max);
    option.series = runs.map((r) => lineSeries(r.label, pick(r), color(r), { markLine: finishLines(r) }));
    option.tooltip.formatter = (p) => tooltipRows(p, id === "cmp-progress" ? "%" : "");
    if (id === "cmp-progress") option.yAxis.max = 100;
    const chart = chartFor(id);
    chart.setOption(option, true);
    chart.group = "compare";
  }
  echarts.connect("compare");
}

/* ====================================================================== recipe editor */

function editorSteps() {
  return S.recipe.map((s) => ({ name: `${unitLabel(s.unit)} - ${s.unit}`, repeat: s.repeat, builders: [...s.builders] }));
}

function stepsFromYaml(content) {
  return (content || []).map((step) => ({
    unit: String(step.name || "").split(" - ").at(-1).trim(),
    repeat: Number(step.repeat) || 1,
    builders: [...(step.builders || [])].map(String),
  }));
}

function builderChoices(unit) {
  const u = S.units[unit];
  const can = (u?.built_by || []).slice().sort();
  const assist = S.catalog.filter((c) => c.can_assist && !can.includes(c.name)).map((c) => c.name);
  return { can, assist };
}

function renderRecipe() {
  const body = $("#recipe-rows");
  body.replaceChildren();
  if (!S.recipe.length) {
    body.append(h("tr", {}, h("td", { colspan: 5, class: "hint" }, "No steps yet. Add one below or load a recipe file.")));
  }
  S.recipe.forEach((step, i) => {
    const known = !!S.units[step.unit];
    const { can, assist } = builderChoices(step.unit);
    // Replay rule: the first listed builder able to build the unit starts it, the rest assist.
    const owner = step.builders.find((b) => can.includes(b));
    const chips = step.builders.map((b, k) => {
      const ok = b === owner || (owner !== undefined && (can.includes(b) || S.units[b]?.can_assist));
      const why = owner === undefined ? `none of the listed builders can build ${unitLabel(step.unit)}`
        : `${unitLabel(b)} cannot assist`;
      return h("span", { class: `builder-chip ${ok ? "" : "invalid"}`, title: ok ? "" : why },
        unitLabel(b), h("button", { type: "button", "aria-label": `Remove builder ${unitLabel(b)}`,
          onclick: () => { step.builders.splice(k, 1); renderRecipe(); } }, "✕"));
    });
    const add = h("select", { "aria-label": `Add builder to step ${i + 1}`,
      onchange: (e) => { if (e.target.value) { step.builders.push(e.target.value); renderRecipe(); } } },
      h("option", { value: "" }, "+ builder"),
      can.length ? h("optgroup", { label: "Can build it" }, ...can.map((n) => h("option", { value: n }, `${unitLabel(n)} (${n})`))) : null,
      h("optgroup", { label: "Can assist" }, ...assist.map((n) => h("option", { value: n }, `${unitLabel(n)} (${n})`))));
    const move = (delta) => {
      const j = i + delta;
      if (j < 0 || j >= S.recipe.length) return;
      [S.recipe[i], S.recipe[j]] = [S.recipe[j], S.recipe[i]];
      renderRecipe();
    };
    body.append(h("tr", {},
      h("td", { class: "num" }, i + 1),
      h("td", {}, known ? unitLabel(step.unit) : h("span", { class: "status status-critical" }, "unknown unit"),
        h("span", { class: "unit-code" }, step.unit)),
      h("td", {}, h("input", { type: "number", min: 1, step: 1, value: step.repeat, "aria-label": `Repeat step ${i + 1}`,
        oninput: (e) => (step.repeat = Math.max(1, Math.round(Number(e.target.value) || 1))) })),
      h("td", {}, h("div", { class: "builders" }, ...chips, add)),
      h("td", {}, h("div", { class: "row-actions" },
        h("button", { type: "button", class: "icon", "aria-label": `Move step ${i + 1} up`, disabled: i === 0, onclick: () => move(-1) }, "↑"),
        h("button", { type: "button", class: "icon", "aria-label": `Move step ${i + 1} down`, disabled: i === S.recipe.length - 1, onclick: () => move(1) }, "↓"),
        h("button", { type: "button", class: "icon", "aria-label": `Delete step ${i + 1}`,
          onclick: () => { S.recipe.splice(i, 1); renderRecipe(); } }, "✕")))));
  });
}

function addRecipeStep(unit) {
  const { can } = builderChoices(unit);
  const prev = S.recipe.at(-1);
  if (prev && prev.unit === unit) {
    prev.repeat += 1;
  } else {
    const builder = can.includes("corcom") ? "corcom" : can[0];
    S.recipe.push({ unit, repeat: 1, builders: builder ? [builder] : [] });
  }
  renderRecipe();
}

function recipeMessages(items) {
  $("#recipe-messages").replaceChildren(...items.map(([kind, text]) => h("div", { class: `message ${kind}` }, text)));
}

async function scoreRecipe() {
  if (!S.recipe.length) return toast("The recipe has no steps.", true);
  const goal = readGoal();
  if (!Object.keys(goal.targets).length) {
    return recipeMessages([["error", "Set a goal first (or use “Use the recipe editor's end state as targets” on the left)."]]);
  }
  try {
    const name = $("#recipe-file").value;
    const data = await api("/api/evaluate", { method: "POST",
      body: { goal, recipe: editorSteps(), label: `Hand: ${S.recipeName || name || "recipe editor"}` } });
    addRun(data);
    const ev = data.evaluation;
    recipeMessages([
      ...data.problems.map((p) => ["error", p]),
      ev.reached ? ["ok", `Reached the goal at ${clock(ev.finish_time)} with ${fmt(ev.stall_seconds, 1)} s of stall; weighted cost ${fmt(ev.cost, 1)}. Added to Runs.`] : null,
    ].filter(Boolean));
  } catch (e) {
    recipeMessages([["error", e.message]]);
  }
}

async function goalFromRecipe() {
  if (!S.recipe.length) return toast("The recipe editor is empty.", true);
  try {
    const res = await api("/api/recipe/end-state", { method: "POST", body: { recipe: editorSteps() } });
    if (res.problems.length) toast(res.problems[0], true);
    S.targets = Object.entries(res.targets).map(([unit, count]) => ({ unit, count }));
    if (res.mex_spots_needed > sliders.config.mex_spots.get()) sliders.config.mex_spots.set(res.mex_spots_needed);
    renderTargets();
    toast(`Targets set to the recipe's end state (${S.targets.length} unit types).`);
  } catch (e) { toast(e.message, true); }
}

async function loadRecipeFile() {
  const name = $("#recipe-file").value;
  if (!name) return;
  try {
    const { content } = await api(`/api/files/recipes/${encodeURIComponent(name)}`);
    S.recipe = stepsFromYaml(content);
    S.recipeName = name;
    renderRecipe();
    recipeMessages([]);
    toast(`Loaded recipes/${name}`);
  } catch (e) { toast(e.message, true); }
}

/* ====================================================================== in-game guide */

function renderIngame() {
  const run = runById(S.selected);
  $("#guide-plan").disabled = !run;
  $("#guide-install").disabled = !run || !S.barData;
  $("#guide-run").textContent = run
    ? `Plan from “${run.label}”: ${run.data.jobs.length} steps, goal reached at ${clock(run.data.evaluation.finish_time)} in the model.`
    : "Select a run above to export its plan.";
  $("#guide-compare").hidden = !S.gameLog;
  if (S.gameLog && typeof echarts !== "undefined") renderGuideCompare(S.gameLog);
}

async function refreshGameLogs() {
  try {
    const { data_dir: dir, logs } = await api("/api/guide/logs");
    S.barData = dir;
    $("#guide-folder").textContent = dir ? `BAR data folder: ${dir}` : "BAR data folder not found. Set BAR_DATA_DIR before starting app.py, or download the files.";
    $("#guide-logs").replaceChildren(...(logs.length ? logs : [null]).map((log) => h("li", {}, log
      ? h("button", { type: "button", "aria-pressed": String(S.gameLogName === log.name), onclick: () => openGameLog(log.name) },
        h("span", {}, log.name), h("span", { class: "hint" }, new Date(log.modified * 1000).toLocaleString()))
      : h("span", { class: "hint" }, dir ? "No game logs yet. Play a game with the guide on." : ""))));
    renderIngame();
  } catch (e) { toast(e.message, true); }
}

async function openGameLog(name) {
  try {
    S.gameLog = await api(`/api/guide/logs/${encodeURIComponent(name)}`);
    S.gameLogName = name;
    $$("#guide-logs button").forEach((b) => b.setAttribute("aria-pressed", String(b.firstChild.textContent === name)));
    renderIngame();
  } catch (e) { toast(e.message, true); }
}

async function installGuidePlan() {
  const run = runById(S.selected);
  if (!run) return;
  try {
    const res = await api("/api/guide/install", { method: "POST", body: { run: run.data } });
    toast(`Sent “${run.label}” (${res.steps} steps) to BAR. Enable Build Order Guide in the widget list (F11).`);
  } catch (e) { toast(e.message, true); }
}

async function downloadGuidePlan() {
  const run = runById(S.selected);
  if (!run) return;
  try {
    const plan = await api("/api/guide/plan", { method: "POST", body: { run: run.data } });
    download("build_guide_plan.json", JSON.stringify(plan), "application/json");
    toast("Saved build_guide_plan.json. Put it in LuaUI/Config/ in your BAR data folder.");
  } catch (e) { toast(e.message, true); }
}

function loadGameLog(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const log = JSON.parse(reader.result);
      if (log.format !== "bar-build-guide-log" || !Array.isArray(log.steps)) throw new Error("not a build guide log");
      S.gameLog = log;
      S.gameLogName = null;
      renderIngame();
      toast(`Loaded ${file.name}`);
    } catch (e) { toast(`${file.name}: ${e.message}`, true); }
  };
  reader.readAsText(file);
}

const isTime = (v) => v !== null && v !== undefined;
const delta = (a, b) => (isTime(a) && isTime(b) ? a - b : null);
const fmtDelta = (d) => (d === null ? "—" : `${d > 0 ? "+" : ""}${fmt(d, 1)} s`);
const deltaClass = (d) => (d === null ? "" : d >= 2 ? "late" : d <= -2 ? "early" : "");

function renderGuideCompare(log) {
  const steps = log.steps;
  const started = steps.filter((s) => isTime(s.actual_start));
  const finished = steps.filter((s) => isTime(s.actual_finish));
  const planEnd = Math.max(0, ...steps.map((s) => s.planned_finish || 0));
  const gameEnd = finished.length === steps.length ? Math.max(0, ...finished.map((s) => s.actual_finish)) : null;
  const delays = started.map((s) => s.actual_start - s.planned_start);
  const worst = started.reduce((w, s) => (!w || s.actual_start - s.planned_start > w.actual_start - w.planned_start ? s : w), null);
  const extra = Array.isArray(log.extra) ? log.extra : [];

  $("#guide-title").replaceChildren(`${log.plan_name || "Build guide"}`,
    h("span", { class: "hint" }, [log.map, log.recorded].filter(Boolean).join(" · ")));
  const tile = (label, value, sub) => h("div", { class: "tile" }, h("div", { class: "tile-label" }, label),
    h("div", { class: "tile-value" }, value), sub ? h("div", { class: "tile-sub" }, sub) : null);
  $("#guide-tiles").replaceChildren(
    tile("Build done", gameEnd === null ? "not finished" : clock(gameEnd),
      gameEnd === null ? `${finished.length} of ${steps.length} steps finished` : `plan ${clock(planEnd)} · ${fmtDelta(gameEnd - planEnd)}`),
    tile("Steps started", `${started.length} / ${steps.length}`,
      extra.length ? `${extra.length} ${extra.length === 1 ? "unit" : "units"} built that ${extra.length === 1 ? "is" : "are"} not in the plan`
        : "no units outside the plan"),
    tile("Average start delay", delays.length ? fmtDelta(delays.reduce((a, b) => a + b, 0) / delays.length) : "—",
      "game start − planned start"),
    tile("Biggest delay", worst ? fmtDelta(worst.actual_start - worst.planned_start) : "—",
      worst ? `step ${worst.n}: ${worst.display}` : ""),
  );

  const c1 = tok("--series-1"), c2 = tok("--series-2");
  const column = (rows, k) => (rows || []).map((r) => [r[0], r[k]]);
  const maxT = Math.ceil(Math.max(planEnd, ...(log.resources || []).map((r) => r[0]), 1));
  for (const [id, k] of Object.entries({ "guide-metal": 1, "guide-energy": 2, "guide-mincome": 3 })) {
    const option = baseOption(maxT);
    option.series = [lineSeries("Plan", column(log.plan_resources, k), c1), lineSeries("Game", column(log.resources, k), c2)];
    option.tooltip.formatter = (p) => tooltipRows(p);
    const chart = chartFor(id);
    chart.setOption(option, true);
    chart.group = "guide";
    legend(`lg-${id}`, [{ label: "Plan", color: c1, kind: "line" }, { label: "Game", color: c2, kind: "line" }]);
  }
  const option = baseOption(maxT);
  option.series = [lineSeries("Delay", started.map((s) => [s.planned_start, s.actual_start - s.planned_start]), c2,
    { showSymbol: true, symbolSize: 5 })];
  option.tooltip = { ...option.tooltip, trigger: "item", formatter: (p) => {
    const s = started[p.dataIndex];
    return `Step ${s.n}: <strong>${s.display}</strong><br>planned ${clock(s.planned_start)}, game ${clock(s.actual_start)} (${fmtDelta(p.value[1])})`;
  } };
  const chart = chartFor("guide-delay");
  chart.setOption(option, true);
  chart.group = "guide";
  echarts.connect("guide");

  const head = h("tr", {}, ...["#", "Unit", "Builder", "Plan start", "Game start", "Δ start", "Plan done", "Game done", "Δ done"]
    .map((t, i) => h("th", { class: i === 1 || i === 2 ? "text" : "" }, t)));
  const rows = steps.map((s) => {
    const ds = delta(s.actual_start, s.planned_start), df = delta(s.actual_finish, s.planned_finish);
    return h("tr", {}, h("td", {}, s.n), h("td", { class: "text" }, s.display), h("td", { class: "text" }, s.builder),
      h("td", {}, clock(s.planned_start)), h("td", {}, clock(s.actual_start ?? null)), h("td", { class: deltaClass(ds) }, fmtDelta(ds)),
      h("td", {}, clock(s.planned_finish)), h("td", {}, clock(s.actual_finish ?? null)), h("td", { class: deltaClass(df) }, fmtDelta(df)));
  });
  $("#guide-table").replaceChildren(h("thead", {}, head), h("tbody", {}, ...rows));
}

/* ====================================================================== tabs, theme, init */

const TABS = ["timeline", "builders", "compare", "recipe", "ingame"];

function setTab(tab) {
  S.tab = tab;
  for (const name of TABS) {
    $(`#tab-${name}`).hidden = name !== tab;
    $(`#tabbtn-${name}`).setAttribute("aria-selected", String(name === tab));
  }
  renderTab();
}

function renderTab() {
  const run = runById(S.selected);
  for (const panel of ["timeline", "builders"]) {
    $(`#tab-${panel} [data-needs-run]`).hidden = !!run;
    $(`#tab-${panel} [data-run-view]`).hidden = !run;
  }
  if (typeof echarts === "undefined") {
    if (S.tab !== "recipe" && run) toast("The chart library (web/vendor/echarts.min.js) did not load.", true);
    if (S.tab === "recipe") renderRecipe();
    if (S.tab === "ingame") renderIngame();
    return;
  }
  if (S.tab === "timeline" && run) renderTimeline(run);
  else if (S.tab === "builders" && run) renderBuilders(run);
  else if (S.tab === "compare") renderCompare();
  else if (S.tab === "recipe") renderRecipe();
  else if (S.tab === "ingame") renderIngame();
}

function applyTheme(theme) {
  if (theme) document.documentElement.dataset.theme = theme;
  const dark = theme ? theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  $("#theme-toggle").textContent = dark ? "Light theme" : "Dark theme";
  if (typeof echarts !== "undefined") {
    disposeCharts();
    renderTab();
  }
}

function wire() {
  for (const name of TABS) {
    $(`#tabbtn-${name}`).addEventListener("click", () => setTab(name));
  }
  $(".tabs").addEventListener("keydown", (e) => {
    const order = TABS;
    const i = order.indexOf(S.tab);
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      const next = order[(i + (e.key === "ArrowRight" ? 1 : order.length - 1)) % order.length];
      setTab(next);
      $(`#tabbtn-${next}`).focus();
    }
  });
  $("#theme-toggle").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    storageSet("bar-opt-theme", next);
    applyTheme(next);
  });
  $("#goal-load").addEventListener("click", loadGoalFile);
  $("#goal-save").addEventListener("click", saveGoalFile);
  $("#goal-from-recipe").addEventListener("click", goalFromRecipe);
  $("#run-btn").addEventListener("click", runOptimize);
  $("#cancel-btn").addEventListener("click", async () => {
    if (!S.job) return;
    $("#cancel-btn").disabled = true;
    try { await api(`/api/jobs/${S.job.id}/cancel`, { method: "POST" }); } catch (e) { toast(e.message, true); }
  });
  makeCombo($("#target-search"), $("#target-results"), addTarget);
  makeCombo($("#step-search"), $("#step-results"), addRecipeStep, (u) => u.built_by.length > 0);

  $("#steps-filter").addEventListener("change", (e) => {
    S.stepsFilter = e.target.value;
    const run = runById(S.selected);
    if (run) renderSteps(run);
  });
  $("#guide-plan").addEventListener("click", downloadGuidePlan);
  $("#guide-install").addEventListener("click", installGuidePlan);
  $("#guide-logs-refresh").addEventListener("click", refreshGameLogs);
  $("#tabbtn-ingame").addEventListener("click", refreshGameLogs);
  $("#guide-log-load").addEventListener("click", () => $("#guide-log-file").click());
  $("#guide-log-file").addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (file) loadGameLog(file);
    e.target.value = "";
  });
  $("#export-yaml").addEventListener("click", () => {
    const run = runById(S.selected);
    if (run) download(`${slug(run.label)}.yaml`, recipeToYaml(run.data.yaml_steps));
  });
  $("#copy-steps").addEventListener("click", async () => {
    const run = runById(S.selected);
    if (!run) return;
    try { await navigator.clipboard.writeText(stepsText(run)); toast("Steps copied."); }
    catch { toast("The browser blocked clipboard access.", true); }
  });
  $("#save-recipe").addEventListener("click", async () => {
    const run = runById(S.selected);
    if (!run) return;
    const name = askFileName("recipes", slug(run.label));
    if (name) { try { await saveFile("recipes", name, run.data.yaml_steps); } catch (e) { toast(e.message, true); } }
  });
  $("#open-in-editor").addEventListener("click", () => {
    const run = runById(S.selected);
    if (!run) return;
    S.recipe = stepsFromYaml(run.data.yaml_steps);
    S.recipeName = null;
    setTab("recipe");
    recipeMessages([["ok", `Loaded the build of “${run.label}”. Note the YAML format keeps order and builder types, not exact timing.`]]);
  });

  $("#recipe-load").addEventListener("click", loadRecipeFile);
  $("#recipe-new").addEventListener("click", () => {
    if (S.recipe.length && !confirm("Clear the recipe editor?")) return;
    S.recipe = [];
    S.recipeName = null;
    renderRecipe();
    recipeMessages([]);
  });
  $("#recipe-save").addEventListener("click", async () => {
    if (!S.recipe.length) return toast("The recipe has no steps.", true);
    const name = askFileName("recipes", S.recipeName ? S.recipeName.replace(/\.ya?ml$/, "") : "my_build");
    if (name) {
      try { if (await saveFile("recipes", name, editorSteps())) S.recipeName = name; } catch (e) { toast(e.message, true); }
    }
  });
  $("#recipe-score").addEventListener("click", scoreRecipe);

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => Object.values(charts).forEach((c) => c.getDom().offsetParent && c.resize()), 120);
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (!document.documentElement.dataset.theme) applyTheme(null);
  });
}

async function init() {
  const saved = storageGet("bar-opt-theme");
  applyTheme(saved === "light" || saved === "dark" ? saved : null);
  wire();
  try {
    const [defaults, catalog] = await Promise.all([api("/api/defaults"), api("/api/catalog")]);
    S.defaults = defaults;
    S.catalog = catalog;
    S.units = Object.fromEntries(catalog.map((u) => [u.name, u]));
    buildForm();
    await refreshFiles();
    $("#data-note").textContent = `${catalog.length} units reachable from ${defaults.config.commander}` +
      (defaults.data_commit ? ` · BAR data ${defaults.data_commit.slice(0, 10)}` : "");
    if ([...$("#goal-file").options].some((o) => o.value === "coralab.yaml")) {
      $("#goal-file").value = "coralab.yaml";
      await loadGoalFile();
    } else {
      renderTargets();
    }
    renderRecipe();
  } catch (e) {
    toast(`Could not reach the optimizer server: ${e.message}`, true);
  }
}

init();
