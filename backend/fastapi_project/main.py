import asyncio
import random
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request, status
import httpx

app = FastAPI(title="Parking Simulator Backend")

# Configuration constants
SIMULATOR_URL = "http://127.0.0.1:9898"
SIMULATOR_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJodHRwOi8vc2NoZW1hcy54bWxzb2FwLm9yZy93cy8yMDA1LzA1L2lkZW50aXR5L2NsYWltcy9lbWFpbGFkZHJlc3MiOiJhZG1pbiIsImV4cCI6MTc5MDA0NjgyNywiaXNzIjoiUGFya2luZ1NpbXVsYXRvciJ9."
    "3DA04V2M5nRvSN1Ge-1FFpEc8sJot6sECqouunCyrDI"
)

# Valid parking spot range: S1 to S30
VALID_PARKING_SPOTS = [f"S{i}" for i in range(1, 31)]

# In-memory storage state
webhook_events = []
recent_car_arrivals = []
active_cars = {}     # car_plate -> { entry_time, car_type, assigned_spot, planned_duration, charged }
charged_cars = set()  # set of car_plates to prevent double charging penalties

# Parallel Dual Queues: Independent Entrance and Exit processing
entry_queue = asyncio.Queue()
exit_queue = asyncio.Queue()
spot_lock = asyncio.Lock()  # Lock to ensure thread-safe spot selection


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
    """Background worker for EXIT processing (FCFS among exit cars, running in parallel with entrance)."""
    print("[EXIT WORKER] Exit Queue Worker started.")
    while True:
        data = await exit_queue.get()
        try:
            await process_car_exit(data)
        except Exception as e:
            print(f"[EXIT WORKER ERROR] {e}")
        finally:
            exit_queue.task_done()


@app.on_event("startup")
async def startup_event():
    """Start parallel Entrance and Exit background workers on application startup."""
    asyncio.create_task(entry_worker())
    asyncio.create_task(exit_worker())


# =====================================================================
# 3. FASTAPI CONTROLLER LAYER (HTTP Routes)
# =====================================================================

@app.post("/webhook")
@app.post("/webhook.php")
async def webhook(request: Request):
    """Receive incoming events from the Parking Simulator and route to Entrance or Exit queue."""
    data = await request.json()
    
    # Store raw event log
    webhook_events.append(data)
    if len(webhook_events) > 100:
        webhook_events.pop(0)

    spot_type = data.get("SpotType", "")
    spot_name = data.get("SpotName", "")
    
    # Route to parallel queues based on event type
    if spot_type == "EntrySpot" or (spot_name and spot_name.upper().startswith("ENTRY")):
        await entry_queue.put(data)
    elif spot_type == "ExitSpot" or (spot_name and spot_name.upper().startswith("EXIT")):
        await exit_queue.put(data)

    return {
        "status": "queued",
        "entry_queue_size": entry_queue.qsize(),
        "exit_queue_size": exit_queue.qsize(),
    }



async def call_simulator_api(
    endpoint: str,
    method: str = "GET",
    params: dict = None,
    json_data: dict = None,
):
    """Low-level HTTP wrapper to call the simulator API asynchronously."""
    url = f"{SIMULATOR_URL}/api/v1/{endpoint.lstrip('/')}"
    headers = {"Authorization": f"Bearer {SIMULATOR_TOKEN}"}

    try:
        async with httpx.AsyncClient() as client:
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
        # Step 1: Thread-safe spot query & selection using async spot_lock
        async with spot_lock:
            available_spots = await api_get_available_spots()

            if available_spots:
                # Step 2: Assign random spot
                selected_spot_obj = random.choice(available_spots)
                assigned_spot = selected_spot_obj.get("name") or selected_spot_obj.get("spotName")

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

        if assigned_spot:
            print(f"[ENTRY LOGIC] Assigned spot {assigned_spot} to {car_plate}. Opening gate {spot_name}...")

            # Step 3: Open entry barrier gate via isolated API call
            await api_open_barrier_gate(spot_name)

            # Step 4: Direct car to assigned spot via isolated API call
            await api_send_car_to_destination(car_plate, assigned_spot)

            # Step 5: Automatically close gate after car passes
            asyncio.create_task(auto_close_gate_after_delay(spot_name, delay=5.0))

        else:
            print(f"[ENTRY LOGIC] Parking lot is FULL! Directing car {car_plate} to leave...")
            # If no free spots available, guide car to leavepark
            try:
                await api_open_barrier_gate(spot_name)
                await api_send_car_to_destination(car_plate, "leavepark")
                asyncio.create_task(auto_close_gate_after_delay(spot_name, delay=5.0))
            except Exception as ex:
                print(f"[ENTRY LOGIC ERROR] Guiding car {car_plate} away failed: {ex}")
    except Exception as e:
        print(f"[ENTRY LOGIC ERROR] {car_plate}: {e}")



