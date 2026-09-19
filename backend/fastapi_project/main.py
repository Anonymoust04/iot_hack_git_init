from pydantic import BaseModel
import asyncio
import random
from datetime import datetime
import time
from fastapi import FastAPI, HTTPException, Request, status
import httpx

app = FastAPI(title="Parking Simulator Backend")

import db_hook  # MySQL: login/roles, dashboard API, webhook history (see db_hook.py)
import sys
from pathlib import Path
_backend_dir = str(Path(__file__).resolve().parents[1])
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
try:
    from app.config import get_settings
except Exception:
    class DummySettings:
        sim_timeout_seconds = 10.0
    def get_settings():
        return DummySettings()

db_hook.setup(app)

# Configuration constants
SIMULATOR_URL = "http://127.0.0.1:9898"
SIMULATOR_EMAIL = "admin"
SIMULATOR_PASSWORD = "admin"

SIMULATOR_TOKEN: str | None = None
_token_lock = asyncio.Lock()

class LoginRequest(BaseModel):
    email: str = "admin"
    password: str = "admin"


@app.post("/auth/login")
async def test_login(body: LoginRequest):
    """Test the simulator login with arbitrary credentials."""
    token = await api_login(body.email, body.password)
    return {
        "status": "ok",
        "email": body.email,
        "token_preview": f"{token[:25]}...",
        "token_length": len(token),
    }

async def api_login(email: str | None = None, password: str | None = None) -> str:
    """POST /api/v1/auth/login -> {"token": "..."} and cache it."""
    global SIMULATOR_TOKEN
    email = email or SIMULATOR_EMAIL
    password = password or SIMULATOR_PASSWORD

    url = f"{SIMULATOR_URL}/api/v1/auth/login"
    payload = {"email": email, "password": password}

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json=payload)

    if not resp.is_success:
        raise HTTPException(resp.status_code, f"Login failed: {resp.text}")

    token = resp.json().get("token")
    if not token:
        raise HTTPException(502, "Login response had no token")

    SIMULATOR_TOKEN = token
    print(f"[AUTH] Logged in as {email}, token cached ({token[:20]}...)")
    return token

async def get_token(force: bool = False) -> str:
    """Return a cached token, logging in only when missing or forced."""
    global SIMULATOR_TOKEN
    async with _token_lock:
        if force or not SIMULATOR_TOKEN:
            await api_login()
        return SIMULATOR_TOKEN


# Valid parking spot range: S1 to S30
VALID_PARKING_SPOTS = [f"S{i}" for i in range(1, 31)]

# In-memory storage state
webhook_events = []
recent_car_arrivals = []
active_cars = {}     # car_plate -> { entry_time, car_type, assigned_spot, planned_duration, charged }
charged_cars = set()  # set of car_plates to prevent double charging penalties

# Thread-safe parking spot hash table: spot_name -> True (free) / False (occupied).
# All reads and writes MUST be performed while holding spot_lock to avoid race conditions.
parking_spots: dict[str, bool] = {f"S{i}": True for i in range(1, 31)}

# Parallel Dual Queues & Dedicated Gate A / Gate B Queues
entry_queue = asyncio.Queue()
exit_queue = asyncio.Queue()
gate_a_queue = asyncio.Queue()  # Dedicated Gate A Queue
gate_b_queue = asyncio.Queue()  # Dedicated Gate B Queue
spot_lock = asyncio.Lock()      # Lock to ensure thread-safe spot selection


async def entry_worker():
    """Background worker for ENTRANCE processing (FCFS among entry cars)."""
    print("[ENTRY WORKER] Entrance Queue Worker started.")
    while True:
        data = await entry_queue.get()
        try:
            await process_car_entry(data)
        except Exception as e:
            print(f"[ENTRY WORKER ERROR] {e}")
        finally:
            entry_queue.task_done()


async def exit_worker():
    """Background worker for EXIT processing (FCFS among exit cars)."""
    print("[EXIT WORKER] Exit Queue Worker started.")
    while True:
        data = await exit_queue.get()
        try:
            await process_car_exit(data)
        except Exception as e:
            print(f"[EXIT WORKER ERROR] {e}")
        finally:
            exit_queue.task_done()


async def gate_a_worker():
    """Dedicated background worker/thread controlling Gate A opening & closing when a car arrives."""
    print("[GATE A WORKER] Dedicated Gate A Controller Worker started.")
    while True:
        event = await gate_a_queue.get()
        car_plate = event.get("car_plate", "")
        raw_gate = event.get("gate_name", "GateA")
        # Ensure we always target the actual barrier gate "GateA" instead of "EntrySpot"
        destination = event.get("destination", "leavepark")
        gate_name = raw_gate
        
        print(f"[GATE A WORKER] Car arrival notification received for car {car_plate}. Opening gate {gate_name}...")
        try:
            # 1. Open Gate A
            await api_open_barrier_gate(gate_name)

            # await asyncio.sleep(2.0)
            
            # 2. Direct car to assigned spot or leavepark
            print(f"[GATE A WORKER] Guiding car {car_plate} to destination {destination}...")
            await api_send_car_to_destination(car_plate, destination)

            # 3. Wait for car to pass through
            await asyncio.sleep(2.0)
            
            # 4. Close Gate A ONLY if no remaining tasks in gate_a_queue
            if gate_a_queue.empty():
                print(f"[GATE A WORKER] No remaining tasks in Gate A queue. Closing entrance gate {gate_name}...")
                await api_close_barrier_gate(gate_name)
            else:
                print(f"[GATE A WORKER] {gate_a_queue.qsize()} car(s) remaining in Gate A queue. Keeping gate {gate_name} open...")
        except Exception as e:
            print(f"[GATE A WORKER ERROR] Failed to operate gate {gate_name} for {car_plate}: {e}")
        finally:
            gate_a_queue.task_done()


