import random
import re
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import autogen
import asyncio
import uvicorn
from typing import List, Dict, Optional
from constants import GAME_RULES, ACTION_PENALTIES, TRADING_RULES, SABOTAGE_RULES
from dotenv import load_dotenv
import os
import json

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open("static/index.html", "r") as f:
        return f.read()

openai_api_key = os.getenv("OPENAI_API_KEY")
config_list_gpt4 = [{"model": "gpt-4o-mini", "api_key": openai_api_key}]
# config_list_gpt4 = [{"model": "gpt-3.5-turbo", "api_key": openai_api_key}]
config_list_gpt35 = [{"model": "gpt-3.5-turbo", "api_key": openai_api_key}]

competition_running = False
simulation_task = None

class GameState(BaseModel):
    day: int = 1
    money: int = GAME_RULES["starting_money"]
    reserved_money: int = 0  # New field to track money reserved for buy orders
    energy: int = GAME_RULES["max_energy"]
    crops: List[Dict] = []
    harvested_crops: Dict[str, int] = {}
    pending_trades: List[Dict] = []
    buy_offers: List[Dict] = []
    sell_offers: List[Dict] = []

class ActionLog(BaseModel):
    day: int
    action: str
    details: str

def create_assistant(name, config_list):
    return autogen.AssistantAgent(
        name=name,
        system_message=f"""You are an experienced farmer NPC in a farming simulation game. 
        Make optimal choices based on the provided game rules and remaining time.
        Game Rules: {GAME_RULES}""",
        llm_config={"config_list": config_list}
    )

assistant_gpt4 = create_assistant("FarmerNPC_GPT4", config_list_gpt4)
assistant_gpt35 = create_assistant("FarmerNPC_GPT35", config_list_gpt35)

user_proxy = autogen.UserProxyAgent(
    name="GameState",
    human_input_mode="NEVER",
    max_consecutive_auto_reply=10,
    is_termination_msg=lambda x: x.get("content", "").rstrip().endswith("TERMINATE")
)

def plant_crop(state: GameState, crop_type: str, action_log: List[ActionLog]):
    if crop_type in GAME_RULES["crops"] and state.money >= GAME_RULES["crops"][crop_type]["cost"] and state.energy >= GAME_RULES["energy_cost"]["plant"]:
        state.crops.append({"type": crop_type, "planted_at": state.day})
        state.money -= GAME_RULES["crops"][crop_type]["cost"]
        state.energy -= GAME_RULES["energy_cost"]["plant"]
        action_log.append(ActionLog(day=state.day, action="Plant", details=f"Planted {crop_type}"))
    else:
        state.energy = max(0, state.energy - ACTION_PENALTIES["plant"])
        action_log.append(ActionLog(day=state.day, action="Failed Plant", details=f"Attempted to plant {crop_type} but lacked resources. Energy penalty applied."))

def harvest_crop(state: GameState, action_log: List[ActionLog]):
    harvestable_crops = [crop for crop in state.crops if state.day - crop["planted_at"] >= GAME_RULES["crops"][crop["type"]]["growth_time"]]
    
    if harvestable_crops and state.energy >= GAME_RULES["energy_cost"]["harvest"]:
        state.energy -= GAME_RULES["energy_cost"]["harvest"]
        
        # Get all pending sell orders
        pending_sell_orders = {trade["crop_type"]: trade["amount"] for trade in state.pending_trades if trade["type"] == "sell"}
        
        total_harvested = 0
        total_money_earned = 0
        harvested_counts = {crop_type: 0 for crop_type in GAME_RULES["crops"]}
        
        for crop in harvestable_crops[:]:
            crop_type = crop["type"]
            # Skip this crop if we've harvested enough to fulfill pending sell orders
            if harvested_counts[crop_type] < pending_sell_orders.get(crop_type, 0):
                harvested_counts[crop_type] += 1
                continue
            
            state.crops.remove(crop)
            
            # Apply yield multiplier for damaged crops
            yield_multiplier = SABOTAGE_RULES["damaged_crop_yield_factor"] if crop.get("damaged", False) else 1
            harvest_amount = int(yield_multiplier * 1)  # Assuming 1 is the normal yield
            
            # Apply discount to sell price
            discounted_price = GAME_RULES["crops"][crop_type]["sell_price"] * GAME_RULES["harvest_sell_discount"]
            money_earned = harvest_amount * discounted_price
            
            total_harvested += harvest_amount
            total_money_earned += money_earned
            state.money += money_earned
        
        action_log.append(ActionLog(day=state.day, action="Harvest", details=f"Harvested {total_harvested} crops, earned {total_money_earned:.2f} money"))
    else:
        state.energy = max(0, state.energy - ACTION_PENALTIES["harvest"])
        action_log.append(ActionLog(day=state.day, action="Failed Harvest", details="No harvestable crops or insufficient energy. Energy penalty applied."))