async def process_car_exit(data: dict):
    """Internal logic to handle a car arriving at an exit gate with single-charge enforcement & immediate gate open."""
    car_plate = data.get("CarPlateNumber", "")
    car_type = data.get("CarType", "Normal")
    spot_name = data.get("SpotName", "")
    planned_duration = float(data.get("PlannedParkingDurationInMinutes", 1) or 1)
    server_time = data.get("ServerDateTime", "")

    if not car_plate:
        return

    print(f"[EXIT LOGIC] Car {car_plate} ({car_type}) arrived at exit gate {spot_name}.")

    # Step 1: Single-Charge Enforcement (Avoid double charging penalty or runaway cars)
    if car_plate not in charged_cars:
        # Mark as charged immediately to block duplicate concurrent webhooks
        charged_cars.add(car_plate)
        
        car_info = active_cars.get(car_plate, {})
        duration = planned_duration

        # Calculate duration from entry time if available
        entry_time_str = car_info.get("entry_time")
        if entry_time_str and server_time:
            try:
                fmt = "%Y-%m-%d %H:%M:%S"
                dt_entry = datetime.strptime(entry_time_str, fmt)
                dt_exit = datetime.strptime(server_time, fmt)
                elapsed_minutes = (dt_exit - dt_entry).total_seconds() / 60.0
                if elapsed_minutes > 0:
                    duration = max(1.0, elapsed_minutes)
            except Exception:
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
            if car_plate in active_cars:
                active_cars[car_plate]["charged"] = True
            print(f"[EXIT LOGIC] Payment verified & recorded for {car_plate}.")
        except Exception as e:
            print(f"[EXIT LOGIC ERROR] Charging request failed for {car_plate}: {e}")
    else:
        print(f"[EXIT LOGIC] Payment already verified for {car_plate}. Skipping request.")

    # Step 2: Open Exit Gate immediately so car does not wait/run away
    try:
        print(f"[EXIT LOGIC] Opening exit gate {spot_name} for {car_plate}...")
        await api_open_barrier_gate(spot_name)
    except Exception as e:
        print(f"[EXIT LOGIC ERROR] Failed to open exit gate {spot_name}: {e}")

    # Step 3: Direct car to leavepark/exit
    try:
        print(f"[EXIT LOGIC] Guiding car {car_plate} to leave park...")
        await api_send_car_to_destination(car_plate, "leavepark")
    except Exception as e:
        print(f"[EXIT LOGIC ERROR] Directing car {car_plate} to leave failed: {e}")

    # Step 4: Auto-close gate behind car after 3s delay
    asyncio.create_task(auto_close_gate_after_delay(spot_name, delay=3.0))




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


# --- Simulator GET Routes ---

@app.get("/list-parking-spots")
async def list_parking_spots():
    """Fetch all parking spots from the simulator."""
    return await call_simulator_api("list-parking-spots")


@app.get("/list-barriers")
async def list_barriers():
    """Fetch all barriers from the simulator."""
    return await call_simulator_api("list-barriers")


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



        
