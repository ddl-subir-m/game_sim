import re
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
import autogen
import asyncio
import uvicorn
from typing import List, Dict
from dataclasses import dataclass
from constants import GAME_RULES, ACTION_PENALTIES, MAX_ACTIONS_PER_DAY
from dotenv import load_dotenv
import os

import json
from sse_starlette.sse import EventSourceResponse

from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

# Load environment variables
load_dotenv()

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Update this line to serve static files from a 'static' directory
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open("static/index.html", "r") as f:
        return f.read()

# Configure AutoGen for two different models
openai_api_key = os.getenv("OPENAI_API_KEY")
config_list_gpt4 = [{"model": "gpt-4o-mini", "api_key": openai_api_key}]
# config_list_gpt4 = [{"model": "gpt-3.5-turbo", "api_key": openai_api_key}]
config_list_gpt35 = [{"model": "gpt-3.5-turbo", "api_key": openai_api_key}]

competition_results = None
competition_running = False
simulation_task = None

@dataclass
class GameState:
    day: int = 1
    money: int = GAME_RULES["starting_money"]
    energy: int = GAME_RULES["max_energy"]
    crops: List[Dict] = None

    def __post_init__(self):
        if self.crops is None:
            self.crops = []

@dataclass
class ActionLog:
    day: int
    action: str
    details: str

def update_state(state: GameState, decision: str, action_log: List[ActionLog]):
    decision_parts = decision.split()
    action_number = decision_parts[0]
    action = decision_parts[1]
    crop_type = decision_parts[2] if len(decision_parts) > 2 else None

    if action_number == "1":  # Plant
        if crop_type in GAME_RULES["crops"] and state.money >= GAME_RULES["crops"][crop_type]["cost"] and state.energy >= GAME_RULES["energy_cost"]["plant"]:
            state.crops.append({"type": crop_type, "planted_at": state.day})
            state.money -= GAME_RULES["crops"][crop_type]["cost"]
            state.energy -= GAME_RULES["energy_cost"]["plant"]
            action_log.append(ActionLog(day=state.day, action="Plant", details=f"Planted {crop_type}"))
        else:
            state.energy = max(0, state.energy - ACTION_PENALTIES["plant"])
            action_log.append(ActionLog(day=state.day, action="Failed Plant", details=f"Attempted to plant {crop_type} but lacked resources. Energy penalty applied."))
    elif action_number == "2":  # Harvest
        harvestable_crops = [crop for crop in state.crops if state.day - crop["planted_at"] >= GAME_RULES["crops"][crop["type"]]["growth_time"]]
        if harvestable_crops and state.energy >= GAME_RULES["energy_cost"]["harvest"]:
            crop = harvestable_crops[0]
            state.crops.remove(crop)
            state.money += GAME_RULES["crops"][crop["type"]]["sell_price"]
            state.energy -= GAME_RULES["energy_cost"]["harvest"]
            action_log.append(ActionLog(day=state.day, action="Harvest", details=f"Harvested {crop['type']}"))
        else:
            state.energy = max(0, state.energy - ACTION_PENALTIES["harvest"])
            action_log.append(ActionLog(day=state.day, action="Failed Harvest", details="No harvestable crops or insufficient energy. Energy penalty applied."))
    elif action_number == "3":  # Maintenance (Wait)
        if state.energy >= GAME_RULES["energy_cost"]["maintenance"]:
            state.energy -= GAME_RULES["energy_cost"]["maintenance"]
            action_log.append(ActionLog(day=state.day, action="Maintenance", details="Performed farm maintenance"))
        else:
            state.energy = max(0, state.energy - ACTION_PENALTIES["maintenance"])
            action_log.append(ActionLog(day=state.day, action="Failed Maintenance", details="Insufficient energy for maintenance, rested instead. Small energy penalty applied."))

    # End of day updates
    state.day += 1
    state.energy = min(state.energy + GAME_RULES["energy_regen_per_day"], GAME_RULES["max_energy"])

# Create two assistant agents (our NPCs)
assistant_gpt4 = autogen.AssistantAgent(
    name="FarmerNPC_GPT4",
    system_message=f"""You are an experienced farmer NPC in a farming simulation game. 
    Make optimal choices based on the provided game rules and remaining time.
    Game Rules: {GAME_RULES}""",
    llm_config={"config_list": config_list_gpt4}
)

assistant_gpt35 = autogen.AssistantAgent(
    name="FarmerNPC_GPT35",
    system_message=f"""You are an experienced farmer NPC in a farming simulation game. 
    Make optimal choices based on the provided game rules and remaining time.
    Game Rules: {GAME_RULES}""",
    llm_config={"config_list": config_list_gpt35}
)