def perform_maintenance(state: GameState, action_log: List[ActionLog]):
    if state.energy >= GAME_RULES["energy_cost"]["maintenance"]:
        state.energy -= GAME_RULES["energy_cost"]["maintenance"]
        action_log.append(ActionLog(day=state.day, action="Maintenance", details="Performed farm maintenance"))
    else:
        state.energy = max(0, state.energy - ACTION_PENALTIES["maintenance"])
        action_log.append(ActionLog(day=state.day, action="Failed Maintenance", details="Insufficient energy for maintenance, rested instead. Small energy penalty applied."))

def sell_crops(state: GameState, other_state: GameState, crop_type: str, amount: int, action_log: List[ActionLog]):
    if state.energy >= TRADING_RULES["trade_energy_cost"]:
        state.energy -= TRADING_RULES["trade_energy_cost"]
        
        # Initialize the crop_type in harvested_crops if it doesn't exist
        if crop_type not in state.harvested_crops:
            state.harvested_crops[crop_type] = 0
        
        available_harvested = state.harvested_crops.get(crop_type, 0)
        harvestable_crops = [crop for crop in state.crops if state.day - crop["planted_at"] >= GAME_RULES["crops"][crop["type"]]["growth_time"] and crop["type"] == crop_type]
        
        if available_harvested + len(harvestable_crops) >= amount:
            trade_value = GAME_RULES["crops"][crop_type]["sell_price"] * amount
            trade_fee = trade_value * TRADING_RULES["trade_fee_percentage"]
            
            # Calculate how much to sell from harvested crops
            from_harvested = min(amount, state.harvested_crops[crop_type])
            to_harvest = amount - from_harvested
            
            # Update the state
            state.harvested_crops[crop_type] -= from_harvested
            if state.harvested_crops[crop_type] == 0:
                del state.harvested_crops[crop_type]
            
            for _ in range(to_harvest):
                crop = next(crop for crop in state.crops if crop["type"] == crop_type and state.day - crop["planted_at"] >= GAME_RULES["crops"][crop["type"]]["growth_time"])
                state.crops.remove(crop)
            
            # Create a pending trade
            pending_trade = {
                "type": "sell",
                "crop_type": crop_type,
                "amount": amount,
                "value": trade_value,
                "fee": trade_fee,
                "expiration": state.day + TRADING_RULES["order_expiration_days"]
            }
            state.pending_trades.append(pending_trade)
            
            action_log.append(ActionLog(day=state.day, action="Offer to Sell", details=f"Offered to sell {amount} {crop_type} for {trade_value - trade_fee} money"))
            
            # Check if there's a matching buy offer
            matching_buy = next((trade for trade in other_state.pending_trades if trade["type"] == "buy" and trade["crop_type"] == crop_type and trade["amount"] >= amount), None)
            if matching_buy:
                complete_trade(other_state, state, pending_trade, action_log)
            else:
                # If no matching buy offer, create a sell offer
                other_state.sell_offers.append(pending_trade)
        else:
            state.energy = max(0, state.energy - TRADING_RULES["trade_penalty"])
            action_log.append(ActionLog(day=state.day, action="Failed Sell", details=f"Insufficient crops to sell. Energy penalty of {TRADING_RULES['trade_penalty']} applied."))
    else:
        action_log.append(ActionLog(day=state.day, action="Failed Sell", details="Insufficient energy for selling"))

