# --------------------------
# Game Data
# --------------------------
UNITS: dict = {
    "commander": {
        "provides": {"metal": 2, "energy": 30, "construction_power": 300},
        "costs": {"metal": 1250, "energy": 0, "build": 0},
        "build_cost": 0,
    },
    "construction_vehicle_cortex": {
        "costs": {"metal": 145, "energy": 2100, "build": 4160},
        "provides": {"construction_power": 95},
    },
    "construction_vehicle_armada": {
        "costs": {"metal": 135, "energy": 1950, "build": 4050},
        "provides": {"construction_power": 95},
    },
    "rascal": {"costs": {"metal": 26, "energy": 270, "build": 1150}},
    "rover": {"costs": {"metal": 31, "energy": 370, "build": 950}},
    "incisor": {"costs": {"metal": 120, "energy": 1040, "build": 2200}},
    "blitz": {"costs": {"metal": 110, "energy": 900, "build": 1960}},
    "lasher": {"costs": {"metal": 155, "energy": 2400, "build": 3440}},
    "brute": {"costs": {"metal": 235, "energy": 2400, "build": 3310}},
    "pounder": {"costs": {"metal": 220, "energy": 2600, "build": 3000}},
    "stout": {"costs": {"metal": 225, "energy": 2000, "build": 2900}},
    "whistler": {"costs": {"metal": 150, "energy": 2100, "build": 3420}},
    "wolverine": {"costs": {"metal": 170, "energy": 2500, "build": 3550}},
    "shellshocker": {"costs": {"metal": 135, "energy": 2200, "build": 3000}},
    "janus": {"costs": {"metal": 240, "energy": 2600, "build": 3550}},
    # ARMADA - BOTS
    "tick": {"costs": {"metal": 21, "energy": 300, "build": 800}},
    "pawn": {"costs": {"metal": 54, "energy": 900, "build": 1650}},
    "construction_bot_armada": {
        "costs": {"metal": 110, "energy": 1600, "build": 3450},
        "provides": {"construction_power": 95},
    },
    "rocketeer": {"costs": {"metal": 120, "energy": 1000, "build": 2010}},
    "crossbow": {"costs": {"metal": 125, "energy": 1100, "build": 1830}},
    "lazarus": {"costs": {"metal": 130, "energy": 1400, "build": 2800}},
    "mace": {"costs": {"metal": 130, "energy": 1300, "build": 2200}},
    "centurion": {"costs": {"metal": 270, "energy": 3100, "build": 4200}},
}

FACTORIES = {
    "t1_vehicle_pad_cortex": {
        "costs": {"metal": 570, "energy": 1550, "build": 5650},
        "provides": {"construction_power": 150},
    },
    "t1_vehicle_pad_armada": {
        "costs": {"metal": 590, "energy": 1550, "build": 5700},
        "provides": {"construction_power": 150},
    },
    "t1_bot_pad_cortex": {
        "costs": {"metal": 470, "energy": 1050, "build": 5000},
        "provides": {"construction_power": 150},
    },
    "t1_bot_pad_armada": {
        "costs": {"metal": 500, "energy": 950, "build": 5000},
        "provides": {"construction_power": 150},
    },
    "t1_aircraft_pad_cortex": {
        "costs": {"metal": 690, "energy": 1100, "build": 5680},
        "provides": {"construction_power": 150},
    },
    "t1_aircraft_pad_armada": {
        "costs": {"metal": 710, "energy": 1100, "build": 5750},
        "provides": {"construction_power": 150},
    },
}

BUILDINGS = {
    "radar_tower_armada": {"costs": {"metal": 60, "energy": 630, "build": 1140}},
    "energy_storage_armada": {
        "costs": {"metal": 170, "energy": 1700, "build": 4110},
        "storage": {"energy": 6000},
    },
    "construction_tower_armada": {
        "costs": {"metal": 210, "energy": 3200, "build": 5300},
        "provides": {"construction_power": 200},
    },
}

GENERATORS = {
    "metal_extractor_cortex": {
        "costs": {"metal": 50, "energy": 500, "build": 1870},
        "provides": {"metal": 2, "energy": -3},
        "storage": {"metal": 50},
    },
    "metal_extractor_armada": {
        "costs": {"metal": 50, "energy": 500, "build": 1800},
        "provides": {"metal": 2, "energy": -3},
        "storage": {"metal": 50},
    },
    "solar_collector_cortex": {
        "costs": {"metal": 150, "energy": 0, "build": 2800},
        "provides": {"energy": 20},
    },
    "solar_collector_armada": {
        "costs": {"metal": 155, "energy": 0, "build": 2600},
        "provides": {"energy": 20},
    },
    "advanced_solar_collector_armada": {
        "costs": {"metal": 350, "energy": 5000, "build": 7950},
        "provides": {"energy": 75},
    },
    "advanced_solar_collector_cortex": {
        "costs": {"metal": 370, "energy": 4000, "build": 8150},
        "provides": {"energy": 75},
    },
    "wind_turbine_cortex": {
        "costs": {"metal": 43, "energy": 175, "build": 1680},
        "provides": {"energy": 14},
        "storage": {"energy": 0.5},
    },
    "wind_turbine_armada": {
        "costs": {"metal": 40, "energy": 175, "build": 1600},
        "provides": {"energy": 14},
        "storage": {"energy": 0.5},
    },
    "energy_converter_cortex": {
        "costs": {"metal": 1, "energy": 1250, "build": 2680},
        "provides": {"metal": 1, "energy": -70},
    },
    "energy_converter_armada": {
        "costs": {"metal": 1, "energy": 1150, "build": 2600},
        "provides": {"metal": 1, "energy": -70},
    },
    "construction_turret": {
        "costs": {"metal": 210, "energy": 3200, "build": 5300},
        "provides": {"construction_power": 200},
    },
}

RECLAIMABLES = {
    "tree": {"costs": {"metal": 0, "energy": 250, "build": 1000}},
    "fern": {"costs": {"metal": 0, "energy": 100, "build": 500}},
}
