import logging
import pandas as pd
import math
import matplotlib.pyplot as plt
import json

from unit_data_transformer import OUTPUT_FILE

logging.basicConfig(
    filename="simulationV2_log.txt",
    filemode="w",
    level=logging.DEBUG,
    format="%(message)s",
)
logger = logging.getLogger()

UNITS_DATA = {}
with open(OUTPUT_FILE, "r") as data:
    UNITS_DATA = json.load(data)

TIDAL_AVERAGE = 14
WIND_AVERAGE = 14
METAL_SPOT_VALUE = 20
ENERGY_CONVERSION_FLOOR = 0.2

class Unit:
    _id_counter = 0

    def __init__(self, name_definition):
        self.id = Unit._id_counter
        Unit._id_counter += 1
        self.idle = True
        # --------------------
        unit: dict = UNITS_DATA[name_definition]
        self.name = str(unit['displayName'])
        self.name_definition = name_definition
        self.energy_cost = unit["unit"]["energyCost"]
        self.metal_cost = unit["unit"]["metalCost"]
        self.build_cost = unit["unit"]["buildTime"]
        self.energy_storage = unit["unit"]["energyStorage"]
        self.metal_storage = unit["unit"]["metalStorage"]
        self.energy_generation = unit["unit"]["energyProduced"]
        self.energy_generation_wind = (
            WIND_AVERAGE if unit["unit"]["windGenerator"] > 0 else 0
        )
        self.energy_generation_tidal = (
            TIDAL_AVERAGE if unit["unit"]["tidalGenerator"] > 0 else 0
        )
        self.metal_generation = METAL_SPOT_VALUE * unit["unit"]["extractsMetal"] * 1000
        self.build_power = unit["unit"]["buildPower"]
        self.energy_consumption = unit["unit"]["energyUpkeep"]
        self.energy_conversion_capacity = unit["unit"]["energyConversionCapacity"]
        self.energy_conversion_efficiency = unit["unit"]["energyConversionEfficiency"]

    def __eq__(self, other):
        if not isinstance(other, Unit):
            return NotImplemented
        return self.id == other.id


class Task:
    def __init__(self, name: str, builders: list[str], action: str):
        self.name = name
        self.action = action
        self.builders = builders
        self.progress: float = 0.0
        self.started: bool = False
        self.completed: bool = False
        self.waiting_for_builders: bool = False
        self.builders_ref: list[Unit] = []
        self.builders_str: str = ""
        self.total_construction_power_needed: int = 0
        self.total_energy_needed: int = 0
        self.total_metal_needed: int = 0
        self.total_construction_power_available: int = 0
        self.time_to_complete: float = 0
        self.energy_cost_per_second: float = 0
        self.metal_cost_per_second: float = 0
        self.print_unsustained_message: bool = True
        self.print_builders_cooldown_message: bool = True
        self.start_time: float = 0
        self.completion_time: float = 0
        self.current_status: str = None
        self.status_history: list[tuple] = []
        self.display_name: str = ''

    def start(self, builders: list[Unit]) -> bool:
        if self.started:
            logging.error(f"Task {self.name} has already been started.")
            return False
        self.started = True
        for builder in builders:
            builder.idle = False
        self.builders_ref = builders
        self.buildable: Unit = Unit(self.name)
        self.display_name = self.buildable.name
        self.total_construction_power_needed: int = self.buildable.build_cost
        self.total_energy_needed: int = self.buildable.energy_cost
        self.total_metal_needed: int = self.buildable.metal_cost
        for builder in builders:
            self.total_construction_power_available += builder.build_power
        self.time_to_complete: float = (
            self.total_construction_power_needed
            / self.total_construction_power_available
        )
        self.energy_cost_per_second: float = (
            self.total_energy_needed / self.time_to_complete
        )
        self.metal_cost_per_second: float = (
            self.total_metal_needed / self.time_to_complete
        )
        logging.info(
            f"Task {self.name} started with builders {[b.name for b in builders]}, {self.time_to_complete:.1f}s to complete."
        )
        self.builders_str = "\n".join(
            f"  - ID: {b.id}, Name: {b.name}" for b in self.builders_ref
        )
        return True


