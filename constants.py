# Game Rules and Constants
GAME_RULES = {
    "total_days": 50,
    "starting_money": 100,
    "max_energy": 100,
    "energy_regen_per_day": 20,
    "energy_cost": {
        "plant": 20,
        "harvest": 30,
        "maintenance": 10
    },
    "crops": {
        "Corn": {"cost": 10, "growth_time": 5, "sell_price": 20},
        "Wheat": {"cost": 15, "growth_time": 7, "sell_price": 30},
        "Tomato": {"cost": 5, "growth_time": 3, "sell_price": 10}
    }
}

# Action Penalties
ACTION_PENALTIES = {
    "plant": 5,
    "harvest": 5,
    "maintenance": 2
}

# Other Constants
MAX_ACTIONS_PER_DAY = 3