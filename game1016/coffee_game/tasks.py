"""Original Kitchen24 tasks and their player-facing descriptions."""

TASKS = {
    "CoffeeSetupMug": {
        "id": "CoffeeSetupMug",
        "title": "Set the mug",
        "instruction": "Place the mug below the dispenser.",
        "description": "Pick up the mug, position it beneath the coffee machine, and release it.",
    },
    "CoffeePressButton": {
        "id": "CoffeePressButton",
        "title": "Start the machine",
        "instruction": "Activate the coffee machine, then move your hand away.",
        "description": "A short contact-control tutorial. The mug is already in place.",
    },
    "CoffeeServeMug": {
        "id": "CoffeeServeMug",
        "title": "Serve the mug",
        "instruction": "Move the mug onto the counter.",
        "description": "Lift the mug from the dispenser and release it on the nearby counter.",
    },
}

CONTROL_HZ = 20
HORIZONS = {"practice": None, "collection": 2400, "benchmark": 720}