# Create the user proxy agent (to simulate the game state)
user_proxy = autogen.UserProxyAgent(
    name="GameState",
    human_input_mode="NEVER",
    max_consecutive_auto_reply=10,
    is_termination_msg=lambda x: x.get("content", "").rstrip().endswith("TERMINATE")
)

class GameState(BaseModel):
    money: int
    energy: float
    crops: list
    day: int

class ActionLog(BaseModel):
    day: int
    action: str
    details: str

class NPCState(BaseModel):
    state: GameState
    action_log: List[ActionLog]

class CompetitionResult(BaseModel):
    gpt4_state: NPCState
    gpt35_state: NPCState
    winner: str

async def make_decision(assistant, state: GameState, days_left: int):

    # Calculate harvestable crops
    harvestable_crops = [crop for crop in state.crops if state.day - crop["planted_at"] >= GAME_RULES["crops"][crop["type"]]["growth_time"]]
    
    # Calculate days until next harvest for each crop
    crops_info = []
    for crop in state.crops:
        days_until_harvest = max(0, GAME_RULES["crops"][crop["type"]]["growth_time"] - (state.day - crop["planted_at"]))
        crops_info.append(f"{crop['type']} (ready in {days_until_harvest} days)")

    message = f"""
Current farm state on day {state.day}:
- Money: {state.money}
- Energy: {state.energy}
- Crops: {crops_info}
- Harvestable crops: {[crop['type'] for crop in harvestable_crops]}
- Days left in the game: {days_left}

Game Rules:
{GAME_RULES}

Instructions:
1. Analyze the current situation based on the farm state and game rules.
2. Consider the best course of action, thinking through your decision step by step.
3. After your analysis, you MUST conclude with EXACTLY ONE of the following decisions:

   1 Plant Wheat
   1 Plant Corn
   1 Plant Tomato
   2 Harvest
   3 Maintenance

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

Example of a correct final part of your response:

Step 5: Based on the analysis, planting Tomato seems to be the most profitable choice.

Final Decision:
1 Plant Tomato

Failure to follow this format exactly will result in a default "Maintenance" action being taken.
"""
    
    response = await user_proxy.a_initiate_chat(assistant, message=message, max_turns=1)
    
    # Extract the content from the ChatResult object
    response_content = response.summary if isinstance(response, autogen.ChatResult) else str(response)
    
    # Use regex to find a decision in the format: number followed by action and optional crop
    pattern = r'(\d+)\.?\s+(Plant|Harvest|Wait|Maintenance)(?:\s+(\w+))?$'
    decision_match = re.findall(pattern, response_content, re.MULTILINE | re.IGNORECASE)
    
    if decision_match:
        last_match = decision_match[-1]
        action_number, action, crop = last_match
        crop = crop if crop else None
        return f"{action_number} {action} {crop}".strip()

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

async def run_competition():
    global simulation_task
    simulation_task = asyncio.current_task()
    gpt4_state = GameState(money=1000, energy=100, crops=[], day=1)
    gpt35_state = GameState(money=1000, energy=100, crops=[], day=1)
    gpt4_log = []
    gpt35_log = []

    for current_day in range(1, GAME_RULES["total_days"] + 1):
        if not competition_running:
            break
        
        days_left = GAME_RULES["total_days"] - current_day + 1
        
        # Run decisions for both models concurrently
        gpt35_decision, gpt4_decision = await asyncio.gather(
            make_decision(assistant_gpt35, gpt35_state, days_left),
            make_decision(assistant_gpt4, gpt4_state, days_left)
        )

        update_state(gpt35_state, gpt35_decision, gpt35_log)
        update_state(gpt4_state, gpt4_decision, gpt4_log)

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

    if competition_running:
        yield {
            "gpt35": {
                "day": "Final",
                "decision": "Competition finished",
                "money": gpt35_state.money,
                "energy": gpt35_state.energy,
                "crops": [{"type": crop["type"], "planted_at": crop["planted_at"]} for crop in gpt35_state.crops]
            },
            "gpt4": {
                "day": "Final",
                "decision": "Competition finished",
                "money": gpt4_state.money,
                "energy": gpt4_state.energy,
                "crops": [{"type": crop["type"], "planted_at": crop["planted_at"]} for crop in gpt4_state.crops]
            }
        }

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "ui":
        uvicorn.run(app, host="0.0.0.0", port=8000)
    else:
        asyncio.run(run_competition())