async def gate_b_worker():
    """Dedicated background worker/thread controlling Gate B opening & closing after payment notification."""
    print("[GATE B WORKER] Dedicated Gate B Controller Worker started.")
    while True:
        event = await gate_b_queue.get()
        car_plate = event.get("car_plate", "")
        gate_name = event.get("gate_name", "GateB")
        
        print(f"[GATE B WORKER] Payment notification received for car {car_plate}. Opening gate {gate_name}...")
        try:
            # 1. Open Gate B
            await api_open_barrier_gate(gate_name)
            
            # 2. Guide car to leave
            print(f"[GATE B WORKER] Guiding car {car_plate} to leave park...")
            await api_send_car_to_destination(car_plate, "leavepark")
            
            # 3. Wait 3 seconds for car to pass through
            print(f"[GATE B WORKER] Waiting 3s for car {car_plate} to clear gate {gate_name}...")
            await asyncio.sleep(3.0)
            
            # 4. Close Gate B (returns to default closed state)
            print(f"[GATE B WORKER] Closing exit gate {gate_name}...")
            await api_close_barrier_gate(gate_name)
            
            # 5. Remove departed car from active_cars
            active_cars.pop(car_plate, None)
            print(f"[GATE B WORKER] Car {car_plate} departed and removed from active_cars.")
        except Exception as e:
            print(f"[GATE B WORKER ERROR] Failed to operate gate {gate_name} for {car_plate}: {e}")
        finally:
            gate_b_queue.task_done()


@app.on_event("startup")
async def startup_event():
    """Start Entrance, Exit, dedicated Gate A, and dedicated Gate B background workers on application startup."""
    asyncio.create_task(entry_worker())
    asyncio.create_task(exit_worker())
    asyncio.create_task(gate_a_worker())
    asyncio.create_task(gate_b_worker())
    try:
        print("[STARTUP] Ensuring GateA (Entrance Gate) and GateB (Exit Gate) are CLOSED by default...")
        await api_close_barrier_gate("GateA")
        await api_close_barrier_gate("GateB")
    except Exception as e:
        print(f"[STARTUP NOTE] Could not close gates on startup: {e}")




# =====================================================================
# 3. FASTAPI CONTROLLER LAYER (HTTP Routes)
# =====================================================================

@app.post("/webhook")
@app.post("/webhook.php")
async def webhook(request: Request):
    """Receive incoming events from the Parking Simulator and route to Entrance or Exit queue."""
    data = await request.json()
    await db_hook.record(request)  # also store it in MySQL (background, never blocks)
    
    # Print all incoming events from webhook
    print(f"[WEBHOOK EVENT RECEIVED] {data}")
    
    # Store raw event log (cap at 100)
    webhook_events.append(data)
    if len(webhook_events) > 100:
        webhook_events.pop(0)

    spot_type = data.get("SpotType", "")
    spot_name = data.get("SpotName", "")
    direction = data.get("Direction", "")

    # --- Ground-truth sync: update parking_spots hash table from simulator events ---
    # Park/CarIn  => simulator confirms car has physically parked in a spot
    # Park/CarOut => simulator confirms car has physically left a spot
    car_plate = data.get("CarPlateNumber", "")
    if spot_type == "Park" and spot_name and spot_name in parking_spots:
        async with spot_lock:
            if direction == "CarIn":
                parking_spots[spot_name] = False  # now occupied
                if car_plate and car_plate in active_cars:
                    active_cars[car_plate]["assigned_spot"] = spot_name
                print(f"[SPOT SYNC] {spot_name} marked OCCUPIED (Park/CarIn from simulator for {car_plate})")
            elif direction == "CarOut":
                parking_spots[spot_name] = True   # now free
                for p, info in list(active_cars.items()):
                    if info.get("assigned_spot") == spot_name or p == car_plate:
                        info["assigned_spot"] = None
                print(f"[SPOT SYNC] {spot_name} marked FREE (Park/CarOut from simulator)")

    # When car leaves the exit spot, it has exited the car park
    if (spot_type == "ExitSpot" or (spot_name and spot_name.upper().startswith("EXIT"))) and direction == "CarOut":
        if car_plate:
            active_cars.pop(car_plate, None)
            print(f"[EXIT SYNC] Car {car_plate} left ExitSpot and removed from active_cars.")

    # Route to parallel queues based on event type
    if spot_type == "EntrySpot" or (spot_name and spot_name.upper().startswith("ENTRY")):
        await entry_queue.put(data)
    elif spot_type == "ExitSpot" or (spot_name and spot_name.upper().startswith("EXIT")):
        await exit_queue.put(data)

    return {
        "status": "queued",
        "entry_queue_size": entry_queue.qsize(),
        "exit_queue_size": exit_queue.qsize(),
        "gate_a_queue_size": gate_a_queue.qsize(),
        "gate_b_queue_size": gate_b_queue.qsize(),
        "free_spots": sum(1 for v in parking_spots.values() if v),
    }

