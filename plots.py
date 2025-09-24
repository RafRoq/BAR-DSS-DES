import matplotlib.pyplot as plt
from typing import List, Any
from matplotlib.patches import Patch
from collections import defaultdict

# Assume your Task and Unit classes are defined elsewhere as you've shown
# from your_module import Task, Unit

def plot_builder_gantt(ax: plt.Axes, completed_tasks: List[Any], simulation_duration: float):
    """
    Generates a Gantt chart showing busy and idle periods for each builder unit.

    Args:
        ax: The Matplotlib Axes object to draw the chart on.
        completed_tasks: A list of completed Task objects.
        simulation_duration: The total duration of the simulation, for showing final idle time.
    """
    builder_activity = []
    
    # 1. Extract activity periods for each builder from the tasks
    for task in completed_tasks:
        if task.start_time is None or task.completion_time is None:
            continue
        duration = task.completion_time - task.start_time
        if duration <= 0:
            continue
        for builder in task.builders_ref:
            builder_label = f"{builder.name} ({builder.id})"
            builder_activity.append({
                'label': builder_label,
                'start': task.start_time,
                'duration': duration
            })

    if not builder_activity:
        ax.set_title("Builder Activity (No Data)")
        return

    # 2. Prepare plot axes and data structures
    unique_builders = sorted(list(set(item['label'] for item in builder_activity)))
    builder_to_y = {name: i for i, name in enumerate(unique_builders)}
    last_task_end_time = {builder: 0.0 for builder in unique_builders}

    ax.set_yticks(range(len(unique_builders)), labels=unique_builders)
    ax.set_ylim(-0.5, len(unique_builders) - 0.5)

    # Sort activities chronologically to correctly calculate idle time
    builder_activity.sort(key=lambda x: x['start'])

    # 3. Plot busy and idle bars for each builder
    for activity in builder_activity:
        y_pos = builder_to_y[activity['label']]
        builder_label = activity['label']
        
        # Draw IDLE time before this task starts
        idle_start = last_task_end_time[builder_label]
        idle_duration = activity['start'] - idle_start
        if idle_duration > 0.1:  # Threshold to avoid plotting tiny slivers
            ax.barh(y_pos, width=idle_duration, left=idle_start, height=0.4, 
                    color='lightgrey', alpha=0.6, edgecolor="grey", hatch='//')

        # Draw BUSY time for the current task
        ax.barh(y_pos, width=activity['duration'], left=activity['start'], height=0.6,
                color='teal', alpha=0.8, edgecolor="black")
        
        # Update the end time for this builder
        last_task_end_time[builder_label] = activity['start'] + activity['duration']

    # 4. Plot final idle time from last task to end of simulation
    for builder, last_end in last_task_end_time.items():
        y_pos = builder_to_y[builder]
        final_idle_duration = simulation_duration - last_end
        if final_idle_duration > 0:
            ax.barh(y_pos, width=final_idle_duration, left=last_end, height=0.4,
                    color='lightgrey', alpha=0.6, edgecolor="grey", hatch='//')

    ax.set_xlabel("Time (seconds)")
    ax.set_title("Builder Unit Activity (Busy vs. Idle)")
    ax.grid(True, linestyle="--", alpha=0.6)
    
    # Add a clear legend
    legend_elements = [
        Patch(facecolor='teal', edgecolor='black', alpha=0.8, label='Busy'),
        Patch(facecolor='lightgrey', edgecolor='grey', alpha=0.6, hatch='//', label='Idle')
    ]
    ax.legend(handles=legend_elements, loc='upper right')

def generate_build_script(completed_tasks: List[Any]) -> str:
    """
    Generates a comprehensive build script from a simulation's results, featuring
    both a master chronological plan and command queues grouped by individual builder unit.

    Args:
        completed_tasks: A list of Task objects processed by the simulation.

    Returns:
        A formatted string with the complete, multi-format build plan.
    """
    if not completed_tasks:
        return "--- No tasks in the simulation to generate a script from. ---"

    # Sort tasks by their actual start time
    sorted_tasks = sorted(completed_tasks, key=lambda t: t.start_time if t.start_time is not None else float('inf'))

    # --- 1. Generate the Master Chronological List ---
    chrono_lines = ["--- Master Chronological Plan ---", "(Follow this list step-by-step)"]
    for i, task in enumerate(sorted_tasks):
        if task.start_time is None or not hasattr(task, 'builders_ref') or not task.builders_ref:
            continue
            
        start_time_str = f"t={task.start_time:.1f}s"
        task_name = task.display_name
        # Now includes builder IDs for clarity in the master list as well
        builder_names = ", ".join(f"{b.name} ({b.id})" for b in task.builders_ref)
        chrono_lines.append(
            f"({i+1}) At {start_time_str}: Use {builder_names} to start building '{task_name}'."
        )
    
    # This dictionary will track which builders are involved in which task number
    task_assignments = defaultdict(list)
    for i, task in enumerate(sorted_tasks):
         if task.start_time is not None and hasattr(task, 'builders_ref'):
              for builder in task.builders_ref:
                   builder_label = f"{builder.name} ({builder.id})"
                   task_assignments[builder_label].append((i + 1, task))

    grouped_lines = ["\n\n--- Individual Builder Command Queues ---", "(For strategic overview and command queuing per unit)"]
    for builder_label, assignments in sorted(task_assignments.items()):
        grouped_lines.append(f"\n--- {builder_label} Command Queue ---")
        for task_num, task in assignments:
            start_time_str = f"t={task.start_time:.1f}s"
            task_name = task.display_name
            
            # Since this is a specific builder's queue, we list all builders for context
            full_builder_list = ", ".join(f"{b.name} ({b.id})" for b in task.builders_ref)
            
            instruction = (
                f"({task_num}) At {start_time_str}: Start building '{task_name}'.\n"
                f"   └─ Collaborators: {full_builder_list}"
            )
            grouped_lines.append(instruction)

    # --- 3. Combine both parts ---
    final_output = "\n".join(chrono_lines) + "\n".join(grouped_lines)
    return final_output