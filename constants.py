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
    },
    "harvest_sell_discount": 0.7  # 90% of the normal sell price
}

# Action Penalties
ACTION_PENALTIES = {
    "plant": 5,
    "harvest": 5,
    "maintenance": 2
}

# Trading and Sabotage Rules
TRADING_RULES = {
    "trade_energy_cost": 10,
    "max_trade_amount": 50,
    "trade_fee_percentage": 0.1,
    "trade_penalty": 5,
    "order_expiration_days": 1  
}

SABOTAGE_RULES = {
    "sabotage_energy_cost": 40,
    "sabotage_money_cost": 30,
    "sabotage_success_rate": 0.7,
    "max_crops_damaged": 3,
    "damaged_crop_yield_factor": 0.5 
}