async def call_simulator_api(
    endpoint: str,
    method: str = "GET",
    params: dict = None,
    json_data: dict = None,
):
    """Low-level HTTP wrapper to call the simulator API asynchronously."""
    token = await get_token()
    url = f"{SIMULATOR_URL}/api/v1/{endpoint.lstrip('/')}"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        async with httpx.AsyncClient(timeout=get_settings().sim_timeout_seconds) as client:
            response = await client.request(
                method=method,
                url=url,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                json=json_data,
            )
            # If token expired or unauthorized, force refresh token and retry once
            if response.status_code == 401:
                print(f"[AUTH REFRESH] 401 received for {endpoint}, refreshing simulator token...")
                token = await get_token(force=True)
                headers = {"Authorization": f"Bearer {token}"}
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    json=json_data,
                )

            if response.is_success:
                try:
                    return response.json()
                except Exception:
                    return {"status": "success", "text": response.text}
            else:
                print(f"[API Error] {method} {endpoint} returned status {response.status_code}: {response.text}")
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Simulator API error: {response.text}"
                )
    except httpx.RequestError as exc:
        print(f"[Network Error] Could not connect to simulator at {url}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not connect to Parking Simulator at {SIMULATOR_URL}. Is the simulator running?"
        )



async def api_get_available_spots() -> list:
    """Fetch all unoccupied parking spots from list-parking-spots API call and return available spot objects."""
    response = await call_simulator_api("list-parking-spots")
    
    # Extract spot array from response (list or wrapped in dict key)
    spots_list = []
    if isinstance(response, list):
        spots_list = response
    elif isinstance(response, dict):
        spots_list = response.get("spots") or response.get("data") or response.get("items") or []

    available_spots = []
    for s in spots_list:
        if isinstance(s, dict):
            spot_name = s.get("name") or s.get("spotName") or s.get("Name") or s.get("SpotName")
            is_occ = (
                s.get("isOccupied") is True or 
                s.get("IsOccupied") is True or 
                s.get("occupied") is True or 
                s.get("Occupied") is True or 
                bool(s.get("carPlateNumber")) or 
                bool(s.get("CarPlateNumber")) or 
                bool(s.get("currentCar"))
            )
            
            # Check if spot is unoccupied
            if not is_occ:
                # Include spot name if inside S1..S30 range (or default if no filter)
                if not spot_name or spot_name in VALID_PARKING_SPOTS:
                    available_spots.append(s)

    # Fallback if simulator returned empty or static mock
    if not available_spots:
        assigned_spots = {info.get("assigned_spot") for info in active_cars.values() if info.get("assigned_spot")}
        free_spot_names = [spot for spot in VALID_PARKING_SPOTS if spot not in assigned_spots]
        available_spots = [{"name": name} for name in free_spot_names]

    free_names = [s.get("name") or s.get("spotName") or s.get("Name") for s in available_spots]
    print(f"[API] Available parking spots from list-parking-spots ({len(free_names)} total): {free_names}")

    return available_spots



async def api_open_barrier_gate(gate_name: str):
    """Open a specified barrier gate."""
    return await call_simulator_api(f"barrier-gates/{gate_name}/open", method="POST")


async def api_close_barrier_gate(gate_name: str):
    """Close a specified barrier gate."""
    return await call_simulator_api(f"barrier-gates/{gate_name}/close", method="POST")


async def api_send_car_to_destination(car_name: str, destination: str):
    """Direct a car to a specified parking spot, exit, or leavepark."""
    return await call_simulator_api(f"car/{car_name}/goto/{destination}", method="POST")


async def api_charge_car(car_name: str, parking_cost: float, charging_cost: float):
    """Send payment / charging request for a car."""
    params = {
        "parkingCost": parking_cost,
        "chargingCost": charging_cost,
    }
    return await call_simulator_api(f"car/{car_name}/charge", method="POST", params=params)


async def auto_close_gate_after_delay(gate_name: str, delay: float = 5.0):
    """Wait for the car to pass through the gate, then close the barrier."""
    await asyncio.sleep(delay)
    try:
        print(f"[AUTOMATION] Closing barrier gate {gate_name} after car passed...")
        await api_close_barrier_gate(gate_name)
    except Exception as e:
        print(f"[AUTOMATION ERROR] Failed to auto-close gate {gate_name}: {e}")


# =====================================================================
# 2. INTERNAL BUSINESS LOGIC LAYER (Automation Workflow)
# =====================================================================