def buy_crops(state: GameState, other_state: GameState, crop_type: str, amount: int, action_log: List[ActionLog]):
    # Initialize crop_type in relevant dictionaries if they don't exist
    if crop_type not in state.crops:
        state.crops[crop_type] = 0
    if crop_type not in state.harvested_crops:
        state.harvested_crops[crop_type] = 0
    if crop_type not in other_state.harvested_crops:
        other_state.harvested_crops[crop_type] = 0

    if state.energy >= TRADING_RULES["trade_energy_cost"]:
        state.energy -= TRADING_RULES["trade_energy_cost"]
        
        trade_value = GAME_RULES["crops"][crop_type]["sell_price"] * amount
        
        if state.money - state.reserved_money >= trade_value:
            # Reserve money for the buy order
            state.reserved_money += trade_value
            
            # Create a pending trade
            pending_trade = {
                "type": "buy",
                "crop_type": crop_type,
                "amount": amount,
                "value": trade_value,
                "expiration": state.day + TRADING_RULES["order_expiration_days"]
            }
            state.pending_trades.append(pending_trade)
            
            action_log.append(ActionLog(day=state.day, action="Offer to Buy", details=f"Offered to buy {amount} {crop_type} for {trade_value} money"))
            
            # Check if there's a matching sell offer
            matching_sell = next((offer for offer in other_state.sell_offers if offer["crop_type"] == crop_type and offer["amount"] >= amount), None)
            if matching_sell:
                complete_trade(state, other_state, pending_trade, action_log)
            else:
                # If no matching sell offer, create a buy offer
                other_state.buy_offers.append(pending_trade)
        else:
            state.energy = max(0, state.energy - TRADING_RULES["trade_penalty"])
            action_log.append(ActionLog(day=state.day, action="Failed Buy", details=f"Insufficient funds for buying. Energy penalty of {TRADING_RULES['trade_penalty']} applied."))
    else:
        action_log.append(ActionLog(day=state.day, action="Failed Buy", details="Insufficient energy for buying"))

def complete_trade(buyer_state: GameState, seller_state: GameState, trade: Dict, action_log: List[ActionLog]):
    buyer_pending_trade = next((t for t in buyer_state.pending_trades if t["type"] == "buy" and t["crop_type"] == trade["crop_type"] and t["amount"] == trade["amount"]), None)
    seller_pending_trade = next((t for t in seller_state.pending_trades if t["type"] == "sell" and t["crop_type"] == trade["crop_type"] and t["amount"] == trade["amount"]), None)
    
    if buyer_pending_trade and seller_pending_trade:
        # Update money for both farms
        buyer_state.reserved_money -= trade["value"]  # Release reserved money
        buyer_state.money -= trade["value"]
        seller_state.money += trade["value"] - seller_pending_trade["fee"]
        
        # Transfer crops
        for _ in range(trade["amount"]):
            buyer_state.crops.append({"type": trade["crop_type"], "planted_at": buyer_state.day})
        
        # Remove pending trades
        buyer_state.pending_trades.remove(buyer_pending_trade)
        seller_state.pending_trades.remove(seller_pending_trade)
        
        # Remove offers if they exist
        if buyer_pending_trade in seller_state.buy_offers:
            seller_state.buy_offers.remove(buyer_pending_trade)
        if seller_pending_trade in buyer_state.sell_offers:
            buyer_state.sell_offers.remove(seller_pending_trade)
        
        action_log.append(ActionLog(day=buyer_state.day, action="Complete Trade", details=f"Completed trade of {trade['amount']} {trade['crop_type']} for {trade['value']} money"))
    else:
        action_log.append(ActionLog(day=buyer_state.day, action="Failed Trade Completion", details="Matching pending trades not found"))

