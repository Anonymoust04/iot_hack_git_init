"""
Parking Simulator Backend - Level 2
====================================
Entrance gates : gate1 (Zone 1 / S-spots), gate3 (Zone 2 / bay-spots), gate5 (Zone 3 / P-spots)
Exit gates     : gate2, gate4, gate6
Zones          : Zone 1 -> S1-S30 | Zone 2 -> bay36-bay65 | Zone 3 -> P69-P98

Key behaviours
--------------
- Zone 1 fills first, then Zone 2, then Zone 3 (for all vehicle types)
- Vehicle-type-aware spot selection (Electric -> EV spots, Accessible -> Accessible spots)
- Re-entry guard: rerouted cars arriving at the correct gate are let through immediately
- Fee charged only after the car has confirmed physically parked (Park/CarIn event)
- CO risk Mid/High/Critical or high numeric CO -> working fans ON; Safe and low CO -> OFF
- Day/Night light control (daytime 06:00-18:00 -> lights OFF; night -> lights ON)
- Preventive maintenance: polls list-alarms every 5 s and repairs idle components
- Usage cycle tracking: polls every 30 s and logs component cycle counts
- Broken/unavailable component health tracked from webhook events; exposed on /component-health
"""

import asyncio
import math
import time
from datetime import datetime
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Request, status
import httpx

app = FastAPI(title="Parking Simulator Backend - Level 2")

import db_hook
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

# -------------------------------------------------
# CONFIGURATION CONSTANTS
# -------------------------------------------------

# SIM_BASE_URL in .env (e.g. http://127.0.0.1:9898/api/v1); tests point it elsewhere so they never
# drive the real simulator
SIMULATOR_URL      = str(getattr(get_settings(), "sim_base_url", "http://127.0.0.1:9898/api/v1")).rstrip("/").removesuffix("/api/v1")
SIMULATOR_EMAIL    = "admin"
SIMULATOR_PASSWORD = "admin"

VALID_PARKING_SPOTS = (
    [f"S{i}"   for i in range(1, 31)]  +
    [f"bay{i}" for i in range(36, 66)] +
    [f"P{i}"   for i in range(69, 99)]
)

ENTRANCE_GATES = ["gate1", "gate3", "gate5"]
EXIT_GATES     = ["gate2", "gate4", "gate6"]
ALL_GATES      = ENTRANCE_GATES + EXIT_GATES

SPOT_CAR_TYPES: dict[str, str] = {
    "S5": "Electric", "S6": "Electric", "S10": "Electric", "S11": "Electric",
    "S20": "Electric", "S21": "Electric", "S25": "Electric", "S26": "Electric",
    "S7": "Accessible", "S8": "Accessible", "S9": "Accessible",
    "bay42": "Electric", "bay43": "Electric", "bay47": "Electric", "bay48": "Electric",
    "bay56": "Electric", "bay57": "Electric", "bay61": "Electric", "bay62": "Electric",
    "bay58": "Accessible", "bay59": "Accessible", "bay60": "Accessible",
    "P74": "Electric", "P75": "Electric", "P76": "Electric", "P77": "Electric", "P78": "Electric",
    "P79": "Accessible", "P80": "Accessible", "P81": "Accessible",
}


def get_spot_type(spot_name: str) -> str:
    return SPOT_CAR_TYPES.get(spot_name, "Any")


def normalize_vehicle_type(raw_type: str) -> str:
    t = str(raw_type or "").strip().lower()
    if "elect" in t or "ev" in t:
        return "Electric"
    if "access" in t or "disab" in t or "handicap" in t:
        return "Accessible"
    return "Normal"


def spot_gate(spot_name: str) -> str:
    if spot_name.startswith("S"):
        return "gate1"
    if spot_name.startswith("bay"):
        return "gate3"
    return "gate5"


def entry_spot_for_gate(gate: str) -> str:
    return {"gate1": "ENTRY1", "gate3": "ENTRY2", "gate5": "ENTRY3"}.get(gate, "ENTRY1")


def extract_spot_number(name: str) -> int:
    digits = "".join(ch for ch in name if ch.isdigit())
    return int(digits) if digits else 9999


def gate_for_entry_spot(spot_name: str) -> str:
    norm = (spot_name or "").lower().replace(" ", "").replace("_", "")
    if "entry2" in norm or "gate3" in norm:
        return "gate3"
    if "entry3" in norm or "gate5" in norm:
        return "gate5"
    return "gate1"


def exit_gate_for_spot(spot_name: str) -> str:
    norm = (spot_name or "").lower()
    if "exit2" in norm or "gate4" in norm:
        return "gate4"
    if "exit3" in norm or "gate6" in norm:
        return "gate6"
    return "gate2"


# -------------------------------------------------
# IN-MEMORY STATE
# -------------------------------------------------

SIMULATOR_TOKEN: str | None = None
_token_lock = asyncio.Lock()

parking_spots: dict[str, bool] = {s: True for s in VALID_PARKING_SPOTS}
spot_lock = asyncio.Lock()
gate_queues: dict[str, asyncio.Queue] = {g: asyncio.Queue() for g in ALL_GATES}

active_cars:  dict[str, dict] = {}
charged_cars: set[str] = set()

webhook_events:      list[dict] = []
recent_car_arrivals: list[dict] = []

component_health: dict[str, dict] = {}
usage_cycles:     dict[str, dict] = {}

_last_sim_hour: int = 12


# -------------------------------------------------
# SIMULATOR AUTH & HTTP HELPERS
# -------------------------------------------------

class LoginRequest(BaseModel):
    email: str = "admin"
    password: str = "admin"


async def api_login(email: str | None = None, password: str | None = None) -> str:
    global SIMULATOR_TOKEN
    email    = email    or SIMULATOR_EMAIL
    password = password or SIMULATOR_PASSWORD
    url = f"{SIMULATOR_URL}/api/v1/auth/login"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json={"email": email, "password": password})
    if not resp.is_success:
        raise HTTPException(resp.status_code, f"Login failed: {resp.text}")
    token = resp.json().get("token")
    if not token:
        raise HTTPException(502, "Login response had no token")
    SIMULATOR_TOKEN = token
    print(f"[AUTH] Logged in as {email} ({token[:20]}...)")
    return token


async def get_token(force: bool = False) -> str:
    global SIMULATOR_TOKEN
    async with _token_lock:
        if force or not SIMULATOR_TOKEN:
            await api_login()
        return SIMULATOR_TOKEN