async def process_car_entry(data: dict):
    """Internal logic to handle a car arriving at an entry gate."""
    car_plate = data.get("CarPlateNumber", "")
    car_type = data.get("CarType", "Normal")
    spot_name = data.get("SpotName", "")
    spot_type = data.get("SpotType", "")
    direction = data.get("Direction", "")
    planned_duration = float(data.get("PlannedParkingDurationInMinutes", 1) or 1)
    server_time = data.get("ServerDateTime", "")

    # Record arrival history
    arrival_info = {
        "car_plate": car_plate,
        "car_type": car_type,
        "spot_name": spot_name,
        "spot_type": spot_type,
        "direction": direction,
        "parking_duration": planned_duration,
        "event_time": server_time,
        "raw_event": data,
    }
    recent_car_arrivals.append(arrival_info)
    if len(recent_car_arrivals) > 50:
        recent_car_arrivals.pop(0)

    print(f"[ENTRY LOGIC] Car {car_plate} ({car_type}) arrived at entry {spot_name}.")

    try:
        # Brief settling delay: allow any in-flight Park/CarIn ground-truth events
        # from the simulator to update parking_spots before we read from it.
        # This prevents assigning a spot that the simulator is still confirming as occupied.
        ENTRY_ASSIGNMENT_DELAY = 0.75  # seconds — tune lower/higher based on simulator speed
        print(f"[ENTRY LOGIC] Waiting {ENTRY_ASSIGNMENT_DELAY}s for parking_spots to settle before assigning for {car_plate}...")
        await asyncio.sleep(ENTRY_ASSIGNMENT_DELAY)

        # Step 1: Thread-safe spot selection from local parking_spots hash table
        async with spot_lock:
            # Derive free spots directly from the hash table — always up-to-date, no API call needed
            free_spots = [s for s, free in parking_spots.items() if free]
            print(f"[ENTRY LOGIC] Free spots from hash table ({len(free_spots)}): {free_spots}")

            if free_spots:
                # Step 2: Pick a random free spot and atomically mark it occupied
                assigned_spot = random.choice(free_spots)
                parking_spots[assigned_spot] = False  # reserve NOW inside the lock

                # Store active car state
                active_cars[car_plate] = {
                    "entry_time": server_time,
                    "car_type": car_type,
                    "assigned_spot": assigned_spot,
                    "planned_duration": planned_duration,
                    "charged": False,
                }
            else:
                assigned_spot = None

        target_gate = "GateA"
        if assigned_spot:
            print(f"[ENTRY LOGIC] Assigned spot {assigned_spot} to {car_plate}. Notifying Gate A worker for gate {target_gate}...")
            await gate_a_queue.put({
                "car_plate": car_plate,
                "gate_name": target_gate,
                "destination": assigned_spot,
            })
        else:
            print(f"[ENTRY LOGIC] Parking lot is FULL! Directing car {car_plate} to leave via Gate A worker...")
            await gate_a_queue.put({
                "car_plate": car_plate,
                "gate_name": target_gate,
                "destination": "leavepark",
            })
    except Exception as e:
        print(f"[ENTRY LOGIC ERROR] {car_plate}: {e}")



async def process_car_exit(data: dict):
    """Internal logic to handle a car arriving at GateB / exit gate, verifying payment before opening gate."""
    car_plate = data.get("CarPlateNumber", "")
    car_type = data.get("CarType", "Normal")
    spot_name = data.get("SpotName", "") or "GateB"
    planned_duration = float(data.get("PlannedParkingDurationInMinutes", 1) or 1)
    server_time = data.get("ServerDateTime", "")

    if not car_plate:
        return

    print(f"[EXIT LOGIC] Car {car_plate} ({car_type}) arrived at exit gate {spot_name}.")

    payment_verified = False

    # Step 1: Request Payment (Single-Charge Enforcement)
    if car_plate not in charged_cars:
        car_info = active_cars.get(car_plate, {})

        duration = car_info.get("planned_duration", planned_duration)

        # Parking Cost = Total minutes spent
        parking_cost = float(max(1, round(duration)))

        # Electricity Charging Cost = minutes * 2 IF electric car, else 0.0
        is_electric = ("EV" in str(car_type).upper()) or ("ELECTRIC" in str(car_type).upper())
        charging_cost = float(parking_cost * 2.0) if is_electric else 0.0

        print(f"[EXIT LOGIC] Requesting payment for {car_plate}: parkingCost={parking_cost}, chargingCost={charging_cost} (EV={is_electric})")

        try:
            # Isolated API call for payment
            await api_charge_car(car_plate, parking_cost, charging_cost)
            charged_cars.add(car_plate)
            if car_plate in active_cars:
                active_cars[car_plate]["charged"] = True
            payment_verified = True
            print(f"[EXIT LOGIC] Payment verified & recorded for {car_plate}.")
        except Exception as e:
            print(f"[EXIT LOGIC ERROR] Payment failed for {car_plate}: {e}")
    else:
        payment_verified = True
        print(f"[EXIT LOGIC] Payment already verified for {car_plate}.")

    # Free the parking spot in the hash table as soon as payment is confirmed
    if payment_verified:
        freed_spot = active_cars.get(car_plate, {}).get("assigned_spot")
        if freed_spot and freed_spot in parking_spots:
            async with spot_lock:
                parking_spots[freed_spot] = True  # mark free immediately
            print(f"[EXIT LOGIC] Spot {freed_spot} freed in parking_spots hash table.")
        if car_plate in active_cars:
            active_cars[car_plate]["assigned_spot"] = None

    # Step 2: Notify dedicated Gate B worker thread if payment is verified
    if payment_verified:
        target_gate = spot_name or "GateB"
        print(f"[EXIT LOGIC] Payment verified for {car_plate}. Notifying Gate B worker thread for gate {target_gate}...")
        await gate_b_queue.put({
            "car_plate": car_plate,
            "gate_name": "GateB",
        })
    else:
        print(f"[EXIT LOGIC] Payment NOT verified for {car_plate}. Gate {spot_name} remains CLOSED.")