def attempt_sabotage(state: GameState, other_state: GameState, action_log: List[ActionLog]):
    if state.energy >= SABOTAGE_RULES["sabotage_energy_cost"] and state.money >= SABOTAGE_RULES["sabotage_money_cost"]:
        state.energy -= SABOTAGE_RULES["sabotage_energy_cost"]
        state.money -= SABOTAGE_RULES["sabotage_money_cost"]
        
        if random.random() < SABOTAGE_RULES["sabotage_success_rate"]:
            # Successful sabotage
            damaged_crops = random.sample(other_state.crops, k=min(len(other_state.crops), SABOTAGE_RULES["max_crops_damaged"]))
            for crop in damaged_crops:
                crop["damaged"] = True
            action_log.append(ActionLog(day=state.day, action="Sabotage", details="Successfully sabotaged the other farm"))
        else:
            action_log.append(ActionLog(day=state.day, action="Failed Sabotage", details="Sabotage attempt failed"))
    else:
        action_log.append(ActionLog(day=state.day, action="Failed Sabotage", details="Insufficient energy or money for sabotage attempt"))

def update_state(state: GameState, other_state: GameState, decision: str, action_log: List[ActionLog]):
    decision_parts = decision.split()
    action_number = decision_parts[0]
    action = decision_parts[1]
    
    if action_number == "1":
        crop_type = decision_parts[2]
        plant_crop(state, crop_type, action_log)
    elif action_number == "2":
        harvest_crop(state, action_log)
    elif action_number == "3":
        perform_maintenance(state, action_log)
    elif action_number == "4":
        crop_type = decision_parts[2]
        amount = int(decision_parts[3])
        sell_crops(state, other_state, crop_type, amount, action_log)
    elif action_number == "5":
        crop_type = decision_parts[2]
        amount = int(decision_parts[3])
        buy_crops(state, other_state, crop_type, amount, action_log)
    elif action_number == "6":
        attempt_sabotage(state, other_state, action_log)

    # Handle order expiration
    expired_buy_orders = [trade for trade in state.pending_trades if trade["type"] == "buy" and trade["expiration"] <= state.day]
    for expired_order in expired_buy_orders:
        state.reserved_money -= expired_order["value"]
        state.pending_trades.remove(expired_order)
        if expired_order in other_state.buy_offers:
            other_state.buy_offers.remove(expired_order)
        action_log.append(ActionLog(day=state.day, action="Buy Order Expired", details=f"Buy order for {expired_order['amount']} {expired_order['crop_type']} expired. {expired_order['value']} money returned."))

    state.sell_offers = [offer for offer in state.sell_offers if offer["expiration"] > state.day]

    # Return expired sell offers to harvested crops
    for expired_offer in [offer for offer in state.sell_offers if offer["expiration"] <= state.day]:
        state.harvested_crops[expired_offer["crop_type"]] = state.harvested_crops.get(expired_offer["crop_type"], 0) + expired_offer["amount"]

    state.day += 1
    state.energy = min(state.energy + GAME_RULES["energy_regen_per_day"], GAME_RULES["max_energy"])

