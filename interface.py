import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import json
import yaml
from main import run_and_collect_results, create_task_list_from_recipe


class BuildOrderEditor(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("BAR Econ Simulator - Build Order IDE")
        self.geometry("900x600")

        # --- Data ---
        self.buildables_data = self._load_buildables_data()
        self.build_sequence = []

        # --- Create UI ---
        self._create_menubar()
        self.main_frame = ttk.Frame(self)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_columnconfigure(1, weight=2)
        self.main_frame.grid_columnconfigure(2, weight=1)
        self.main_frame.grid_rowconfigure(0, weight=1)

        self._create_palette_panel(self.main_frame)
        self._create_sequence_panel(self.main_frame)
        self._create_inspector_panel(self.main_frame)

        self.status_var = tk.StringVar(value="Ready")
        self.status_bar = ttk.Label(self, textvariable=self.status_var, anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=5)

    def _load_buildables_data(self):
        try:
            with open("unit_data_as_dict.json", "r") as f:
                return json.load(f)
        except FileNotFoundError:
            messagebox.showerror("Error", "unit_data_as_dict.json not found!")
            self.quit()
            return {}

    def _refresh_sequence_listbox(self):
        """Clears and repopulates the sequence listbox from self.build_sequence data."""
        self.sequence_listbox.delete(0, tk.END)
        for step in self.build_sequence:
            display_text = f"{step['repeat']}x {step['name']}"
            self.sequence_listbox.insert(tk.END, display_text)

    def _create_menubar(self):
        menubar = tk.Menu(self)
        self.config(menu=menubar)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="New", command=self.new_build)
        file_menu.add_command(label="Open YAML...", command=self.open_build)
        file_menu.add_command(label="Save YAML...", command=self.save_build)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.quit)
        menubar.add_cascade(label="File", menu=file_menu)
        sim_menu = tk.Menu(menubar, tearoff=0)
        sim_menu.add_command(label="Run Simulation", command=self.run_simulation)
        menubar.add_cascade(label="Simulation", menu=sim_menu)

    def _create_palette_panel(self, parent):
        palette_frame = ttk.LabelFrame(parent, text="All Buildables")
        palette_frame.grid(row=0, column=0, sticky="nswe", padx=(0, 5))
        palette_frame.grid_rowconfigure(1, weight=1)
        palette_frame.grid_columnconfigure(0, weight=1)

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._update_palette_list)
        ttk.Label(palette_frame, text="Search:").grid(
            row=0, column=0, sticky="w", padx=5, pady=(5, 0)
        )
        ttk.Entry(palette_frame, textvariable=self.search_var).grid(
            row=0, column=1, sticky="ew", padx=5, pady=(5, 0)
        )

        self.palette_listbox = tk.Listbox(palette_frame, exportselection=False)
        self.palette_listbox.grid(
            row=1, column=0, columnspan=2, sticky="nswe", padx=5, pady=5
        )

        ttk.Button(
            palette_frame, text="Add to Sequence ->", command=self.add_to_sequence
        ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 5), padx=5)

        ttk.Button(
            palette_frame, text="<- Assign as Builder", command=self.assign_builder
        ).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 5), padx=5)

        self._update_palette_list()

    def _update_palette_list(self, *args):
        search_term = self.search_var.get().lower()
        self.palette_listbox.delete(0, tk.END)
        for id_name, data in self.buildables_data.items():
            display_name = data.get("displayName", id_name)
            if search_term in id_name.lower() or search_term in display_name.lower():
                self.palette_listbox.insert(tk.END, f"{display_name} - {id_name}")

    def add_to_sequence(self):
        selected_indices = self.palette_listbox.curselection()
        if not selected_indices:
            return
        full_name = self.palette_listbox.get(selected_indices[0])
        new_step = {"name": full_name, "repeat": 1, "builders": []}
        self.build_sequence.append(new_step)
        self._refresh_sequence_listbox()
        self.sequence_listbox.selection_set(tk.END)
        self.sequence_listbox.see(tk.END)
        self.on_sequence_select(None)
        self.status_var.set(f"Added '{full_name}' to sequence.")

    def _step_dict_2_tuple(self):
        result = []
        for step in self.build_sequence:
            result.append((self._id_name(step['name']), step['builders'], step['repeat']))
        return result

    def _id_name(self, string: str):
        return string.split(' - ')[-1].strip()

    def assign_builder(self):
        task_indices = self.sequence_listbox.curselection()
        if not task_indices:
            messagebox.showwarning("No Task Selected", "Please select a task from the sequence first.")
            return
        task_index = task_indices[0]

        builder_indices = self.palette_listbox.curselection()
        if not builder_indices:
            messagebox.showwarning("No Builder Selected", "Please select a constructor from the list on the left.")
            return
        
        builder_full_name = self.palette_listbox.get(builder_indices[0])
        builder_id = self._id_name(builder_full_name)

        task_builders = self.build_sequence[task_index]["builders"]
        # MODIFICATION: Removed the 'if not in' check to allow duplicates
        task_builders.append(builder_id)

        self.on_sequence_select(None) # Refresh the inspector to show the new builder
        self.status_var.set(f"Assigned '{builder_id}' to task {task_index + 1}.")


    def _create_sequence_panel(self, parent):
        sequence_frame = ttk.LabelFrame(parent, text="Build Order Sequence")
        sequence_frame.grid(row=0, column=1, sticky="nswe", padx=5)
        sequence_frame.grid_rowconfigure(0, weight=1)
        sequence_frame.grid_columnconfigure(0, weight=1)
        sequence_frame.grid_columnconfigure(1, weight=1)
        self.sequence_listbox = tk.Listbox(sequence_frame, exportselection=False)
        self.sequence_listbox.grid(row=0, column=0, columnspan=2, sticky="nswe")
        self.sequence_listbox.bind("<<ListboxSelect>>", self.on_sequence_select)
        ttk.Button(sequence_frame, text="Move Up", command=self.move_up).grid(
            row=1, column=0, sticky="ew", pady=5, padx=(0, 2)
        )
        ttk.Button(sequence_frame, text="Move Down", command=self.move_down).grid(
            row=1, column=1, sticky="ew", pady=5, padx=(2, 0)
        )
        ttk.Button(
            sequence_frame, text="Remove Selected", command=self.remove_from_sequence
        ).grid(row=2, column=0, columnspan=2, sticky="ew")

    def _create_inspector_panel(self, parent):
        inspector_frame = ttk.LabelFrame(parent, text="Step Properties")
        inspector_frame.grid(row=0, column=2, sticky="nswe", padx=(5, 0))
        inspector_frame.grid_rowconfigure(4, weight=1) # Adjusted row configure
        inspector_frame.grid_columnconfigure(1, weight=1)
        
        self.selected_item_name = tk.StringVar(value="Nothing Selected")
        ttk.Label(inspector_frame, text="Name:").grid(
            row=0, column=0, sticky="w", padx=5, pady=2
        )
        ttk.Label(
            inspector_frame, textvariable=self.selected_item_name, wraplength=150
        ).grid(row=0, column=1, sticky="w", padx=5, pady=2)
        
        ttk.Label(inspector_frame, text="Repeat:").grid(
            row=1, column=0, sticky="w", padx=5, pady=2
        )
        self.repeat_var = tk.StringVar(value="1")
        ttk.Entry(inspector_frame, textvariable=self.repeat_var).grid(
            row=1, column=1, sticky="ew", padx=5
        )
        
        ttk.Button(inspector_frame, text="Update Step", command=self.update_step).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=5, padx=5
        )

        ttk.Label(inspector_frame, text="Assigned Builder(s):").grid(
            row=3, column=0, columnspan=2, sticky="w", padx=5, pady=2
        )
        self.builder_listbox = tk.Listbox(inspector_frame, exportselection=False)
        self.builder_listbox.grid(
            row=4, column=0, columnspan=2, sticky="nswe", padx=5, pady=5
        )
        ttk.Button(inspector_frame, text="Remove Selected Builder", command=self.remove_builder_from_step).grid(
            row=5, column=0, columnspan=2, sticky="ew", pady=5, padx=5
        )

    def remove_builder_from_step(self):
        """Removes a builder from the currently selected task."""
        task_indices = self.sequence_listbox.curselection()
        if not task_indices:
            messagebox.showwarning("No Task Selected", "Please select a task first.")
            return
        task_index = task_indices[0]

        builder_indices = self.builder_listbox.curselection()
        if not builder_indices:
            messagebox.showwarning("No Builder Selected", "Please select a builder from the list to remove.")
            return
        
        # MODIFICATION: Delete by index, not by value, to handle duplicates correctly
        builder_index_to_remove = builder_indices[0]
        builder_name = self.build_sequence[task_index]["builders"][builder_index_to_remove]
        del self.build_sequence[task_index]["builders"][builder_index_to_remove]
        
        # Refresh the inspector
        self.on_sequence_select(None)
        self.status_var.set(f"Removed builder '{builder_name}'.")


    def new_build(self):
        if messagebox.askyesno(
            "New Build", "Are you sure you want to clear the current sequence?"
        ):
            self.build_sequence.clear()
            self._refresh_sequence_listbox()
            self.on_sequence_select(None)
            self.status_var.set("New build created.")

    def open_build(self):
        filepath = filedialog.askopenfilename(
            filetypes=[("YAML Files", "*.yaml"), ("All Files", "*.*")]
        )
        if not filepath:
            return
        try:
            with open(filepath, "r") as f:
                loaded_data = yaml.safe_load(f)
                if isinstance(loaded_data, list):
                    self.build_sequence = loaded_data
                else:
                    self.build_sequence = []
            self._refresh_sequence_listbox()
            self.on_sequence_select(None)
            self.status_var.set(f"Opened {filepath}")
        except Exception as e:
            messagebox.showerror("Error Opening File", str(e))

    def save_build(self):
        filepath = filedialog.asksaveasfilename(
            defaultextension=".yaml",
            filetypes=[("YAML Files", "*.yaml"), ("All Files", "*.*")],
        )
        if not filepath:
            return
        try:
            with open(filepath, "w") as f:
                yaml.dump(
                    self.build_sequence, f, default_flow_style=False, sort_keys=False
                )
            self.status_var.set(f"Saved to {filepath}")
        except Exception as e:
            messagebox.showerror("Error Saving File", str(e))

    def run_simulation(self):
        self.status_var.set("Running simulation...")
        print("ACTION: Run Simulation with data:", self.build_sequence)
        step_tuples = self._step_dict_2_tuple()
        tasks = create_task_list_from_recipe(step_tuples)
        run_and_collect_results('Build', tasks, 1200)
        self.status_var.set("Simulation complete.")

    def remove_from_sequence(self):
        selected_indices = self.sequence_listbox.curselection()
        if not selected_indices:
            return
        selected_index = selected_indices[0]
        del self.build_sequence[selected_index]
        self._refresh_sequence_listbox()
        self.on_sequence_select(None)
        self.status_var.set(f"Removed step {selected_index + 1}.")

    def _swap_sequence_items(self, idx1, idx2):
        self.build_sequence[idx1], self.build_sequence[idx2] = (
            self.build_sequence[idx2],
            self.build_sequence[idx1],
        )
        self._refresh_sequence_listbox()
        self.sequence_listbox.selection_set(idx2)

    def move_up(self):
        selected_indices = self.sequence_listbox.curselection()
        if not selected_indices:
            return
        idx = selected_indices[0]
        if idx > 0:
            self._swap_sequence_items(idx, idx - 1)

    def move_down(self):
        selected_indices = self.sequence_listbox.curselection()
        if not selected_indices:
            return
        idx = selected_indices[0]
        if idx < len(self.build_sequence) - 1:
            self._swap_sequence_items(idx, idx + 1)

    def on_sequence_select(self, event):
        """Called when a task is selected. Updates the inspector panel."""
        self.selected_item_name.set("Nothing selected")
        self.repeat_var.set("1")
        self.builder_listbox.delete(0, tk.END)
        
        selected_indices = self.sequence_listbox.curselection()
        if not selected_indices:
            return

        selected_index = selected_indices[0]
        step_data = self.build_sequence[selected_index]

        self.selected_item_name.set(step_data.get("name", ""))
        self.repeat_var.set(str(step_data.get("repeat", 1)))

        assigned_builders = step_data.get("builders", [])
        for builder_id in assigned_builders:
            self.builder_listbox.insert(tk.END, builder_id)

        self.status_var.set(f"Selected step {selected_index + 1}")

    def update_step(self):
        """Updates the repeat count for the selected step."""
        selected_indices = self.sequence_listbox.curselection()
        if not selected_indices:
            return
        selected_index = selected_indices[0]

        try:
            repeat_val = int(self.repeat_var.get())
            if repeat_val < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Invalid Input", "Repeat count must be a positive integer."
            )
            return

        self.build_sequence[selected_index]["repeat"] = repeat_val
        self._refresh_sequence_listbox()
        self.sequence_listbox.selection_set(selected_index)
        self.status_var.set(f"Updated step {selected_index + 1}.")


if __name__ == "__main__":
    app = BuildOrderEditor()
    app.mainloop()