async def handle_webhook_event(data: dict):
    """Central router for incoming webhook events."""
    # Log raw event
    webhook_events.append(data)
    if len(webhook_events) > 100:
        webhook_events.pop(0)

    spot_type = data.get("SpotType", "")
    spot_name = data.get("SpotName", "")
    car_plate = data.get("CarPlateNumber", "")

    if car_plate:
        if spot_type == "EntrySpot" or (spot_name and spot_name.upper().startswith("ENTRY")):
            await process_car_entry(data)
        elif spot_type == "ExitSpot" or (spot_name and spot_name.upper().startswith("EXIT")):
            await process_car_exit(data)


# =====================================================================
# 3. FASTAPI CONTROLLER LAYER (HTTP Routes)
# =====================================================================

@app.get("/webhook")
@app.get("/webhook.php")
def webhook_info():
    """Information route for browser testing."""
    return {"message": "Webhook endpoint is active. Simulator sends POST requests here."}



@app.get("/recent-arrivals")
def get_recent_arrivals(limit: int = 10):
    """Fetch recent car arrivals captured by the webhook."""
    return {
        "count": len(recent_car_arrivals),
        "recent_arrivals": recent_car_arrivals[-limit:]
    }


@app.get("/all-events")
def get_all_events(limit: int = 20):
    """Fetch recent raw webhook events captured by the server."""
    return {
        "count": len(webhook_events),
        "events": webhook_events[-limit:]
    }


@app.get("/recent-activity")
def get_recent_activity(limit: int = 25):
    """Return cleanly structured, newest-first recent activity items for the dashboard."""
    items = []
    for evt in reversed(webhook_events[-80:]):
        event_class = evt.get("EventClass", "")
        server_dt = evt.get("ServerDateTime") or evt.get("Timestamp") or "Recent"
        time_str = server_dt.split(" ")[-1] if " " in str(server_dt) else str(server_dt)
        evt_id = evt.get("EventId") or str(len(items) + 1)
        seq_id = evt.get("SequenceId")

        # 1. Car Spot Action
        if event_class == "car_spot_action":
            plate = evt.get("CarPlateNumber") or evt.get("CarPlate") or evt.get("car_plate") or "Unknown"
            spot_type = evt.get("SpotType", "")
            spot_name = evt.get("SpotName", "")
            direction = evt.get("Direction", "")

            if spot_type == "EntrySpot" or "ENTRY" in spot_name.upper():
                if direction == "CarIn":
                    event_text = "Arrived at Entrance"
                    status_text = "Queued"
                else:
                    event_text = "Passed Entrance Gate"
                    status_text = "In Transit"
            elif spot_type == "Park":
                if direction == "CarIn":
                    event_text = f"Parked in {spot_name}"
                    status_text = "Parked"
                else:
                    event_text = f"Left spot {spot_name}"
                    status_text = "Departing"
            elif spot_type == "ExitSpot" or "EXIT" in spot_name.upper():
                if direction == "CarIn":
                    event_text = "Arrived at Exit Gate"
                    status_text = "Processing"
                else:
                    event_text = "Exited Car Park"
                    status_text = "Departed"
            else:
                event_text = f"{spot_type} {direction}"
                status_text = "Active"

            items.append({
                "id": evt_id,
                "sequenceId": seq_id,
                "category": "car",
                "plate": plate,
                "event": event_text,
                "spot": spot_name or "—",
                "time": time_str,
                "status": status_text,
            })

        # 2. Gate Action
        elif event_class == "gate_action":
            gate_name = evt.get("Name", "Gate")
            action = evt.get("Action", "Operated")
            items.append({
                "id": evt_id,
                "sequenceId": seq_id,
                "category": "gate",
                "plate": f"[{gate_name}]",
                "event": f"Gate {gate_name} {action}",
                "spot": gate_name,
                "time": time_str,
                "status": "Open" if action.lower() in ("open", "opened") else "Closed",
            })

        # 3. Penalty / Fine
        elif event_class == "penalty":
            plate = evt.get("CarPlateNumber") or evt.get("ComponentName") or "Unknown"
            reason = evt.get("Reason", "Violation")
            fine = evt.get("FineAmount", "0")
            items.append({
                "id": evt_id,
                "sequenceId": seq_id,
                "category": "alert",
                "plate": plate,
                "event": f"Penalty: {reason} (${fine})",
                "spot": evt.get("ComponentName", "—"),
                "time": time_str,
                "status": "Penalty",
            })

        # 4. Other events (CO alert, component broken, etc.)
        elif event_class:
            items.append({
                "id": evt_id,
                "sequenceId": seq_id,
                "category": "system",
                "plate": evt.get("CarPlateNumber") or "[SYSTEM]",
                "event": event_class.replace("_", " ").title(),
                "spot": evt.get("Name") or evt.get("ComponentName") or "—",
                "time": time_str,
                "status": "Alert",
            })

        if len(items) >= limit:
            break

    # If still below limit, supplement from recent_car_arrivals
    if len(items) < limit:
        for arr in reversed(recent_car_arrivals[-(limit - len(items)):]):
            p = arr.get("car_plate")
            if p and not any(it.get("plate") == p for it in items):
                items.append({
                    "id": f"arr-{len(items)+1}",
                    "sequenceId": None,
                    "category": "car",
                    "plate": p,
                    "event": "Arrived at Entrance",
                    "spot": arr.get("spot_name", "EntrySpot"),
                    "time": str(arr.get("arrival_time") or "Recent").split(" ")[-1],
                    "status": "Queued",
                })

    return items


