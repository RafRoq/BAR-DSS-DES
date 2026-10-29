"""Local web UI for the build-order optimizer.

    python app.py              # opens http://127.0.0.1:8765 in your browser
    python app.py --port 9000 --no-browser

Serves web/ and a small JSON API. Listens on 127.0.0.1 only; files can only be read from
and written to goals/*.yaml and recipes/*.yaml.
"""

import argparse
import json
import os
import re
import threading
import time
import traceback
import uuid
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import yaml

from bar_opt.config import EconConfig
from bar_opt.data import DATA_FILE, load_units
from bar_opt.goal import Goal, Weights
from bar_opt.recipe import parse_steps, replay
from bar_opt.search import SearchCancelled, SearchConfig, optimize, replay_ops, search_runs
from bar_opt.serialize import catalog_json, run_json
from bar_opt.sim import SimError

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
FOLDERS = {"goals": os.path.join(ROOT, "goals"), "recipes": os.path.join(ROOT, "recipes")}
FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,80}\.ya?ml$")
STATIC = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
          "/style.css": ("style.css", "text/css"),
          "/vendor/echarts.min.js": ("vendor/echarts.min.js", "text/javascript")}  # ECharts 5.5.1, Apache-2.0
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
DATA_COMMIT = ""


class BadRequest(Exception):
    pass


# ---------------------------------------------------------------- requests -> engine objects

def goal_from(data: dict) -> Goal:
    try:
        goal = Goal.from_dict(data)
    except (KeyError, TypeError, ValueError) as e:
        raise BadRequest(f"invalid goal: {e}")
    if not goal.targets:
        raise BadRequest("the goal has no targets")
    units = load_units()
    unknown = [u for u in goal.targets if u not in units]
    if unknown:
        raise BadRequest(f"unknown units in goal: {unknown}")
    return goal


def search_from(data: dict | None) -> SearchConfig:
    data = dict(data or {})
    if data.get("affordability") in ("both", "", None):
        data["affordability"] = None
    try:
        return SearchConfig.from_dict(data)
    except TypeError as e:
        raise BadRequest(f"invalid search settings: {e}")


def recipe_from(steps) -> list:
    if not isinstance(steps, list) or not steps:
        raise BadRequest("the recipe has no steps")
    try:
        return parse_steps(steps)
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise BadRequest(f"invalid recipe step: {e}")


# ---------------------------------------------------------------- optimization jobs

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def start_job(goal: Goal, search: SearchConfig, label: str) -> dict:
    with JOBS_LOCK:
        if any(j["status"] == "running" for j in JOBS.values()):
            raise BadRequest("an optimization is already running")
        job = {
            "id": uuid.uuid4().hex[:12], "label": label, "status": "running", "cancel": False,
            "started": time.time(), "finished": None, "error": None, "result": None,
            "progress": [{"mode": m, "width": w, "status": "pending", "fraction": 0.0, "best_cost": None,
                          "nodes": 0, "layer": 0} for m, w in search_runs(search)],
        }
        JOBS[job["id"]] = job
    threading.Thread(target=run_job, args=(job, goal, search), daemon=True).start()
    return job


def run_job(job: dict, goal: Goal, search: SearchConfig) -> None:
    def progress(info):
        if job["cancel"]:
            raise SearchCancelled
        with JOBS_LOCK:
            row = job["progress"][info["run"]]
            row["status"] = "done" if info.get("done") else "running"
            for key in ("fraction", "nodes", "layer"):
                if key in info:
                    row[key] = info[key]
            if info.get("done"):
                row["best_cost"] = info.get("cost")
            elif info.get("best_cost") is not None:
                row["best_cost"] = info["best_cost"]

    try:
        result = optimize(goal, search, progress)
        payload = None
        if result:
            state = replay_ops(goal, result.ops)
            payload = run_json(state, goal, job["label"], "optimizer", meta={
                "search": {k: v for k, v in asdict(search).items() if k != "economy_units"},
                "seconds": round(time.time() - job["started"], 1), "cancelled": job["cancel"]})
        with JOBS_LOCK:
            job["result"] = payload
            job["status"] = "cancelled" if job["cancel"] else ("done" if payload else "failed")
            if not payload:
                job["error"] = "no build reached the goal" + ("" if job["cancel"] else f" within {goal.max_time:.0f}s")
    except Exception as e:  # report to the UI instead of dying silently in the thread
        traceback.print_exc()
        with JOBS_LOCK:
            job["status"], job["error"] = "failed", f"{type(e).__name__}: {e}"
    finally:
        job["finished"] = time.time()


def job_view(job: dict, include_result: bool) -> dict:
    with JOBS_LOCK:
        view = {k: job[k] for k in ("id", "label", "status", "error", "progress")}
        view["progress"] = [dict(p) for p in job["progress"]]
        view["elapsed"] = round((job["finished"] or time.time()) - job["started"], 1)
        if include_result:
            view["result"] = job["result"]
    return view


# ---------------------------------------------------------------- API

def data_commit() -> str:
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f).get("source", {}).get("commit", "")


