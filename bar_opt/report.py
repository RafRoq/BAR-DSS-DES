"""Turns a recorded run into the analysis graph, builder Gantt chart and cookbook."""

from collections import defaultdict
from dataclasses import dataclass, field

import matplotlib.pyplot as plt

from .plots import plot_builder_gantt
from .sim import State


@dataclass
class BuilderView:
    id: int
    name: str


@dataclass
class TaskView:
    """Duck-types main.Task for plots.py."""

    name: str
    display_name: str
    start_time: float
    completion_time: float
    builders_ref: list[BuilderView]
    status_history: list[tuple] = field(default_factory=list)


def _builder_view(state: State, builder: int) -> BuilderView:
    return BuilderView(builder, state.units[state.recorder.builder_units[builder]].display_name)


def job_views(state: State) -> list[TaskView]:
    """One view per job, with WORKING/STALLED status history taken from the segments."""
    rec = state.recorder
    views = []
    for job in sorted(rec.jobs.values(), key=lambda j: j["start"]):
        end = job["end"] if job["end"] is not None else state.time
        history, last = [], None
        for seg in rec.segments:
            if seg.t1 <= job["start"] or seg.t0 >= end or seg.t1 == seg.t0:
                continue
            status = "STALLED" if seg.speed < 1 - 1e-9 else "WORKING"
            if status != last:
                history.append((max(seg.t0, job["start"]), status))
                last = status
        history.append((end, "COMPLETED"))
        views.append(TaskView(job["unit"], state.units[job["unit"]].display_name, job["start"], end,
                              [_builder_view(state, b) for _, b in job["joins"]], history))
    return views


def builder_views(state: State) -> list[TaskView]:
    """One view per (job, builder) from the moment the builder joined, for plot_builder_gantt."""
    rec = state.recorder
    views = []
    for job in rec.jobs.values():
        end = job["end"] if job["end"] is not None else state.time
        for joined, b in job["joins"]:
            views.append(TaskView(job["unit"], state.units[job["unit"]].display_name, joined, end,
                                  [_builder_view(state, b)]))
    return views


def cookbook(state: State, title: str = "") -> str:
    rec = state.recorder
    events = []
    for job_id, job in rec.jobs.items():
        name = state.units[job["unit"]].display_name
        for i, (t, b) in enumerate(job["joins"]):
            events.append((t, b, ("start building" if i == 0 else "assist") + f" '{name}' (job {job_id})"))
    events.sort(key=lambda e: (e[0], e[1]))

    lines = [f"--- {title} ---"] if title else []
    lines.append("--- Master Chronological Plan ---")
    for n, (t, b, what) in enumerate(events, 1):
        lines.append(f"({n}) At t={t:.1f}s: {_builder_view(state, b).name} ({b}) {what}.")
    per_builder = defaultdict(list)
    for n, (t, b, what) in enumerate(events, 1):
        per_builder[b].append(f"({n}) At t={t:.1f}s: {what}.")
    lines.append("\n--- Individual Builder Command Queues ---")
    for b in sorted(per_builder):
        lines.append(f"\n--- {_builder_view(state, b).name} ({b}) Command Queue ---")
        lines.extend(per_builder[b])
    return "\n".join(lines)


def plot_run(state: State, title: str, path: str | None = None, show: bool = True) -> None:
    """The six-panel analysis figure of main.run_and_collect_results, from exact segments."""
    rec = state.recorder
    segs = rec.segments
    times = [segs[0].t0] + [s.t1 for s in segs] if segs else []
    metal = [segs[0].metal0] + [s.metal1 for s in segs] if segs else []
    energy = [segs[0].energy0] + [s.energy1 for s in segs] if segs else []

    fig, axs = plt.subplots(6, 1, figsize=(18, 20), sharex=True,
                            gridspec_kw={"height_ratios": [3, 2, 1, 2, 2, 2]})
    if segs:
        axs[0].plot(times, metal, label="Metal Stored", color="grey", lw=2)
        axs[0].plot(times, energy, label="Energy Stored", color="gold", lw=2)
        edges = [s.t0 for s in segs] + [segs[-1].t1]
        # Segments under 0.05 s (e.g. energy refilling the converter level after a storage
        # increase) are real but render as spikes; show the rates of the longer ones.
        shown = [s for s in segs if s.t1 - s.t0 >= 0.05] or segs
        rate_edges = [s.t0 for s in shown] + [shown[-1].t1]
        axs[1].stairs([s.net_metal for s in shown], rate_edges, baseline=None, label="Net Metal/sec", color="grey", lw=2)
        axs[1].stairs([s.net_energy for s in shown], rate_edges, baseline=None, label="Net Energy/sec", color="gold", lw=2)
        axs[2].stairs([s.units for s in segs], edges, baseline=None, label="Unit Count", color="cyan", lw=2)
        busy = [s.total_bp - s.idle_bp for s in segs]
        idle = [s.idle_bp for s in segs]
        axs[3].stairs([b + i for b, i in zip(busy, idle)], edges, baseline=busy, fill=True,
                      color="#d62728", label="Idle Power")
        axs[3].stairs(busy, edges, fill=True, color="#2ca02c", label="Busy Power")
    axs[0].set_title("Resource Storage Over Time")
    axs[0].set_ylabel("Amount Stored")
    axs[1].axhline(0, color="red", linestyle="--", lw=1)
    axs[1].set_title("Net Income Over Time")
    axs[1].set_ylabel("Rate (/sec)")
    axs[2].set_title("Unit Count Over Time")
    axs[2].set_ylabel("Number of Units")
    axs[3].set_title("Build Power Utilization")
    axs[3].set_ylabel("Construction Power")

    jobs = job_views(state)
    names = sorted({j.display_name for j in jobs})
    y_of = {n: i for i, n in enumerate(names)}
    axs[4].set_yticks(range(len(names)), labels=names)
    colors = {"WORKING": "dodgerblue", "STALLED": "gold"}
    for job in jobs:
        y = y_of[job.display_name]
        for (t0, status), (t1, _) in zip(job.status_history, job.status_history[1:]):
            axs[4].barh(y, width=t1 - t0, left=t0, height=0.5, color=colors.get(status, "red"),
                        alpha=0.7, edgecolor="black")
        axs[4].plot(job.start_time, y, marker=">", color="red", ms=6)
        axs[4].plot(job.completion_time, y, marker="|", color="green", ms=8, mew=2)
    axs[4].set_title("Task Gantt Chart (blue: working, yellow: stalled)")

    spawn = {f"{_builder_view(state, b).name} ({b})": t for b, t in rec.builder_spawn.items()}
    plot_builder_gantt(axs[5], builder_views(state), state.time, spawn)
    for i in range(4):
        axs[i].legend(loc="upper left")
        axs[i].grid(True, linestyle="--", alpha=0.6)
    axs[4].grid(True, linestyle="--", alpha=0.6)
    axs[5].set_xlabel("Time (seconds)")
    fig.suptitle(f"Simulation Analysis: {title} : {state.time:.1f}s", fontsize=18)
    fig.tight_layout(rect=[0, 0.03, 1, 0.96])
    if path:
        fig.savefig(path, dpi=80)
    if show:
        plt.show()
    plt.close(fig)