@app.get("/system-status")
async def get_system_status():
    """Return backend status, simulator connectivity, and current counts from thread-safe parking_spots."""
    sim_online = False
    try:
        data = await call_simulator_api("list-parking-spots")
        sim_online = True
        if isinstance(data, list):
            now = time.time()
            async with spot_lock:
                for s in data:
                    s_name = s.get("name")
                    if s_name in parking_spots:
                        if s.get("detectedCars", 0) > 0:
                            parking_spots[s_name] = False
                        else:
                            in_transit = any(
                                info.get("assigned_spot") == s_name and (now - info.get("entry_time", 0) < 15)
                                for info in active_cars.values()
                            )
                            if not in_transit:
                                parking_spots[s_name] = True
    except Exception:
        sim_online = False

    async with spot_lock:
        total_spots = len(parking_spots)
        occupied_count = sum(1 for free in parking_spots.values() if not free)
        free_count = total_spots - occupied_count

    return {
        "backend": "online",
        "simulator": "online" if sim_online else "offline",
        "total_spots": total_spots,
        "available_spots": free_count,
        "occupied_spots": occupied_count,
        "cars_inside": len(active_cars),
        "park_full": free_count == 0,
    }


@app.get("/active-cars")
def get_active_cars():
    """Fetch all active cars currently inside the car park."""
    cars_list = []
    now = time.time()
    for plate, info in active_cars.items():
        entry_raw = info.get("entry_time", "")
        display_time = "Recently"
        elapsed_str = "Active"
        if isinstance(entry_raw, (int, float)):
            display_time = datetime.fromtimestamp(entry_raw).strftime("%I:%M %p")
            elapsed_str = f"{max(0, int((now - entry_raw) / 60))}m"
        elif isinstance(entry_raw, str) and entry_raw:
            display_time = entry_raw.split(" ")[-1] if " " in entry_raw else entry_raw
            try:
                dt = datetime.strptime(entry_raw, "%Y-%m-%d %H:%M:%S")
                diff = max(0, int((datetime.now() - dt).total_seconds() / 60))
                elapsed_str = f"{diff}m"
            except Exception:
                elapsed_str = "Active"

        cars_list.append({
            "plateNumber": plate,
            "vehicleType": info.get("car_type", "Standard"),
            "brand": "Standard",
            "model": "Car",
            "colour": "Silver",
            "assignedSpot": info.get("assigned_spot", "—"),
            "parkingSpot": info.get("assigned_spot", "—"),
            "entryTime": display_time,
            "duration": elapsed_str,
            "plannedDuration": info.get("planned_duration", 0),
            "charged": info.get("charged", False),
            "status": "Parked" if not info.get("charged", False) else "Exiting",
        })
    return {
        "count": len(cars_list),
        "cars": cars_list,
    }