async def make_decision(assistant, state: GameState, other_state: GameState, days_left: int):
    # Calculate harvestable crops
    harvestable_crops = [crop for crop in state.crops if state.day - crop["planted_at"] >= GAME_RULES["crops"][crop["type"]]["growth_time"]]
    
    # Calculate days until next harvest for each crop
    crops_info = []
    for crop in state.crops:
        days_until_harvest = max(0, GAME_RULES["crops"][crop["type"]]["growth_time"] - (state.day - crop["planted_at"]))
        crops_info.append(f"{crop['type']} (ready in {days_until_harvest} days)")

    # Prepare order book information
    buy_offers = [f"{offer['crop_type']}: {offer['amount']} @ {offer['value']/offer['amount']:.2f}" for offer in other_state.buy_offers]
    sell_offers = [f"{offer['crop_type']}: {offer['amount']} @ {offer['value']/offer['amount']:.2f}" for offer in other_state.sell_offers]

    message = f"""
Current farm state on day {state.day}:
- Money: {state.money}
- Energy: {state.energy}
- Crops: {crops_info}
- Harvestable crops: {[crop['type'] for crop in harvestable_crops]}
- Days left in the game: {days_left}

Order Book:
Buy Offers: {buy_offers if buy_offers else "None"}
Sell Offers: {sell_offers if sell_offers else "None"}

Game Rules:
{GAME_RULES}

Trading Rules:
{TRADING_RULES}

Sabotage Rules:
{SABOTAGE_RULES}

Instructions:
1. Analyze the current situation based on the farm state, order book, and game rules.
2. Consider the best course of action, thinking through your decision step by step.
3. After your analysis, you MUST conclude with EXACTLY ONE of the following decisions:

   1 Plant Wheat
   1 Plant Corn
   1 Plant Tomato
   2 Harvest
   3 Maintenance
   5 Buy [crop_type] [amount]
   6 Sabotage

Your response MUST strictly adhere to this format:
```
[Your step-by-step analysis here]

Final Decision:
[ONLY ONE of the exact options listed above]
```

Critically important rules:
- Your response MUST end with "Final Decision:" followed by ONLY ONE of the exact options listed above.
- Do not include any other text, numbers, or explanations after the Final Decision line.
- The final decision must be word-for-word one of the options provided, including the number.
- Ensure there is an empty line before "Final Decision:".
- Do not use any punctuation or additional formatting in the Final Decision line.
- Only choose "2 Harvest" if there are harvestable crops available.
- Choose "3 Maintenance" if you want to wait or perform maintenance (it serves as both).
- For selling, specify the crop type and amount (e.g., "4 Sell Wheat 2"). Only harvestable crops can be sold.
- For buying, specify the crop type and amount (e.g., "5 Buy Corn 3"). Crops can be bought only if there is sufficient money.
- Attempting to violate these rules will result in a failed action and a penalty.
- Sabotage is a risky action that can potentially damage the other farm's crops.

Example of correct final parts of your response:

Example 1:
Step 5: Based on the analysis, planting Tomato seems to be the most profitable choice.

Final Decision:
1 Plant Tomato

Example 2:
Step 5: Selling some excess Wheat could be beneficial.

Final Decision:
4 Sell Wheat 2

Example 3:
Step 5: Buying some Corn might diversify our crop portfolio.

Final Decision:
5 Buy Corn 3

Example 4:
Step 5: Attempting sabotage might give us an edge, but it's risky.

Final Decision:
6 Sabotage

Failure to follow this format exactly will result in a default "Maintenance" action being taken.
"""
    
    response = await user_proxy.a_initiate_chat(assistant, message=message, max_turns=1)
    
    # Extract the content from the ChatResult object
    response_content = response.summary if isinstance(response, autogen.ChatResult) else str(response)
    
    # Use regex to find a decision in the format: number followed by action and optional crop
    pattern = r'(\d+)\.?\s+(Plant|Harvest|Wait|Maintenance|Sell|Buy|Sabotage)(?:\s+(\w+))?(?:\s+(\d+))?$'
    decision_match = re.findall(pattern, response_content, re.MULTILINE | re.IGNORECASE)
    
    if decision_match:
        last_match = decision_match[-1]
        action_number, action, crop, amount = last_match
        crop = crop if crop else None
        amount = amount if amount else None
        return f"{action_number} {action} {crop} {amount}".strip()

    # If no valid decision format is found, default to waiting
    return "3 Maintenance"