def defaults() -> dict:
    search = SearchConfig()
    return {
        "data_commit": DATA_COMMIT,
        "weights": asdict(Weights()),
        "config": asdict(EconConfig()),
        "search": {"beam_width": search.beam_width, "constructor_cap": search.constructor_cap,
                   "max_delay": search.max_delay, "improve_seconds": search.improve_seconds,
                   "affordability": "both"},
    }


def evaluate_recipe(data: dict) -> dict:
    goal = goal_from(data.get("goal") or {})
    recipe = recipe_from(data.get("recipe"))
    try:
        state, problems = replay(recipe, goal.config, max_time=goal.max_time, stop_when=goal.satisfied)
    except SimError as e:
        raise BadRequest(str(e))
    if not goal.satisfied(state) and not problems:
        problems = [f"goal not reached, missing {goal.missing(state)}"]
    return run_json(state, goal, data.get("label") or "hand-written", "recipe", problems)


def recipe_end_state(data: dict) -> dict:
    recipe = recipe_from(data.get("recipe"))
    config = EconConfig(mex_spots=1000)
    try:
        state, problems = replay(recipe, config, record=False)
    except SimError as e:
        raise BadRequest(str(e))
    targets = {u: n for u, n in state.counts.items() if n > 0 and u != config.commander}
    mexes = sum(n for u, n in targets.items() if state.units[u].is_mex)
    return {"targets": targets, "mex_spots_needed": mexes, "end_time": round(state.time, 1), "problems": problems}


def list_files() -> dict:
    return {name: sorted(f for f in os.listdir(path) if FILE_NAME.match(f))
            for name, path in FOLDERS.items() if os.path.isdir(path)}


def file_path(folder: str, name: str) -> str:
    if folder not in FOLDERS or not FILE_NAME.match(name):
        raise BadRequest("files must be goals/<name>.yaml or recipes/<name>.yaml")
    return os.path.join(FOLDERS[folder], name)


class Handler(BaseHTTPRequestHandler):
    server_version = "BAROptimizer/1.0"

    def log_message(self, fmt, *args):
        if not self.path.startswith("/api/jobs/"):  # polled several times a second
            super().log_message(fmt, *args)

    # -------- plumbing

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status: int = 200) -> None:
        self._send(status, json.dumps(data).encode("utf-8"), "application/json")

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as e:
            raise BadRequest(f"invalid JSON: {e}")

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ALLOWED_HOSTS

    def _dispatch(self, method: str) -> None:
        if not self._host_ok():
            self._json({"error": "forbidden host"}, 403)
            return
        path = unquote(urlparse(self.path).path)
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        try:
            if method == "GET" and path in STATIC:
                name, ctype = STATIC[path]
                with open(os.path.join(WEB, name), "rb") as f:
                    self._send(200, f.read(), ctype)
                return
            self._json(self._api(method, path))
        except BadRequest as e:
            self._json({"error": str(e)}, 400)
        except FileNotFoundError as e:
            self._json({"error": f"not found: {os.path.basename(str(e.filename or path))}"}, 404)
        except Exception as e:
            traceback.print_exc()
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    # -------- routes

    def _api(self, method: str, path: str):
        parts = [p for p in path.split("/") if p]
        if parts[:1] != ["api"]:
            raise FileNotFoundError(path)
        route = parts[1:]

        if method == "GET" and route == ["defaults"]:
            return defaults()
        if method == "GET" and route == ["catalog"]:
            cfg = EconConfig()
            return catalog_json(load_units(), cfg.commander, cfg.wind_average, cfg.tidal_strength, cfg.metal_per_spot)
        if method == "GET" and route == ["files"]:
            return list_files()
        if len(route) == 3 and route[0] == "files":
            path = file_path(route[1], route[2])
            if method == "GET":
                with open(path, encoding="utf-8") as f:
                    return {"name": route[2], "content": yaml.safe_load(f)}
            if method == "PUT":
                content = self._body().get("content")
                if route[1] == "recipes" and not isinstance(content, list):
                    raise BadRequest("a recipe is a list of steps")
                if route[1] == "goals" and not isinstance(content, dict):
                    raise BadRequest("a goal is a mapping")
                with open(path, "w", encoding="utf-8") as f:
                    yaml.dump(content, f, default_flow_style=False, sort_keys=False)
                return {"saved": f"{route[1]}/{route[2]}"}
        if method == "POST" and route == ["optimize"]:
            data = self._body()
            goal = goal_from(data.get("goal") or {})
            job = start_job(goal, search_from(data.get("search")), data.get("label") or "optimizer")
            return job_view(job, include_result=False)
        if len(route) >= 2 and route[0] == "jobs":
            job = JOBS.get(route[1])
            if job is None:
                raise FileNotFoundError(route[1])
            if method == "POST" and route[2:] == ["cancel"]:
                job["cancel"] = True
                return job_view(job, include_result=False)
            if method == "GET" and len(route) == 2:
                return job_view(job, include_result=job["status"] != "running")
        if method == "POST" and route == ["evaluate"]:
            return evaluate_recipe(self._body())
        if method == "POST" and route == ["recipe", "end-state"]:
            return recipe_end_state(self._body())
        raise FileNotFoundError(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    global DATA_COMMIT
    load_units()  # fail fast if the data file is missing
    DATA_COMMIT = data_commit()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"BAR Build Optimizer running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