class GameSimulation:
    TIME_STEP = 0.001
    PRINT_INTERVAL = 1

    def __init__(self, tasks: list[Task] = []):
        self.time: float = 0.0
        self.energy_generation: float = 0
        self.metal_generation: float = 0
        self.energy_consumption: float = 0
        self.metal_consumption: float = 0
        self.energy_generation_future: float = 0
        self.metal_generation_future: float = 0
        self.energy: float = 1000
        self.metal: float = 1000
        self.max_energy: int = 1000
        self.max_metal: int = 1000
        self.total_energy_generated: int = 0
        self.total_metal_generated: int = 0
        self.total_energy_spent: int = 0
        self.total_metal_spent: int = 0
        self.total_energy_lost: int = 0
        self.total_metal_lost: int = 0
        self.units: list[Unit] = []
        self.tasks_completed: list[Task] = []
        self.task_in_progress: list[Task] = []
        self.tasks: list[Task] = tasks
        self.tasks_done: int = 0
        self.last_print_time: int = -1
        self.print_state_next: bool = False
        self.cookbook: str = ""
        self.timeline_data: list[dict] = []
        self.idle_construction_power: int = 0
        self.total_construction_power: int = 0
        # -----------------------------
        self.units.append(Unit("armcom"))

    def check_builders_availability(
        self, task: Task, builders: list[Unit]
    ) -> Unit | None:
        for builder in builders:
            if not builder.idle:
                return False
        if task.print_unsustained_message:
            logging.info(f"All builders are idle for task {task.name}.")
        return True

    def obtain_builders_reference(self, builders: list[str]) -> list[Unit]:
        builders_copy = builders.copy()
        result: list[Unit] = []
        for unit in self.units:
            for unit_name in builders_copy:
                if unit.name_definition == unit_name:
                    builders_copy.remove(unit_name)
                    result.append(unit)
        return result

    def _calculate_construction_power(self):
        self.idle_construction_power = 0
        self.total_construction_power = 0
        for unit in self.units:
            self.total_construction_power += unit.build_power
            if unit.idle:
                self.idle_construction_power += unit.build_power
        return

    def can_build_sustainable(self, task: Task, builders: list[Unit]) -> bool:
        buildable: Unit = Unit(task.name)
        total_construction_power_available: int = 0
        total_construction_power_needed: int = buildable.build_cost
        total_energy_needed: int = buildable.energy_cost
        total_metal_needed: int = buildable.metal_cost
        for builder in builders:
            total_construction_power_available += builder.build_power
        time_to_complete: float = (
            total_construction_power_needed / total_construction_power_available
        )
        energy_cost_per_second: float = total_energy_needed / time_to_complete
        metal_cost_per_second: float = total_metal_needed / time_to_complete
        energy_generation_during_task = (
            self.energy_generation_future - energy_cost_per_second
        )
        metal_generation_during_task = (
            self.metal_generation_future - metal_cost_per_second
        )
        if energy_generation_during_task == 0 and metal_generation_during_task == 0:
            logging.info(
                f"Sustainable build possible for task {task.name}: will complete in {time_to_complete:.1f}s before resources run out."
            )
            return True
        if energy_generation_during_task < 0:
            time_to_zero_energy = self.energy / abs(energy_generation_during_task)
        else:
            time_to_zero_energy = 1_000_000
        if metal_generation_during_task < 0:
            time_to_zero_metal = self.metal / abs(metal_generation_during_task)
        else:
            time_to_zero_metal = 1_000_000
        if (
            time_to_complete < time_to_zero_energy
            and time_to_complete < time_to_zero_metal
        ):
            logging.info(
                f"Sustainable build possible for task {task.name}: will complete in {time_to_complete:.1f}s before resources run out.\n{energy_generation_during_task}, {self.energy_generation_future}"
            )
            return True
        else:
            if task.print_unsustained_message:
                logging.info(
                    f"Unsustainable build for task {task.name}: will NOT complete in {time_to_complete:.1f}s before resources run out."
                )
                task.print_unsustained_message = False
                return False

    def have_dependencies(self, task: Task) -> bool:
        return True

    def start_task(self, task: Task) -> bool:
        task.start(self.obtain_builders_reference(task.builders))
        task.start_time = self.time
        task.current_status = "WORKING"
        task.status_history.append((self.time, "WORKING"))
        self.task_in_progress.append(task)
        self.tasks.remove(task)
        self.cookbook += f"{self.time:.1f}: task {task.name} started with builders:\n{task.builders_str}\n\n"
        return True

    def work_on_tasks(self):
        if not self.task_in_progress:
            return

        self.energy_consumption = 0
        self.metal_consumption = 0
        for task in self.task_in_progress[:]:
            if task.completed:
                continue

            new_status = ""

            progress_this_tick = (
                task.total_construction_power_available * self.TIME_STEP
            ) / task.total_construction_power_needed
            if (task.progress + progress_this_tick) > 1.0:
                progress_this_tick = 1 - task.progress
            energy_needed = task.total_energy_needed * progress_this_tick
            metal_needed = task.total_metal_needed * progress_this_tick
            if self.energy >= energy_needed and self.metal >= metal_needed:
                new_status = "WORKING"
            else:
                new_status = "STALLED"

            if new_status != task.current_status:
                task.current_status = new_status
                task.status_history.append((self.time, new_status))

            if new_status == "WORKING":
                self.energy -= energy_needed
                self.metal -= metal_needed
                self.total_energy_spent += energy_needed
                self.total_metal_spent += metal_needed
                self.energy_consumption += task.energy_cost_per_second
                self.metal_consumption += task.metal_cost_per_second
                task.progress += progress_this_tick

                if (
                    math.isclose(1.0, task.progress, abs_tol=1e-5)
                    or task.progress >= 1.0
                ):
                    self.complete_task(task)
                    self._calculate_construction_power()

    def complete_task(self, task: Task):
        task.progress = 1.0
        task.completed = True
        task.completion_time = self.time
        task.status_history.append((self.time, "COMPLETED"))

        if task in self.task_in_progress:
            self.task_in_progress.remove(task)
        self.tasks_completed.append(task)

        for builder in task.builders_ref:
            builder.idle = True

        for other_task in self.tasks:
            other_task.print_unsustained_message = True
            other_task.waiting_for_builders = False

        new_buildable: Unit = Unit(task.name)
        if new_buildable:
            self.units.append(new_buildable)
            logging.info(
                f"{new_buildable.__class__.__name__} {new_buildable.name} created."
            )

        logging.info(f"Task {task.name} completed.")

    def calculate_resource_storage(self):
        self.max_energy = 500
        self.max_metal = 500
        for unit in self.units:
            self.max_energy += unit.energy_storage
            self.max_metal += unit.metal_storage
    
    def _process_energy_conversion(self):
        for unit in self.units:
            if (unit.energy_conversion_capacity > 0) and (unit.energy_conversion_efficiency > 0) and ((self.energy + unit.energy_conversion_capacity) > (self.max_energy * ENERGY_CONVERSION_FLOOR)):
                self.energy_generation -= unit.energy_conversion_capacity
                self.metal_generation += unit.energy_conversion_capacity * unit.energy_conversion_efficiency


    def calculate_resource_generation(self):
        self.energy_generation = 0
        self.metal_generation = 0
        self.energy_generation_future = 0
        self.metal_generation_future = 0
        self._process_energy_conversion()
        for unit in self.units:
            self.energy_generation += (
                unit.energy_generation
                + unit.energy_generation_wind
                + unit.energy_generation_tidal
                - unit.energy_consumption
            )
            self.metal_generation += unit.metal_generation
        for task_in_progress in self.task_in_progress:
            if not (task_in_progress.completed):
                self.energy_generation_future -= task_in_progress.energy_cost_per_second
                self.metal_generation_future -= task_in_progress.metal_cost_per_second
        self.energy_generation_future += self.energy_generation
        self.metal_generation_future += self.metal_generation

    def apply_resource_generation(self):
        self.energy += self.energy_generation * self.TIME_STEP
        self.metal += self.metal_generation * self.TIME_STEP
        if self.energy > self.max_energy:
            self.total_energy_lost += self.energy - self.max_energy
            self.energy = self.max_energy
        if self.metal > self.max_metal:
            self.total_metal_lost += self.metal - self.max_metal
            self.metal = self.max_metal

    def print_status(self):
        logger.info(
            f"Time: {self.time:.1f}s | Energy: {self.energy:.1f}/{self.max_energy:.1f} | Metal: {self.metal:.1f}/{self.max_metal:.1f}"
        )
        logger.info(
            f"Energy Gen: {(self.energy_generation - self.energy_consumption):.1f}/s | Metal Gen: {(self.metal_generation - self.metal_consumption):.1f}/s"
        )
        logger.info(f"Units: {len(self.units)}")
        logger.info(f"Tasks in Progress: {len(self.task_in_progress)}")
        for task in self.task_in_progress:
            logger.info(
                f"  - Task: {task.name}, Action: {task.action}, Progress: {task.progress * 100:.1f}%"
            )
        logger.info("-" * 50)

    def check_tasks(self):
        if len(self.tasks) == 0 and len(self.task_in_progress) == 0:
            logger.info("No tasks to process. Ending simulation.")
            return False
        elif len(self.tasks) == 0 and len(self.task_in_progress) > 0:
            return True
        for task in self.tasks:
            if (
                not task.started
                and not task.completed
                and not task.waiting_for_builders
            ):
                builders = self.obtain_builders_reference(task.builders)
                if len(builders) == 0 or len(builders) != len(task.builders):
                    logger.info(
                        f"Specified builders do not exist for task {task.name}."
                    )
                    task.waiting_for_builders = True
                    return True
                if not self.check_builders_availability(task, builders):
                    task.waiting_for_builders = True
                    logger.info(
                        f"Builders not available for task {task.name}. Waiting..."
                    )
                    return True
                if self.can_build_sustainable(task, builders):
                    self.start_task(task)
                    self._calculate_construction_power()
                    return True
                elif (
                    len(self.task_in_progress) == 0
                    and self.energy == self.max_energy
                    and self.metal == self.max_metal
                ):
                    logger.info(
                        f"Cannot start task {task.name} due to resource constraints. Ending simulation."
                    )
                    return False
        if len(self.task_in_progress) > 0:
            return True
        elif self.energy != self.max_energy or self.metal != self.max_metal:
            return True
        else:
            raise RuntimeError("Undefined ending for check_stats()")

    def _collect_snapshot(self):
        snapshot = {
            "time": self.time,
            "metal": self.metal,
            "energy": self.energy,
            "net_metal_sec": self.metal_generation - self.metal_consumption,
            "net_energy_sec": self.energy_generation - self.energy_consumption,
            "idle_construction_power": self.idle_construction_power,
            "total_construction_power": self.total_construction_power,
            "unit_count": len(self.units),
        }
        self.timeline_data.append(snapshot)

    def simulate_step(self):
        self.calculate_resource_storage()
        self.calculate_resource_generation()
        x = self.check_tasks()
        self.apply_resource_generation()
        self.work_on_tasks()
        self.time += self.TIME_STEP
        return x

    def run(self, max_time: int = 300):
        logger.info(f"Starting simulation for a max of {max_time} seconds.")
        last_log_time = -1
        while self.time < max_time:
            if self.print_state_next:
                self.print_state_next = False
                self.print_status()
            x = self.simulate_step()
            if int(self.time) > last_log_time:
                self._collect_snapshot()
                last_log_time = int(self.time)
            if not x:
                self.print_status()
                logger.info("No more tasks can be processed. Ending simulation.")
                break
            if self.time - self.last_print_time >= self.PRINT_INTERVAL:
                self.print_status()
                self.last_print_time = self.time