async def call_simulator_api(endpoint: str, method: str = "GET",
                              params: dict = None, json_data: dict = None):
    token = await get_token()
    url   = f"{SIMULATOR_URL}/api/v1/{endpoint.lstrip('/')}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=get_settings().sim_timeout_seconds) as client:
            response = await client.request(method=method, url=url, headers=headers,
                                            params=params, json=json_data)
            if response.status_code == 401:
                token   = await get_token(force=True)
                headers = {"Authorization": f"Bearer {token}"}
                response = await client.request(method=method, url=url, headers=headers,
                                                params=params, json=json_data)
            if response.is_success:
                try:
                    return response.json()
                except Exception:
                    return {"status": "success", "text": response.text}
            else:
                print(f"[API Error] {method} {endpoint} -> {response.status_code}")
                raise HTTPException(status_code=response.status_code,
                                    detail=f"Simulator API error: {response.text}")
    except httpx.RequestError as exc:
        print(f"[Network Error] {url}: {exc}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"Could not connect to Parking Simulator at {SIMULATOR_URL}.")


async def is_component_operable(name: str) -> bool:
    h = component_health.get(name, {})
    return not h.get("broken", False) and not h.get("under_maintenance", False)


async def api_open_barrier_gate(gate_name: str):
    if not await is_component_operable(gate_name):
        print(f"[SAFETY] Blocked opening {gate_name}: not operable.")
        return {"status": "blocked"}
    return await call_simulator_api(f"barrier-gates/{gate_name}/open", method="POST")


async def api_close_barrier_gate(gate_name: str):
    if not await is_component_operable(gate_name):
        print(f"[SAFETY] Blocked closing {gate_name}: not operable.")
        return {"status": "blocked"}
    return await call_simulator_api(f"barrier-gates/{gate_name}/close", method="POST")


async def api_send_car_to_destination(car_name: str, destination: str):
    return await call_simulator_api(f"car/{car_name}/goto/{destination}", method="POST")


async def api_charge_car(car_name: str, parking_cost: float, charging_cost: float):
    return await call_simulator_api(f"car/{car_name}/charge", method="POST",
                                    params={"parkingCost": parking_cost, "chargingCost": charging_cost})


# -------------------------------------------------
# GATE WORKERS
# -------------------------------------------------

async def auto_close_gate_after_delay(gate_name: str, delay: float = 1.0):
    if delay > 0:
        await asyncio.sleep(delay)
    try:
        q = gate_queues.get(gate_name)
        if q is None or q.empty():
            await api_close_barrier_gate(gate_name)
    except Exception as e:
        print(f"[AUTOMATION ERROR] Close {gate_name}: {e}")


async def release_unparked_assignment(car_plate: str, destination: str):
    """Return a reserved spot when its entrance gate cannot be opened."""
    async with spot_lock:
        car = active_cars.get(car_plate)
        if car and not car.get("parked") and car.get("assigned_spot") == destination:
            parking_spots[destination] = True
            active_cars.pop(car_plate, None)


async def dedicated_entrance_gate_worker(gate_name: str, queue: asyncio.Queue):
    print(f"[{gate_name.upper()} WORKER] Entrance worker started.")
    while True:
        event       = await queue.get()
        car_plate   = event.get("car_plate", "")
        destination = event.get("destination", "leavepark")
        # for i in range(12):
        #     if await is_component_operable(gate_name):
        #         break
        #     print(f"[{gate_name.upper()} SAFETY] Maintenance wait {i+1}/12 for {car_plate}...")
        # await asyncio.sleep(1.0)
        print(f"[{gate_name.upper()} WORKER] Opening for {car_plate} -> {destination}")
        try:
            open_result = await api_open_barrier_gate(gate_name)

            if isinstance(open_result, dict) and open_result.get("status") == "blocked":
                print(
                    f"[{gate_name.upper()} WORKER] Gate blocked for {car_plate}; "
                    f"releasing {destination} and sending the car away."
                )
                await release_unparked_assignment(car_plate, destination)
                await api_send_car_to_destination(car_plate, "leavepark")
                continue

            await asyncio.sleep(0.5)
            await api_send_car_to_destination(car_plate, destination)
            # The EntrySpot CarOut webhook closes this gate after the car passes.
        except Exception as e:
            print(f"[{gate_name.upper()} WORKER ERROR] {car_plate}: {e}")
        finally:
            queue.task_done()


async def dedicated_exit_gate_worker(gate_name: str, queue: asyncio.Queue):
    print(f"[{gate_name.upper()} WORKER] Exit worker started.")
    while True:
        event     = await queue.get()
        car_plate = event.get("car_plate", "")
        for i in range(12):
            if await is_component_operable(gate_name):
                break
            print(f"[{gate_name.upper()} SAFETY] Maintenance wait {i+1}/12 for {car_plate}...")
            # await asyncio.sleep(1.0)
        print(f"[{gate_name.upper()} WORKER] Opening exit for {car_plate}")
        try:
            await api_open_barrier_gate(gate_name)
            await api_send_car_to_destination(car_plate, "leavepark")
            # await asyncio.sleep(1.2)
            if queue.empty():
                await api_close_barrier_gate(gate_name)
        except Exception as e:
            print(f"[{gate_name.upper()} WORKER ERROR] {car_plate}: {e}")
        finally:
            queue.task_done()


# -------------------------------------------------
# CO MONITORING & FAN AUTOMATION
# -------------------------------------------------

# The simulator rates each zone's CO itself: Safe < Mid < High < Critical (webhook "DangerLevel",
# list-zones "risk"). It only sends CO webhooks from Mid upward; ~50 ppm is Mid (hackathon docs).
# A stale Safe label with a high numeric reading still needs ventilation. Fans turn off when
# both the risk and the numeric reading are below Mid.
CO_RISK_ORDER      = {"Safe": 0, "Mid": 1, "High": 2, "Critical": 3}
CO_VENTILATE_FROM  = "Mid"
CO_MID_PPM         = 50.0   # only used if a reading arrives without a risk label
CO_CHECK_ACTIVE_S  = 20     # re-check list-zones this often while any zone is ventilating
CO_CHECK_IDLE_S    = 60     # ...and this often otherwise (list-* calls have a simulated cost)

zone_co: dict[str, dict] = {}   # zone -> {"ppm", "risk", "ventilating", "updated", "fans_on"}
_co_lock = asyncio.Lock()


def co_needs_ventilation(risk: str | None, ppm: float | None) -> bool:
    rated_high = CO_RISK_ORDER.get(str(risk).strip().title(), 0) >= CO_RISK_ORDER[CO_VENTILATE_FROM]
    measured_high = ppm is not None and ppm >= CO_MID_PPM
    return rated_high or measured_high


async def audit_system(action: str, target_type: str, target_name: str, success: bool = True, **details):
    """Audit log entry for something the system did by itself (no operator): actor stays empty."""
    try:
        from app.services.audit import record_audit_async
        await record_audit_async(action, target_type=target_type, target_name=target_name,
                                 success=success, details=details or None)
    except Exception as e:
        print(f"[AUDIT] not written ({action} {target_name}): {e}")


async def _audit_fans(zone: str, action: str, names: list, risk, ppm, failed: dict):
    try:
        from app.services.audit import record_audit_async
        for name in names:
            error = failed.get(name)
            await record_audit_async(action, target_type="fan", target_name=name, success=error is None,
                                     details={"zone": zone, "co_ppm": ppm, "risk": risk, **({"error": error} if error else {})})
    except Exception as e:
        print(f"[CO VENTILATION] audit not written: {e}")


async def ventilate_zone(zone_name: str, on: bool, risk=None, ppm=None):
    """Switch every working fan of the zone ON (or OFF); skips fans already in that state."""
    try:
        fans = await call_simulator_api("list-exhaust-fans")
        if not isinstance(fans, list):
            return False
        target = [
            f for f in fans
            if str(f.get("zoneParent") or "").upper() == zone_name.upper()
            and bool(f.get("isOn", False)) != on
            and not f.get("broken", False)
            and not f.get("isUnderMaintenance", False)
            and f.get("name")
            and await is_component_operable(f["name"])
        ]
        zone_fans = [f.get("name") for f in fans if str(f.get("zoneParent") or "").upper() == zone_name.upper()]
        if not target:
            if on and not zone_fans:
                print(f"[CO VENTILATION] No fans in {zone_name}.")
            return True
        names = [f["name"] for f in target]
        print(f"[CO VENTILATION] {zone_name} ({risk}, {ppm} ppm): fans {'ON' if on else 'OFF'} {names}")
        results = await asyncio.gather(*[
            call_simulator_api(f"exhaust-fans/{n}/{'on' if on else 'off'}", method="POST") for n in names
        ], return_exceptions=True)
        failed = {n: str(r) for n, r in zip(names, results) if isinstance(r, Exception)}
        for n, err in failed.items():
            print(f"[CO VENTILATION ERROR] {n}: {err}")
        zone_co.setdefault(zone_name, {})["fans_on"] = [n for n in names if n not in failed] if on else []
        asyncio.create_task(_audit_fans(zone_name, "AUTO_FAN_ON" if on else "AUTO_FAN_OFF", names, risk, ppm, failed))
        return not failed
    except Exception as e:
        print(f"[CO VENTILATION ERROR] {zone_name}: {e}")
        return False


async def update_zone_co(zone_name: str, ppm, risk):
    """New CO reading (webhook or list-zones): switch fans when the zone crosses the Mid line."""
    if not zone_name:
        return
    try:
        ppm = round(float(ppm), 2) if ppm is not None else None
    except (TypeError, ValueError):
        ppm = None
    need = co_needs_ventilation(risk, ppm)
    async with _co_lock:
        state = zone_co.setdefault(zone_name, {"ventilating": False, "fans_on": []})
        was = state.get("ventilating", False)
        needs_safe_check = not state.get("safe_reconciled", False)
        state.update(ppm=ppm, risk=risk, updated=time.time(), ventilating=need)
        if need:
            state["safe_reconciled"] = False
        if need:
            # also re-run while ventilating: a fan repaired / turned off by maintenance goes back ON
            await ventilate_zone(zone_name, True, risk, ppm)
        elif was or needs_safe_check:
            # Reconcile once even after a restart: an already-running fan must turn off at Safe.
            if await ventilate_zone(zone_name, False, risk, ppm):
                state["safe_reconciled"] = True


async def co_monitor_worker():
    """Backup for webhooks: reads list-zones, catches missed alerts and turns fans off once Safe."""
    print("[CO MONITOR] Started (ventilate from Mid; off when Safe).")
    while True:
        try:
            zones = await call_simulator_api("list-zones")
            if isinstance(zones, list):
                for z in zones:
                    if isinstance(z, dict) and z.get("name"):
                        await update_zone_co(z["name"], z.get("gasCarbonMonoxideLevel"), z.get("risk"))
        except Exception as e:
            print(f"[CO MONITOR ERROR] {e}")
        active = any(v.get("ventilating") for v in zone_co.values())
        await asyncio.sleep(CO_CHECK_ACTIVE_S if active else CO_CHECK_IDLE_S)


# -------------------------------------------------
# DAY/NIGHT LIGHT CONTROLLER
# -------------------------------------------------

async def auto_light_controller_worker():
    print("[LIGHT CONTROLLER] Day/Night light controller started.")
    while True:
        try:
            hour     = _last_sim_hour
            is_night = not (6 <= hour < 18)
            lights   = await call_simulator_api("list-lights")
            if isinstance(lights, list):
                for light in lights:
                    name = light.get("name")
                    if not name or light.get("broken") or light.get("isUnderMaintenance"):
                        continue
                    currently_on = light.get("isOn", False)
                    if is_night and not currently_on:
                        print(f"[LIGHT] Night -> ON: {name}")
                        await call_simulator_api(f"lights/{name}/on", method="POST")
                    elif not is_night and currently_on:
                        print(f"[LIGHT] Day -> OFF: {name}")
                        await call_simulator_api(f"lights/{name}/off", method="POST")
        except Exception as e:
            print(f"[LIGHT CONTROLLER ERROR] {e}")
        await asyncio.sleep(60.0)


# -------------------------------------------------
# USAGE CYCLE TRACKER
# -------------------------------------------------

async def usage_cycle_tracker_worker():
    print("[USAGE CYCLES] Usage cycle tracker started.")
    while True:
        for endpoint, c_type in [("list-barriers", "gate"), ("list-lights", "light"), ("list-exhaust-fans", "fan")]:
            try:
                items = await call_simulator_api(endpoint)
                if isinstance(items, list):
                    for item in items:
                        n = item.get("name")
                        if n:
                            usage_cycles[n] = {
                                "cycles":       item.get("usageCycles") or item.get("cycleCount") or 0,
                                "last_updated": time.time(),
                                "type":         c_type,
                            }
            except Exception:
                pass
        await asyncio.sleep(30.0)


# -------------------------------------------------
# PREVENTIVE MAINTENANCE WORKER
# -------------------------------------------------

async def _maintain_spot(name: str):
    async with spot_lock:
        is_free     = parking_spots.get(name, True)
        is_assigned = any(info.get("assigned_spot") == name for info in active_cars.values())
    if not is_free or is_assigned:
        return
    print(f"[PREEMPTIVE MAINTENANCE] Spot {name} IDLE - repairing...")
    async with spot_lock:
        parking_spots[name] = False
    component_health[name] = {"broken": False, "under_maintenance": True}
    try:
        await call_simulator_api(f"parking-spots/{name}/repair", method="POST")
        await audit_system("AUTO_REPAIR", "spot", name, reason="preventive maintenance (usage cycles)")
    except Exception as err:
        print(f"[PREEMPTIVE MAINTENANCE ERROR] Spot {name}: {err}")
        await audit_system("AUTO_REPAIR", "spot", name, success=False, error=str(err)[:200])
        async with spot_lock:
            parking_spots[name] = True

async def _maintain_gate(name: str):
    q = gate_queues.get(name)
    if not q or not q.empty():
        return
    print(f"[PREEMPTIVE MAINTENANCE] Gate {name} IDLE - repairing...")
    component_health[name] = {"broken": False, "under_maintenance": True}
    try:
        await call_simulator_api(f"barrier-gates/{name}/repair", method="POST")
        await audit_system("AUTO_REPAIR", "gate", name, reason="preventive maintenance (usage cycles)")
    except Exception as err:
        print(f"[PREEMPTIVE MAINTENANCE ERROR] Gate {name}: {err}")
        await audit_system("AUTO_REPAIR", "gate", name, success=False, error=str(err)[:200])


async def _maintain_fan(name: str):
    print(f"[PREEMPTIVE MAINTENANCE] Fan {name} - turning off and repairing...")
    component_health[name] = {"broken": False, "under_maintenance": True}
    try:
        await call_simulator_api(f"exhaust-fans/{name}/off",    method="POST")
        await call_simulator_api(f"exhaust-fans/{name}/repair", method="POST")
        await audit_system("AUTO_REPAIR", "fan", name, reason="preventive maintenance (usage cycles)")
    except Exception as err:
        print(f"[PREEMPTIVE MAINTENANCE ERROR] Fan {name}: {err}")
        await audit_system("AUTO_REPAIR", "fan", name, success=False, error=str(err)[:200])


async def auto_preemptive_maintenance_worker():
    print("[MAINTENANCE SCHEDULER] Preventive maintenance worker started.")
    while True:
        try:
            alarms = await call_simulator_api("list-alarms")
            if isinstance(alarms, list):
                for alarm in alarms:
                    name    = alarm.get("name", "")
                    problem = str(alarm.get("problem", ""))
                    if "maintenance" not in problem.lower():
                        continue
                    if component_health.get(name, {}).get("under_maintenance"):
                        continue
                    if name.startswith("S") or name.startswith("bay") or name.startswith("P"):
                        await _maintain_spot(name)
                    elif name.startswith("gate"):
                        await _maintain_gate(name)
                    elif name.startswith("f_") or name.startswith("fan"):
                        await _maintain_fan(name)
        except Exception:
            pass
        # Sync barrier health from simulator
        try:
            barriers = await call_simulator_api("list-barriers")
            if isinstance(barriers, list):
                for b in barriers:
                    n = b.get("name")
                    if n:
                        component_health[n] = {
                            "broken":            b.get("broken", False),
                            "under_maintenance": b.get("isUnderMaintenance", False),
                        }
        except Exception:
            pass
        # Recover repaired spots
        try:
            spots_list = await call_simulator_api("list-parking-spots")
            if isinstance(spots_list, list):
                for s in spots_list:
                    s_name = s.get("name")
                    if not s_name or s_name not in component_health:
                        continue
                    if not s.get("isUnderMaintenance", False) and not s.get("broken", False):
                        component_health.pop(s_name, None)
                        if not s.get("isOccupied", False):
                            async with spot_lock:
                                parking_spots[s_name] = True
        except Exception:
            pass
        await asyncio.sleep(5.0)


# -------------------------------------------------
# BUSINESS LOGIC - CAR ENTRY
# -------------------------------------------------

async def process_car_entry(data: dict):
    global _last_sim_hour
    car_plate   = data.get("CarPlateNumber", "")
    car_type    = data.get("CarType", "Normal")
    spot_name   = data.get("SpotName", "")
    planned_dur = float(data.get("PlannedParkingDurationInMinutes", 1) or 1)
    server_time = data.get("ServerDateTime", "")

    try:
        _last_sim_hour = int(str(server_time).split(" ")[1].split(":")[0])
    except Exception:
        pass

    recent_car_arrivals.append({
        "car_plate": car_plate, "car_type": car_type, "spot_name": spot_name,
        "parking_duration": planned_dur, "event_time": server_time, "raw_event": data,
    })
    if len(recent_car_arrivals) > 50:
        recent_car_arrivals.pop(0)

    print(f"[ENTRY] {car_plate} ({car_type}) at {spot_name}.")

    # RE-ENTRY GUARD: rerouted car arriving at correct gate
    existing = active_cars.get(car_plate)
    if existing and existing.get("assigned_spot"):
        assigned_spot = existing["assigned_spot"]
        current_gate  = gate_for_entry_spot(spot_name)
        print(f"[ENTRY REROUTE] {car_plate} -> {current_gate} for pre-assigned {assigned_spot}.")
        await gate_queues[current_gate].put({
            "car_plate": car_plate, "gate_name": current_gate, "destination": assigned_spot,
        })
        return

    try:
        g1_ok, g3_ok, g5_ok = await asyncio.gather(
            is_component_operable("gate1"),
            is_component_operable("gate3"),
            is_component_operable("gate5"),
        )
        gate_operable = {"gate1": g1_ok, "gate3": g3_ok, "gate5": g5_ok}

        if not any(gate_operable.values()):
            print(f"[ENTRY] ALL entrance gates inoperable -> leavepark {car_plate}.")
            await api_send_car_to_destination(car_plate, "leavepark")
            return

        norm_type = normalize_vehicle_type(car_type)
        assigned_spot = target_gate = target_entry = None
        zone1_spots = zone2_spots = zone3_spots = []

        # Reserve a spot atomically: concurrent entrance webhooks must not choose the same one.
        async with spot_lock:
            all_free = [s for s, free in parking_spots.items() if free]
            eligible = []
            if norm_type == "Electric":
                eligible += [s for s in all_free if get_spot_type(s) == "Electric"]
            elif norm_type == "Accessible":
                eligible += [s for s in all_free if get_spot_type(s) == "Accessible"]
            eligible += [s for s in all_free if get_spot_type(s) == "Any"]

            operable = [s for s in eligible if gate_operable.get(spot_gate(s))]
            zone1_spots = [s for s in operable if s.startswith("S")]
            zone2_spots = [s for s in operable if s.startswith("bay")]
            zone3_spots = [s for s in operable if s.startswith("P")]

            if zone1_spots:
                assigned_spot = sorted(zone1_spots, key=extract_spot_number)[0]
                target_gate, target_entry = "gate1", "ENTRY1"
            elif zone2_spots:
                assigned_spot = sorted(zone2_spots, key=extract_spot_number)[0]
                target_gate, target_entry = "gate3", "ENTRY2"
            elif zone3_spots:
                assigned_spot = sorted(zone3_spots, key=extract_spot_number)[0]
                target_gate, target_entry = "gate5", "ENTRY3"

            if assigned_spot:
                parking_spots[assigned_spot] = False
                active_cars[car_plate] = {
                    "entry_time": server_time, "car_type": car_type,
                    "assigned_spot": assigned_spot, "assigned_ts": time.time(),
                    "planned_duration": planned_dur, "charged": False, "parked": False,
                }

        if assigned_spot:
            current_gate = gate_for_entry_spot(spot_name)
            print(
                f"[ROUTING] {car_plate}: current={spot_name}/{current_gate}, "
                f"assigned={assigned_spot}, target={target_entry}/{target_gate}"
            )

            if current_gate == target_gate:
                print(f"[ENTRY] {car_plate} at correct {current_gate} -> {assigned_spot}.")
                await gate_queues[current_gate].put({
                    "car_plate": car_plate,
                    "gate_name": current_gate,
                    "destination": assigned_spot,
                })
            else:
                print(
                    f"[ENTRY REROUTE] {car_plate} at {current_gate}, "
                    f"needs {target_gate} -> {target_entry}."
                )

                if current_gate == "gate3" and zone1_spots:
                    await api_close_barrier_gate("gate3")
                elif current_gate == "gate5" and (zone1_spots or zone2_spots):
                    await api_close_barrier_gate("gate5")
                await api_send_car_to_destination(car_plate, target_entry)
        else:
            print(f"[ENTRY] No spot for {car_plate} ({norm_type}) -> leavepark.")
            open_gate    = next((g for g in ENTRANCE_GATES if gate_operable.get(g)), None)
            current_gate = gate_for_entry_spot(spot_name)
            if open_gate and current_gate == open_gate:
                await gate_queues[open_gate].put({
                    "car_plate": car_plate, "gate_name": open_gate, "destination": "leavepark",
                })
            else:
                await api_send_car_to_destination(car_plate, "leavepark")

    except Exception as e:
        print(f"[ENTRY ERROR] {car_plate}: {e}")


# -------------------------------------------------
# BUSINESS LOGIC - CAR EXIT
# -------------------------------------------------

async def process_car_exit(data: dict):
    global _last_sim_hour
    car_plate   = data.get("CarPlateNumber","") or data.get("CarPlate") or data.get("car_plate") or ""
    spot_name   = data.get("SpotName","") or "EXIT1"
    server_time = data.get("ServerDateTime","")

    if not car_plate:
        return

    car_info  = active_cars.get(car_plate, {})
    raw_type  = data.get("CarType") or car_info.get("car_type","Normal")
    norm_type = normalize_vehicle_type(raw_type)
    is_ev     = (norm_type == "Electric")

    try:
        _last_sim_hour = int(str(server_time).split(" ")[1].split(":")[0])
    except Exception:
        pass

    print(f"[EXIT] {car_plate} ({norm_type}) at {spot_name}.")

    duration = float(car_info.get("planned_duration", 0) or 0)
    if duration <= 0:
        for evt in reversed(webhook_events):
            p = evt.get("CarPlateNumber") or evt.get("CarPlate") or evt.get("car_plate")
            if p and p.strip().upper() == car_plate.strip().upper():
                d = float(evt.get("PlannedParkingDurationInMinutes") or 0)
                if d > 0:
                    duration = d; break
    if duration <= 0:
        entry_t = car_info.get("entry_time")
        if entry_t and server_time:
            try:
                fmt = "%Y-%m-%d %H:%M:%S"
                duration = max(1, math.ceil(
                    (datetime.strptime(str(server_time), fmt) -
                     datetime.strptime(str(entry_t), fmt)).total_seconds() / 60))
            except Exception:
                pass
    if duration <= 0:
        duration = 1.0

    exit_gate  = exit_gate_for_spot(spot_name)
    payment_ok = car_plate in charged_cars

    if not payment_ok:
        parking_cost  = float(max(1, round(duration)))
        charging_cost = float(parking_cost * 2.0) if is_ev else 0.0
        print(f"[EXIT] Charging {car_plate}: parking=${parking_cost}, EV=${charging_cost}")
        try:
            await api_charge_car(car_plate, parking_cost, charging_cost)
            charged_cars.add(car_plate)
            if car_plate in active_cars:
                active_cars[car_plate]["charged"] = True
            payment_ok = True
            print(f"[EXIT] Payment confirmed for {car_plate}.")
        except Exception as e:
            print(f"[EXIT ERROR] Charge failed for {car_plate}: {e}")
    else:
        print(f"[EXIT] {car_plate} already charged.")

    if payment_ok:
        freed = car_info.get("assigned_spot") or car_info.get("parked_spot")
        if freed and freed in parking_spots:
            async with spot_lock:
                parking_spots[freed] = True
            print(f"[EXIT] Spot {freed} freed.")
        if car_plate in active_cars:
            active_cars[car_plate]["assigned_spot"] = None
        print(f"[EXIT] Queuing {car_plate} for exit via {exit_gate}.")
        await gate_queues[exit_gate].put({"car_plate": car_plate, "gate_name": exit_gate})
    else:
        print(f"[EXIT] Payment NOT confirmed for {car_plate} - gate stays CLOSED.")


# -------------------------------------------------
# STARTUP
# -------------------------------------------------

@app.on_event("startup")
async def startup_event():
    for g in ENTRANCE_GATES:
        asyncio.create_task(dedicated_entrance_gate_worker(g, gate_queues[g]))
    for g in EXIT_GATES:
        asyncio.create_task(dedicated_exit_gate_worker(g, gate_queues[g]))
    asyncio.create_task(auto_preemptive_maintenance_worker())
    asyncio.create_task(auto_light_controller_worker())
    asyncio.create_task(co_monitor_worker())
    asyncio.create_task(usage_cycle_tracker_worker())
    print("[STARTUP] Closing all 6 gates...")
    try:
        await asyncio.gather(*[
            call_simulator_api(f"barrier-gates/{g}/close", method="POST")
            for g in ALL_GATES
        ], return_exceptions=True)
    except Exception as e:
        print(f"[STARTUP NOTE] {e}")
    print(f"[STARTUP] Level 2 ready. Entrance: {ENTRANCE_GATES} | Exit: {EXIT_GATES} | Spots: {len(VALID_PARKING_SPOTS)}")


# -------------------------------------------------
# WEBHOOK
# -------------------------------------------------

@app.get("/webhook")
@app.get("/webhook.php")
def webhook_info():
    return {"message": "Webhook endpoint is active."}


@app.post("/webhook")
@app.post("/webhook.php")
async def webhook(request: Request):
    global _last_sim_hour
    data = await request.json()
    await db_hook.record(request)
    print(f"[WEBHOOK] {data}")
    webhook_events.append(data)
    if len(webhook_events) > 100:
        webhook_events.pop(0)

    try:
        _last_sim_hour = int(str(data.get("ServerDateTime","")).split(" ")[1].split(":")[0])
    except Exception:
        pass

    event_class = data.get("EventClass","")
    spot_type   = data.get("SpotType","")
    spot_name   = data.get("SpotName","")
    direction   = data.get("Direction","")
    car_plate   = data.get("CarPlateNumber") or data.get("CarPlate") or data.get("car_plate") or ""

    if event_class in ("component_broken","component_failure"):
        comp = data.get("Name") or data.get("ComponentName")
        if comp:
            component_health[comp] = {"broken": True, "under_maintenance": False}
            print(f"[COMPONENT] {comp} BROKEN.")

    elif event_class in ("component_fixed","component_repaired"):
        comp = data.get("Name") or data.get("ComponentName")
        if comp:
            component_health[comp] = {"broken": False, "under_maintenance": False}
            if comp in parking_spots:
                async with spot_lock:
                    parking_spots[comp] = True
            print(f"[COMPONENT] {comp} REPAIRED.")

    elif event_class in ("component_maintenance","component_under_maintenance"):
        comp = data.get("Name") or data.get("ComponentName")
        if comp:
            component_health[comp] = {"broken": False, "under_maintenance": True}

    elif event_class in ("payment_made","payment_received"):
        pay_plate = data.get("CarPlateNumber") or data.get("CarPlate")
        if pay_plate:
            charged_cars.add(pay_plate)
            if pay_plate in active_cars:
                active_cars[pay_plate]["charged"] = True

    elif event_class in ("carbon_monoxide_event","carbon_monoxide_level_change"):
        zone_name = data.get("ZoneName","ZONE1")
        co_level  = data.get("CarbonMonoxideLevel")
        danger    = data.get("DangerLevel")   # Safe / Mid / High / Critical (webhooks come from Mid up)
        print(f"[CO] Zone {zone_name}: {co_level} ppm, {danger}")
        asyncio.create_task(update_zone_co(zone_name, co_level, danger))

    elif event_class == "penalty":
        print(f"[PENALTY] {data.get('CarPlateNumber')}: {data.get('Reason')} - ${data.get('FineAmount')}")

    # Ground-truth parking sync
    if spot_type == "Park" or (spot_name and spot_name in parking_spots):
        async with spot_lock:
            if direction == "CarIn":
                parking_spots[spot_name] = False
                if car_plate:
                    if car_plate not in active_cars:
                        active_cars[car_plate] = {
                            "entry_time":       data.get("ServerDateTime",""),
                            "car_type":         data.get("CarType","Normal"),
                            "assigned_spot":    spot_name,
                            "parked_spot":      spot_name,
                            "planned_duration": float(data.get("PlannedParkingDurationInMinutes",1) or 1),
                            "charged": False, "parked": True,
                        }
                    else:
                        active_cars[car_plate]["assigned_spot"] = spot_name
                        active_cars[car_plate]["parked_spot"]   = spot_name
                        active_cars[car_plate]["parked"]        = True
                print(f"[SENSOR] {spot_name} OCCUPIED by {car_plate}.")
            elif direction == "CarOut":
                parking_spots[spot_name] = True
                for p, info in list(active_cars.items()):
                    if info.get("assigned_spot") == spot_name:
                        info["assigned_spot"] = None
                print(f"[SENSOR] {spot_name} FREE.")

    if (spot_type == "EntrySpot" or (spot_name and spot_name.upper().startswith("ENTRY"))) and direction == "CarOut":
        asyncio.create_task(auto_close_gate_after_delay(gate_for_entry_spot(spot_name), 0.3))

    if (spot_type == "ExitSpot" or (spot_name and spot_name.upper().startswith("EXIT"))) and direction == "CarOut":
        if car_plate:
            active_cars.pop(car_plate, None)
            charged_cars.discard(car_plate)
        asyncio.create_task(auto_close_gate_after_delay(exit_gate_for_spot(spot_name), 0.3))

    if not car_plate and spot_name and direction == "CarOut":
        car_plate = next(
            (p for p, info in active_cars.items()
             if info.get("assigned_spot") == spot_name or info.get("parked_spot") == spot_name), ""
        )

    if (spot_type == "EntrySpot" or (spot_name and spot_name.upper().startswith("ENTRY"))) and direction == "CarIn":
        asyncio.create_task(process_car_entry(data))
    elif (spot_type == "ExitSpot" or (spot_name and spot_name.upper().startswith("EXIT"))) and direction == "CarIn":
        asyncio.create_task(process_car_exit(data))

    return {
        "status":           "dispatched",
        "gate_queue_sizes": {g: q.qsize() for g, q in gate_queues.items()},
        "free_spots":       sum(1 for v in parking_spots.values() if v),
    }


# -------------------------------------------------
# AUTH ROUTE
# -------------------------------------------------

@app.post("/auth/login")
async def test_login(body: LoginRequest):
    token = await api_login(body.email, body.password)
    return {"status": "ok", "email": body.email,
            "token_preview": f"{token[:25]}...", "token_length": len(token)}


# -------------------------------------------------
# DASHBOARD DATA ROUTES
# -------------------------------------------------

@app.get("/recent-arrivals")
def get_recent_arrivals(limit: int = 10):
    return {"count": len(recent_car_arrivals), "recent_arrivals": recent_car_arrivals[-limit:]}


@app.get("/all-events")
def get_all_events(limit: int = 20):
    return {"count": len(webhook_events), "events": webhook_events[-limit:]}


@app.get("/recent-activity")
def get_recent_activity(limit: int = 25):
    items = []
    for evt in reversed(webhook_events[-80:]):
        event_class = evt.get("EventClass","")
        server_dt   = evt.get("ServerDateTime") or evt.get("Timestamp") or "Recent"
        time_str    = server_dt.split(" ")[-1] if " " in str(server_dt) else str(server_dt)
        evt_id      = evt.get("EventId") or str(len(items)+1)
        seq_id      = evt.get("SequenceId")

        if event_class == "car_spot_action":
            plate     = evt.get("CarPlateNumber") or evt.get("CarPlate") or "Unknown"
            s_type    = evt.get("SpotType","")
            s_name    = evt.get("SpotName","")
            direction = evt.get("Direction","")
            if s_type == "EntrySpot" or "ENTRY" in s_name.upper():
                event_text  = "Arrived at Entrance" if direction=="CarIn" else "Passed Entrance Gate"
                status_text = "Queued" if direction=="CarIn" else "In Transit"
            elif s_type == "Park":
                event_text  = f"Parked in {s_name}" if direction=="CarIn" else f"Left spot {s_name}"
                status_text = "Parked" if direction=="CarIn" else "Departing"
            elif s_type == "ExitSpot" or "EXIT" in s_name.upper():
                event_text  = "Arrived at Exit Gate" if direction=="CarIn" else "Exited Car Park"
                status_text = "Processing" if direction=="CarIn" else "Departed"
            else:
                event_text  = f"{s_type} {direction}"
                status_text = "Active"
            items.append({"id":evt_id,"sequenceId":seq_id,"category":"car","plate":plate,
                          "event":event_text,"spot":s_name or "—","time":time_str,"status":status_text})
        elif event_class == "gate_action":
            gate_name = evt.get("Name","Gate")
            action    = evt.get("Action","Operated")
            items.append({"id":evt_id,"sequenceId":seq_id,"category":"gate","plate":f"[{gate_name}]",
                          "event":f"Gate {gate_name} {action}","spot":gate_name,"time":time_str,
                          "status":"Open" if action.lower() in ("open","opened") else "Closed"})
        elif event_class == "penalty":
            plate  = evt.get("CarPlateNumber") or evt.get("ComponentName") or "Unknown"
            reason = evt.get("Reason","Violation")
            fine   = evt.get("FineAmount","0")
            items.append({"id":evt_id,"sequenceId":seq_id,"category":"alert","plate":plate,
                          "event":f"Penalty: {reason} (${fine})","spot":evt.get("ComponentName","—"),
                          "time":time_str,"status":"Penalty"})
        elif event_class in ("carbon_monoxide_event","carbon_monoxide_level_change"):
            zone  = evt.get("ZoneName","")
            level = evt.get("CarbonMonoxideLevel",0)
            items.append({"id":evt_id,"sequenceId":seq_id,"category":"alert","plate":"[CO SENSOR]",
                          "event":f"CO {level} ppm in {zone}","spot":zone,"time":time_str,"status":"Alert"})
        elif event_class:
            items.append({"id":evt_id,"sequenceId":seq_id,"category":"system",
                          "plate":evt.get("CarPlateNumber") or "[SYSTEM]",
                          "event":event_class.replace("_"," ").title(),
                          "spot":evt.get("Name") or evt.get("ComponentName") or "—",
                          "time":time_str,"status":"Alert"})
        if len(items) >= limit:
            break
    return items


@app.get("/co-status")
async def co_status():
    """Latest CO reading per zone as the controller sees it (no simulator call)."""
    return [
        {"name": zone, "gasCarbonMonoxideLevel": v.get("ppm"), "risk": v.get("risk"),
         "ventilating": v.get("ventilating", False), "fansOn": v.get("fans_on", []),
         "updated": v.get("updated"), "ventilateFrom": CO_VENTILATE_FROM}
        for zone, v in sorted(zone_co.items())
    ]


@app.get("/system-status")
async def get_system_status():
    sim_online = False
    try:
        await call_simulator_api("list-parking-spots")
        sim_online = True
    except Exception:
        pass

    async with spot_lock:
        total    = len(parking_spots)
        free     = sum(1 for v in parking_spots.values() if v)
        occupied = total - free
        z1_free  = sum(1 for s,v in parking_spots.items() if s.startswith("S")   and v)
        z2_free  = sum(1 for s,v in parking_spots.items() if s.startswith("bay") and v)
        z3_free  = sum(1 for s,v in parking_spots.items() if s.startswith("P")   and v)

    return {
        "backend": "online", "simulator": "online" if sim_online else "offline",
        "total_spots": total, "available_spots": free, "occupied_spots": occupied,
        "cars_inside": len(active_cars), "park_full": free == 0,
        "broken_components": sum(1 for h in component_health.values() if h.get("broken")),
        "zone_free": {"zone1": z1_free, "zone2": z2_free, "zone3": z3_free},
    }


@app.get("/active-cars")
def get_active_cars():
    cars = []
    for plate, info in active_cars.items():
        entry_raw    = info.get("entry_time","")
        display_time = "Recently"
        elapsed_str  = "Active"
        if isinstance(entry_raw, str) and entry_raw:
            display_time = entry_raw.split(" ")[-1] if " " in entry_raw else entry_raw
            try:
                dt          = datetime.strptime(entry_raw, "%Y-%m-%d %H:%M:%S")
                elapsed_str = f"{max(0, int((datetime.now()-dt).total_seconds()/60))}m"
            except Exception:
                pass
        cars.append({
            "plateNumber": plate, "vehicleType": info.get("car_type","Standard"),
            "brand": "Standard", "model": "Car", "colour": "Silver",
            "assignedSpot": info.get("assigned_spot","—"),
            "parkingSpot":  info.get("parked_spot") or info.get("assigned_spot","—"),
            "entryTime": display_time, "duration": elapsed_str,
            "plannedDuration": info.get("planned_duration",0),
            "charged": info.get("charged",False), "parked": info.get("parked",False),
            "status": "Exiting" if info.get("charged") else ("Parked" if info.get("parked") else "Arriving"),
        })
    return {"count": len(cars), "cars": cars}


@app.get("/vehicles/{plate}")
def get_vehicle_details(plate: str):
    info = active_cars.get(plate)
    if info:
        entry_raw    = info.get("entry_time","")
        display_time = str(entry_raw)
        elapsed_str  = "Active"
        if isinstance(entry_raw, str) and entry_raw:
            try:
                dt           = datetime.strptime(entry_raw, "%Y-%m-%d %H:%M:%S")
                display_time = dt.strftime("%I:%M %p")
                elapsed_str  = f"{max(0, int((datetime.now()-dt).total_seconds()/60))}m"
            except Exception:
                display_time = entry_raw
        return {
            "found": True, "plateNumber": plate,
            "vehicleType": info.get("car_type","Sedan"),
            "brand":"Standard","model":"Car","colour":"Silver",
            "parkingSpot": info.get("parked_spot") or info.get("assigned_spot","—"),
            "status":"Exiting" if info.get("charged") else ("Parked" if info.get("parked") else "Arriving"),
            "entryTime":display_time,"exitTime":"—","duration":elapsed_str,
            "charged":info.get("charged",False),
        }
    for arr in reversed(recent_car_arrivals):
        if arr.get("car_plate","").lower() == plate.lower():
            return {
                "found":True,"plateNumber":arr.get("car_plate"),
                "vehicleType":arr.get("car_type","Sedan"),
                "brand":"Standard","model":"Car","colour":"White",
                "parkingSpot":arr.get("spot_name","—"),"status":"Arrived",
                "entryTime":str(arr.get("event_time","Recent")).split(" ")[-1],
                "exitTime":"—","duration":"Recently entered","charged":False,
            }
    for evt in reversed(webhook_events):
        p = evt.get("CarPlateNumber") or evt.get("CarPlate") or evt.get("car_plate")
        if p and p.lower() == plate.lower():
            return {
                "found":True,"plateNumber":p,"vehicleType":evt.get("CarType","Sedan"),
                "brand":"Vehicle","model":"Standard","colour":"Grey",
                "parkingSpot":evt.get("SpotName","—"),
                "status":"Exited" if evt.get("SpotType")=="ExitSpot" else "Active",
                "entryTime":evt.get("ServerDateTime","Earlier"),
                "exitTime":"—","duration":"Completed","charged":True,
            }
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
                return {
                    "found":True,"plateNumber":sess.car_plate,
                    "vehicleType":getattr(sess,"car_type","Sedan") or "Sedan",
                    "brand":"Standard","model":"Car","colour":"Silver",
                    "parkingSpot":s_name or "—",
                    "status":"Exited" if sess.status=="completed" else "Active",
                    "entryTime":sess.entry_time.strftime("%I:%M %p") if sess.entry_time else "Earlier",
                    "exitTime":sess.exit_time.strftime("%I:%M %p") if sess.exit_time else "—",
                    "duration":f"Fee: ${(sess.fee_cents or 0)/100:.2f}" if sess.fee_cents else "Completed",
                    "charged":sess.status=="completed" or bool(sess.fee_cents),
                }
    except Exception:
        pass
    return {"found":False,"plateNumber":plate,"message":f"No records for {plate}"}


# -------------------------------------------------
# COMPONENT HEALTH & USAGE CYCLES (NEW ROUTES)
# -------------------------------------------------

@app.get("/component-health")
def get_component_health():
    broken      = {n:h for n,h in component_health.items() if h.get("broken")}
    maintenance = {n:h for n,h in component_health.items() if h.get("under_maintenance")}
    return {
        "broken": broken, "under_maintenance": maintenance,
        "broken_count": len(broken), "maintenance_count": len(maintenance),
        "all": component_health,
    }


@app.get("/usage-cycles")
def get_usage_cycles():
    return {"cycles": usage_cycles, "total_components": len(usage_cycles)}


# -------------------------------------------------
# SIMULATOR PASSTHROUGH GET ROUTES
# -------------------------------------------------

@app.get("/list-parking-spots")
async def list_parking_spots():
    broken_spots = set()
    maint_spots  = set()
    try:
        sim_spots = await call_simulator_api("list-parking-spots")
        if isinstance(sim_spots, list):
            for s in sim_spots:
                n = s.get("name")
                if n:
                    if s.get("broken"):             broken_spots.add(n)
                    if s.get("isUnderMaintenance"): maint_spots.add(n)
    except Exception as e:
        print(f"[WARN] list-parking-spots: {e}")

    async with spot_lock:
        local_snap = dict(parking_spots)

    spots = []
    zone_defs = [
        ("Zone 1", [f"S{i}"   for i in range(1, 31)]),
        ("Zone 2", [f"bay{i}" for i in range(36, 66)]),
        ("Zone 3", [f"P{i}"   for i in range(69, 99)]),
    ]
    i_global = 0
    for zone_label, names in zone_defs:
        for name in names:
            i_global   += 1
            is_free     = local_snap.get(name, True)
            is_broken   = name in broken_spots or component_health.get(name,{}).get("broken",False)
            is_maint    = name in maint_spots  or component_health.get(name,{}).get("under_maintenance",False)
            is_occupied = not is_free
            assigned_plate = None
            if is_occupied:
                assigned_plate = next(
                    (p for p,info in active_cars.items()
                     if info.get("assigned_spot")==name or info.get("parked_spot")==name), None)
                if not assigned_plate:
                    for evt in reversed(webhook_events):
                        if evt.get("SpotName") == name:
                            assigned_plate = evt.get("CarPlate") or evt.get("car_plate") or evt.get("CarPlateNumber")
                            if assigned_plate: break
            status = "maintenance" if (is_broken or is_maint) else ("occupied" if is_occupied else "free")
            spots.append({
                "name": name, "zone": zone_label, "purpose": "Park",
                "number": i_global, "spotType": get_spot_type(name),
                "isOccupied": is_occupied, "detectedCars": 1 if is_occupied else 0,
                "broken": is_broken, "isUnderMaintenance": is_maint, "isBroken": is_broken,
                "status": status, "carPlateNumber": assigned_plate if is_occupied else None,
            })
    return spots


@app.get("/list-barriers")
async def list_barriers():
    try:
        return await call_simulator_api("list-barriers")
    except Exception:
        return [
            {"name": g, "isOpen": False,
             "isBroken": component_health.get(g,{}).get("broken",False),
             "isUnderMaintenance": component_health.get(g,{}).get("under_maintenance",False),
             "type": "Entrance" if g in ENTRANCE_GATES else "Exit"}
            for g in ALL_GATES
        ]


@app.get("/list-lights")
async def list_lights():
    return await call_simulator_api("list-lights")

@app.get("/list-exhaust-fans")
async def list_exhaust_fans():
    return await call_simulator_api("list-exhaust-fans")

@app.get("/list-alarms")
async def list_alarms():
    return await call_simulator_api("list-alarms")

@app.get("/list-zones")
async def list_zones():
    return await call_simulator_api("list-zones")

@app.get("/test")
async def test():
    return await call_simulator_api("test")


# -------------------------------------------------
# SIMULATOR CONTROL POST ROUTES
# -------------------------------------------------

@app.post("/barrier-gates/{name}/open")
async def open_barrier_gate(name: str):
    return await api_open_barrier_gate(name)

@app.post("/barrier-gates/{name}/close")
async def close_barrier_gate(name: str):
    return await api_close_barrier_gate(name)

@app.post("/barrier-gates/{name}/repair")
async def repair_barrier_gate(name: str):
    return await call_simulator_api(f"barrier-gates/{name}/repair", method="POST")

@app.post("/lights/{name}/on")
async def turn_on_light(name: str):
    return await call_simulator_api(f"lights/{name}/on", method="POST")

@app.post("/lights/{name}/off")
async def turn_off_light(name: str):
    return await call_simulator_api(f"lights/{name}/off", method="POST")

@app.post("/lights/group/{name}/on")
async def turn_on_light_group(name: str):
    return await call_simulator_api(f"lights/group/{name}/on", method="POST")

@app.post("/lights/group/{name}/off")
async def turn_off_light_group(name: str):
    return await call_simulator_api(f"lights/group/{name}/off", method="POST")

@app.post("/exhaust-fans/{name}/repair")
async def repair_exhaust_fan(name: str):
    return await call_simulator_api(f"exhaust-fans/{name}/repair", method="POST")

@app.post("/exhaust-fans/{name}/on")
async def turn_on_exhaust_fan(name: str):
    return await call_simulator_api(f"exhaust-fans/{name}/on", method="POST")

@app.post("/exhaust-fans/{name}/off")
async def turn_off_exhaust_fan(name: str):
    return await call_simulator_api(f"exhaust-fans/{name}/off", method="POST")

@app.post("/parking-spots/{name}/repair")
async def repair_parking_spot(name: str):
    return await call_simulator_api(f"parking-spots/{name}/repair", method="POST")

@app.post("/car/{name}/goto/{destination}")
async def car_goto(name: str, destination: str):
    return await api_send_car_to_destination(name, destination)

@app.post("/car/{name}/charge")
async def car_charge(name: str, parking_cost: float = 0.0, charging_cost: float = 0.0):
    return await api_charge_car(name, parking_cost, charging_cost)

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
        