@app.get("/vehicles/{plate}")
def get_vehicle_details(plate: str):
    """Fetch real-time vehicle details for a given plate number."""
    now = time.time()
    info = active_cars.get(plate)
    if info:
        entry_raw = info.get("entry_time", "")
        display_time = str(entry_raw)
        elapsed_str = "Active"
        if isinstance(entry_raw, (int, float)):
            display_time = datetime.fromtimestamp(entry_raw).strftime("%I:%M %p")
            elapsed_str = f"{max(0, int((now - entry_raw) / 60))}m"
        elif isinstance(entry_raw, str) and entry_raw:
            try:
                dt = datetime.strptime(entry_raw, "%Y-%m-%d %H:%M:%S")
                display_time = dt.strftime("%I:%M %p")
                elapsed_str = f"{max(0, int((datetime.now() - dt).total_seconds() / 60))}m"
            except Exception:
                display_time = entry_raw
        return {
            "found": True,
            "plateNumber": plate,
            "vehicleType": info.get("car_type", "Sedan"),
            "brand": "Standard",
            "model": "Car",
            "colour": "Silver",
            "parkingSpot": info.get("assigned_spot", "—"),
            "status": "Parked" if not info.get("charged", False) else "Exiting",
            "entryTime": display_time,
            "exitTime": "—",
            "duration": elapsed_str,
            "charged": info.get("charged", False),
        }
    
    # Check recent arrivals
    for arr in reversed(recent_car_arrivals):
        if arr.get("car_plate", "").lower() == plate.lower():
            return {
                "found": True,
                "plateNumber": arr.get("car_plate"),
                "vehicleType": arr.get("car_type", "Sedan"),
                "brand": "Standard",
                "model": "Car",
                "colour": "White",
                "parkingSpot": arr.get("spot_name", "—"),
                "status": "Arrived",
                "entryTime": arr.get("arrival_time", "Recent"),
                "exitTime": "—",
                "duration": "Recently entered",
                "charged": False,
            }
            
    # Check recent webhook events
    for evt in reversed(webhook_events):
        p = evt.get("CarPlate") or evt.get("car_plate")
        if p and p.lower() == plate.lower():
            return {
                "found": True,
                "plateNumber": p,
                "vehicleType": evt.get("CarType", "Sedan"),
                "brand": "Vehicle",
                "model": "Standard",
                "colour": "Grey",
                "parkingSpot": evt.get("SpotName", "—"),
                "status": "Exited" if evt.get("SpotType") == "ExitSpot" else "Active",
                "entryTime": evt.get("Timestamp", "Earlier"),
                "exitTime": "—",
                "duration": "Completed",
                "charged": True,
            }

    # Check MySQL database parking sessions
    try:
        from app.db.session import SessionLocal
        from app.models import ParkingSession, ParkingSpot
        from sqlalchemy import select
        with SessionLocal() as db:
            row = db.execute(
                select(ParkingSession, ParkingSpot.name)
                .outerjoin(ParkingSpot, ParkingSession.parking_spot_id == ParkingSpot.id)
                .where(ParkingSession.car_plate.like(f"{plate}%"))
                .order_by(ParkingSession.id.desc())
            ).first()
            if row:
                sess, s_name = row
                entry_str = sess.entry_time.strftime("%I:%M %p") if sess.entry_time else "Earlier"
                exit_str = sess.exit_time.strftime("%I:%M %p") if sess.exit_time else "—"
                fee_display = f"${(sess.fee_cents or 0) / 100:.2f}"
                return {
                    "found": True,
                    "plateNumber": sess.car_plate,
                    "vehicleType": getattr(sess, "car_type", "Sedan") or "Sedan",
                    "brand": "Standard",
                    "model": "Car",
                    "colour": "Silver",
                    "parkingSpot": s_name or "—",
                    "status": "Exited" if sess.status == "completed" else "Active",
                    "entryTime": entry_str,
                    "exitTime": exit_str,
                    "duration": f"Fee: {fee_display}" if sess.fee_cents else "Completed",
                    "charged": sess.status == "completed" or bool(sess.fee_cents),
                }
    except Exception:
        pass

    return {
        "found": False,
        "plateNumber": plate,
        "message": f"No vehicle records found for {plate}"
    }


# --- Simulator GET Routes ---

@app.get("/list-parking-spots")
async def list_parking_spots():
    """Return all parking spots state based strictly on the thread-safe parking_spots hash table holding spot_lock."""
    # 1. Check simulator for any broken/maintenance flags or unrecorded detections
    broken_spots = set()
    detected_map = {}
    try:
        sim_spots = await call_simulator_api("list-parking-spots")
        if isinstance(sim_spots, list):
            for s in sim_spots:
                s_name = s.get("name")
                if s_name:
                    detected_map[s_name] = s.get("detectedCars", 0)
                    if s.get("broken") or s.get("isUnderMaintenance"):
                        broken_spots.add(s_name)
    except Exception as e:
        print(f"[WARN] Failed calling simulator list-parking-spots: {e}")

    now = time.time()
    async with spot_lock:
        # Clean active_cars stale spots and update parking_spots
        for name in VALID_PARKING_SPOTS:
            if name in detected_map:
                if detected_map[name] > 0:
                    parking_spots[name] = False
                else:
                    in_transit = False
                    for p, info in list(active_cars.items()):
                        if info.get("assigned_spot") == name:
                            t = info.get("entry_time", 0)
                            if isinstance(t, (int, float)) and (now - t < 15):
                                in_transit = True
                            else:
                                info["assigned_spot"] = None
                    if not in_transit:
                        parking_spots[name] = True

        local_spots_snapshot = dict(parking_spots)

    # 3. Construct the spots array (S1 to S30) directly from parking_spots hash table
    spots = []
    for i in range(1, 31):
        name = f"S{i}"
        is_free = local_spots_snapshot.get(name, True)
        is_broken = name in broken_spots
        is_occupied = not is_free

        # Match vehicle plate from active_cars or recent webhook events ONLY if occupied
        assigned_plate = None
        if is_occupied:
            assigned_plate = next((p for p, info in active_cars.items() if info.get("assigned_spot") == name), None)
            if not assigned_plate:
                for evt in reversed(webhook_events):
                    evt_spot = evt.get("SpotName") or evt.get("spot_name")
                    if evt_spot == name:
                        p = evt.get("CarPlate") or evt.get("car_plate") or evt.get("CarPlateNumber")
                        if p:
                            assigned_plate = p
                            break

        status = "maintenance" if is_broken else ("occupied" if is_occupied else "free")

        spots.append({
            "name": name,
            "purpose": "Park",
            "number": i,
            "isOccupied": is_occupied,
            "detectedCars": 1 if is_occupied else 0,
            "broken": is_broken,
            "isUnderMaintenance": is_broken,
            "isBroken": is_broken,
            "status": status,
            "carPlateNumber": assigned_plate if is_occupied else None,
            "zone": "Zone 1"
        })

    return spots