def run_and_collect_results(build_name: str, tasks: list, max_time: int) -> dict:
    print(f"\n--- Running Simulation: {build_name} ---")

    game = GameSimulation(tasks=tasks)
    game.run(max_time=max_time)

    with open(f"{build_name}.txt", "w") as file:
        file.write(game.cookbook)

    completed_tasks = game.tasks_completed
    timeline_data = game.timeline_data

    timeline_df = pd.DataFrame(timeline_data)

    fig, axs = plt.subplots(
        5,
        1,
        figsize=(18, 20),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 2, 2, 2, 1.5]},
    )

    if not timeline_df.empty:
        axs[0].plot(
            timeline_df["time"],
            timeline_df["metal"],
            label="Metal Stored",
            color="grey",
            lw=2,
        )
        axs[0].plot(
            timeline_df["time"],
            timeline_df["energy"],
            label="Energy Stored",
            color="gold",
            lw=2,
        )
    axs[0].set_title("Resource Storage Over Time")
    axs[0].set_ylabel("Amount Stored")

    if not timeline_df.empty:
        axs[1].plot(
            timeline_df["time"],
            timeline_df["net_metal_sec"],
            label="Net Metal/sec",
            color="grey",
            lw=2,
        )
        axs[1].plot(
            timeline_df["time"],
            timeline_df["net_energy_sec"],
            label="Net Energy/sec",
            color="gold",
            lw=2,
        )
    axs[1].axhline(0, color="red", linestyle="--", lw=1)
    axs[1].set_title("Net Income Over Time")
    axs[1].set_ylabel("Rate (/sec)")

    if not timeline_df.empty:
        axs[2].plot(
            timeline_df["time"],
            timeline_df["unit_count"],
            label="Unit Count",
            color="cyan",
            lw=2,
        )
    axs[2].set_title("Army Size Over Time")
    axs[2].set_ylabel("Number of Units")

    if not timeline_df.empty:
        busy_power = (
            timeline_df["total_construction_power"]
            - timeline_df["idle_construction_power"]
        )

        axs[3].stackplot(
            timeline_df["time"],
            [busy_power, timeline_df["idle_construction_power"]],
            labels=["Busy Power", "Idle Power"],
            colors=["#2ca02c", "#d62728"],
        )
        axs[3].set_title("Build Power Utilization")
        axs[3].set_ylabel("Construction Power")
        axs[3].legend(loc="upper left")

    if completed_tasks:
        unique_names = sorted(list(set(task.display_name for task in completed_tasks)))
        name_to_y = {name: i for i, name in enumerate(unique_names)}

        axs[4].set_yticks(range(len(unique_names)), labels=unique_names)

        status_colors = {
            "WORKING": "dodgerblue",
            "STALLED": "gold",
        }

        for task in completed_tasks:
            y_pos = name_to_y.get(task.display_name)
            if y_pos is not None and task.status_history:
                for i in range(len(task.status_history) - 1):
                    start_time, status = task.status_history[i]
                    end_time, _ = task.status_history[i + 1]
                    duration = end_time - start_time
                    color = status_colors.get(status, "red")
                    axs[4].barh(
                        y_pos,
                        width=duration,
                        left=start_time,
                        height=0.5,
                        color=color,
                        alpha=0.7,
                        edgecolor="black",
                    )

            if task.start_time is not None:
                axs[4].plot(task.start_time, y_pos, marker=">", color="red", ms=6)
            if task.completion_time is not None:
                axs[4].plot(
                    task.completion_time, y_pos, marker="|", color="green", ms=8, mew=2
                )

    for i in range(4):
        axs[i].legend(loc="upper left")
        axs[i].grid(True, linestyle="--", alpha=0.6)

    axs[4].grid(True, linestyle="--", alpha=0.6)
    axs[4].set_xlabel("Time (seconds)")
    axs[4].set_title("Task Gantt Chart")

    fig.suptitle(f"Simulation Analysis: {build_name}", fontsize=18)
    fig.tight_layout(rect=[0, 0.03, 1, 0.96])
    plt.show()
    print(
        f"Simulation ended at time {game.time:.1f}s\n"
        f"Tasks completed: {game.tasks_done} of {len(game.tasks)}\n"
        f"Energy: {game.energy:.1f}/{game.max_energy:.1f}, Metal: {game.metal:.1f}/{game.max_metal:.1f}\n"
        f"Total Energy Generated: {game.total_energy_generated:.1f}, Total Metal Generated: {game.total_metal_generated:.1f}\n"
        f"Tasks done: {[x.name for x in game.tasks_completed]}\n"
        f"Tasks not done: {[x.name for x in game.tasks]}\n"
    )

    return {
        "Build Order": build_name,
        "End Time (s)": f"{game.time:.1f}",
        "Completed Tasks": len(game.tasks_completed),
        "Final Energy": f"{game.energy:.1f}",
        "Final Metal": f"{game.metal:.1f}",
        "Energy/s": f"{game.energy_generation:.2f}",
        "Metal/s": f"{game.metal_generation:.2f}",
        "Total Energy Gen": f"{game.total_energy_generated:.1f}",
        "Total Metal Gen": f"{game.total_metal_generated:.1f}",
        "Total Energy Spent": f"{game.total_energy_spent:.1f}",
        "Total Metal Spent": f"{game.total_metal_spent:.1f}",
        "Total Energy Lost": f"{game.total_energy_lost:.1f}",
        "Total Metal Lost": f"{game.total_metal_lost:.1f}",
        "Average Energy Gen/s": f"{(game.total_energy_generated / game.time):.2f}",
        "Average Metal Gen/s": f"{(game.total_metal_generated / game.time):.2f}",
        "Average Energy Spent/s": f"{(game.total_energy_spent / game.time):.2f}",
        "Average Metal Spent/s": f"{(game.total_metal_spent / game.time):.2f}",
    }


