# BAR Economy Simulator & Build Order Analyzer

![Python Version](https://img.shields.io/badge/python-3.12%2B-blue)

A Python-based discrete-time simulator designed to model and analyze the economic efficiency of build orders in the RTS game *Beyond All Reason* (BAR).

BAR DSS DES stands for, Beyond All Reason Decision Support System Discrete Event Simulator

## What is This?

As a fan of the complex and strategic gameplay of *Beyond All Reason*, I wanted a way to quantitatively compare different opening strategies and economic plans. This tool was built to move beyond "feel" and into data-driven analysis.

The simulator processes a predefined build order, step-by-step, tracking resource generation (Metal & Energy), build costs, and build times. The goal is to determine the optimal path to a desired tech level or army composition, identifying potential resource stalls and inefficiencies along the way.

## Key Features

-   **Resource Flow Simulation:** Accurately models Metal and Energy income and expenditure over time.
-   **Task Queue Management:** Processes a sequential build order, respecting dependencies and resource requirements.
-   **Object-Oriented Design:** Uses classes to represent in-game entities like factories, generators, and units, making the system extensible.
-   **State Tracking:** Monitors the game state at each time step, including resource storage, unit counts, and task progress.
-   **Data Output:** Generates a summary of the simulation, providing key metrics for analysis.

## Getting Started

To get a local copy up and running, follow these simple steps.

### Prerequisites

-   Python 3.13 or higher
-   pip (Python package installer)

### Installation

1.  **Clone the repository:**
    ```sh
    git clone [https://github.com/](https://github.com/)[Your-GitHub-Username]/[Your-Repo-Name].git
    ```
2.  **Navigate to the project directory:**
    ```sh
    cd [Your-Repo-Name]
    ```
3.  **Create and activate a virtual environment (recommended):**
    - On macOS/Linux:
      ```sh
      python3 -m venv venv
      source venv/bin/activate
      ```
    - On Windows:
      ```sh
      python -m venv venv
      .\venv\Scripts\activate
      ```
4.  **Install the required packages:**
    *(You will need to create this file by running `pip freeze > requirements.txt`)*
    ```sh
    pip install -r requirements.txt
    ```

## Usage

The simulation is configured by defining a list of tasks in the main script.

1.  **Define Your Build Order:**
    Open the main script (e.g., `main.py`) and modify the `tasks` list to define your build schedule. Each task specifies what to build and which builders to use.

    ```python
    # Example of a simple build order definition
    tasks = [
        Task(name="metal_extractor", buildable_type="generator", builders_needed=["commander"]),
        Task(name="metal_extractor", buildable_type="generator", builders_needed=["commander"]),
        Task(name="wind_turbine", buildable_type="generator", builders_needed=["commander"]),
        Task(name="t1_vehicle_pad", buildable_type="factory", builders_needed=["commander"]),
        Task(name="blitz", buildable_type="unit", builders_needed=["t1_vehicle_pad"]),
        Task(name="blitz", buildable_type="unit", builders_needed=["t1_vehicle_pad"]),
    ]
    ```

2.  **Run the Simulation:**
    Execute the main script from your terminal.
    ```sh
    python main.py
    ```

3.  **Analyze the Output:**
    The script will print a summary of the simulation to the console and/or generate an output file (e.g., an Excel spreadsheet) with the detailed results.

    ```
    --- Simulation Complete ---
    Total Time: 345.2s
    Final Metal Stored: 87.5
    Final Energy Stored: 120.1
    
    --- Units Produced ---
    - Blitz: 2
    
    --- Buildings Constructed ---
    - Metal Extractor: 2
    - Wind Turbine: 1
    - T1 Vehicle Pad: 1
    ```

## Project Status

**In-Progress:** This project is under active development. The core simulation logic is functional, but features are still being added and refined.

### Known Limitations
-   Does not currently model unit movement, resource reclamation or travel time.
-   Assumes a constant rate of resource generation from extractors.
-   Wind turbines have fixed energy output values
-   The current build order logic is strictly sequential and somewhat conditional.
-   It never waits to start a task, if starting a task at the moment will stall mid construction it will check if the next task in the list is buildable

## Future Plans (TODO)

-   [ ] Implement a smarter task assignment system.
-   [ ] Implement metal extractor upgrade logic.
-   [ ] Complete the data.py file with all the available game data