@app.get("/stream-competition")
async def stream_competition(request: Request):
    global competition_running
    if competition_running:
        return JSONResponse(content={"error": "Competition already running"}, status_code=400)
    
    competition_running = True

    async def event_generator():
        try:
            async for state in run_competition():
                if await request.is_disconnected():
                    break
                yield f"data: {json.dumps(state)}\n\n"
                await asyncio.sleep(0.1)  # Small delay to allow for interruption
        finally:
            global competition_running
            competition_running = False

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/stop-competition")
async def stop_competition():
    global competition_running, simulation_task
    if competition_running:
        competition_running = False
        if simulation_task:
            await simulation_task
            simulation_task = None
    return JSONResponse(content={"message": "Competition stopped"})

def clear_order_book(state: GameState):
    # Return reserved money from buy orders
    state.money += state.reserved_money
    state.reserved_money = 0

    # Return crops from sell orders to harvested_crops
    for trade in state.pending_trades:
        if trade["type"] == "sell":
            state.harvested_crops[trade["crop_type"]] = state.harvested_crops.get(trade["crop_type"], 0) + trade["amount"]

    # Clear pending trades and offers
    state.pending_trades.clear()
    state.buy_offers.clear()
    state.sell_offers.clear()

async def run_competition():
    global simulation_task
    simulation_task = asyncio.current_task()
    gpt4_state = GameState(money=GAME_RULES["starting_money"], energy=GAME_RULES["max_energy"], crops=[], day=1)
    gpt35_state = GameState(money=GAME_RULES["starting_money"], energy=GAME_RULES["max_energy"], crops=[], day=1)
    gpt4_log = []
    gpt35_log = []

    for current_day in range(1, GAME_RULES["total_days"] + 1):
        if not competition_running:
            break
        
        days_left = GAME_RULES["total_days"] - current_day + 1
        
        # Run decisions for both models concurrently
        gpt35_decision, gpt4_decision = await asyncio.gather(
            make_decision(assistant_gpt35, gpt35_state, gpt4_state, days_left),
            make_decision(assistant_gpt4, gpt4_state, gpt35_state, days_left)
        )
        update_state(gpt35_state, gpt4_state, gpt35_decision, gpt35_log)
        update_state(gpt4_state, gpt35_state, gpt4_decision, gpt4_log)
        
        yield {
            "gpt35": {
                "day": current_day,
                "decision": gpt35_decision,
                "money": gpt35_state.money,
                "energy": gpt35_state.energy,
                "crops": [{"type": crop["type"], "planted_at": crop["planted_at"]} for crop in gpt35_state.crops]
            },
            "gpt4": {
                "day": current_day,
                "decision": gpt4_decision,
                "money": gpt4_state.money,
                "energy": gpt4_state.energy,
                "crops": [{"type": crop["type"], "planted_at": crop["planted_at"]} for crop in gpt4_state.crops]
            }
        }

        await asyncio.sleep(0.1)  # Small delay to prevent blocking

    # Clear order books on final day
    clear_order_book(gpt35_state)
    clear_order_book(gpt4_state)

    if competition_running:
        yield {
            "gpt35": {
                "day": "Final",
                "decision": "Competition finished",
                "money": gpt35_state.money,
                "energy": gpt35_state.energy,
                "crops": [{"type": crop["type"], "planted_at": crop["planted_at"]} for crop in gpt35_state.crops],
                "harvested_crops": gpt35_state.harvested_crops
            },
            "gpt4": {
                "day": "Final",
                "decision": "Competition finished",
                "money": gpt4_state.money,
                "energy": gpt4_state.energy,
                "crops": [{"type": crop["type"], "planted_at": crop["planted_at"]} for crop in gpt4_state.crops],
                "harvested_crops": gpt4_state.harvested_crops
            }
        }

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "ui":
        uvicorn.run(app, host="0.0.0.0", port=8000)
    else:
        asyncio.run(run_competition())