def create_task_list_from_recipe(recipe: list) -> list:
    final_task_list = []
    for name, builders, repeat in recipe:
        for _ in range(repeat):
            final_task_list.append(Task(name, builders, "build"))
    return final_task_list


if __name__ == "__main__":
    armada_bot = [
        ("armmex", ["armcom"], 3),
        ("armwin", ["armcom"], 4),
        ("armlab", ["armcom"], 1),
        ("armck", ["armlab", "armcom"], 1),
        ("armwin", ["armcom"], 4),
        ("armmex", ["armcom"], 2),
        ("armrad", ["armcom"], 1),
        (
            "armck",
            ["armlab", "armck"],
            1,
        ),
        (
            "armpw",
            ["armlab", "armck"],
            10,
        ),
        (
            "armrock",
            ["armlab", "armck"],
            10,
        ),
        (
            "armestor",
            ["armck", "armcom"],
            1,
        ),
        (
            "armwin",
            ["armck", "armcom"],
            8,
        ),
        (
            "armmakr",
            ["armck", "armcom"],
            1,
        ),
        (
            "armham",
            ["armlab", "armck"],
            10,
        ),
    ]
    build_orders = {
        "armada_bot": create_task_list_from_recipe(armada_bot),
    }
    print("--- Starting all simulations... ---")
    all_results = []
    simulation_duration = 1200

    for name, tasks in build_orders.items():
        logger.info(f"Running Shedule: {name}")
        result = run_and_collect_results(name, tasks, max_time=simulation_duration)
        all_results.append(result)

    print("--- All simulations completed successfully. ---")