@app.get("/list-barriers")
async def list_barriers():
    """Fetch all barriers from the simulator, with fallback."""
    try:
        return await call_simulator_api("list-barriers")
    except Exception:
        # Fallback default barrier gates state
        return [
            {"name": "GateA", "isOpen": False, "isBroken": False, "type": "Entrance"},
            {"name": "GateB", "isOpen": False, "isBroken": False, "type": "Exit"}
        ]


@app.get("/list-lights")
async def list_lights():
    """Fetch all lights from the simulator."""
    return await call_simulator_api("list-lights")


@app.get("/list-exhaust-fans")
async def list_exhaust_fans():
    """Fetch all exhaust fans from the simulator."""
    return await call_simulator_api("list-exhaust-fans")


@app.get("/list-alarms")
async def list_alarms():
    """Fetch all alarms from the simulator."""
    return await call_simulator_api("list-alarms")


@app.get("/list-zones")
async def list_zones():
    """Fetch all zones from the simulator."""
    return await call_simulator_api("list-zones")


@app.get("/test")
async def test():
    """Test endpoint for simulator API."""
    return await call_simulator_api("test")


# --- Simulator POST Control Routes ---

@app.post("/barrier-gates/{name}/open")
async def open_barrier_gate(name: str):
    """Open a barrier gate by name."""
    return await api_open_barrier_gate(name)


@app.post("/barrier-gates/{name}/close")
async def close_barrier_gate(name: str):
    """Close a barrier gate by name."""
    return await call_simulator_api(f"barrier-gates/{name}/close", method="POST")


@app.post("/barrier-gates/{name}/repair")
async def repair_barrier_gate(name: str):
    """Repair a barrier gate by name."""
    return await call_simulator_api(f"barrier-gates/{name}/repair", method="POST")


@app.post("/lights/{name}/on")
async def turn_on_light(name: str):
    """Turn on a light by name."""
    return await call_simulator_api(f"lights/{name}/on", method="POST")


@app.post("/lights/{name}/off")
async def turn_off_light(name: str):
    """Turn off a light by name."""
    return await call_simulator_api(f"lights/{name}/off", method="POST")


@app.post("/lights/group/{name}/on")
async def turn_on_light_group(name: str):
    """Turn on a light group by name."""
    return await call_simulator_api(f"lights/group/{name}/on", method="POST")


@app.post("/lights/group/{name}/off")
async def turn_off_light_group(name: str):
    """Turn off a light group by name."""
    return await call_simulator_api(f"lights/group/{name}/off", method="POST")


@app.post("/exhaust-fans/{name}/repair")
async def repair_exhaust_fan(name: str):
    """Repair an exhaust fan by name."""
    return await call_simulator_api(f"exhaust-fans/{name}/repair", method="POST")


@app.post("/exhaust-fans/{name}/on")
async def turn_on_exhaust_fan(name: str):
    """Turn on an exhaust fan by name."""
    return await call_simulator_api(f"exhaust-fans/{name}/on", method="POST")


@app.post("/exhaust-fans/{name}/off")
async def turn_off_exhaust_fan(name: str):
    """Turn off an exhaust fan by name."""
    return await call_simulator_api(f"exhaust-fans/{name}/off", method="POST")


@app.post("/parking-spots/{name}/repair")
async def repair_parking_spot(name: str):
    """Repair a parking spot by name."""
    return await call_simulator_api(f"parking-spots/{name}/repair", method="POST")


@app.post("/car/{name}/goto/{destination}")
async def car_goto(name: str, destination: str):
    """Direct a car to a destination spot."""
    return await api_send_car_to_destination(name, destination)


@app.post("/car/{name}/charge")
async def car_charge(name: str, parking_cost: float = 0.0, charging_cost: float = 0.0):
    """Charge a car with optional parking and charging cost query parameters."""
    return await api_charge_car(name, parking_cost, charging_cost)



        
