"""Fast, collision-free entry routing and broken-fan recovery."""

import asyncio
import time
import zlib

import main


def reset_entry_state(monkeypatch, spots):
    monkeypatch.setattr(main, "parking_spots", spots)
    monkeypatch.setattr(main, "active_cars", {})
    monkeypatch.setattr(main, "recent_car_arrivals", [])
    monkeypatch.setattr(main, "component_health", {})
    monkeypatch.setattr(main, "spot_lock", asyncio.Lock())
    monkeypatch.setattr(main, "entry_lock", asyncio.Lock())
    monkeypatch.setattr(main, "entry_occupied", {entry: False for entry in ("ENTRY1", "ENTRY2", "ENTRY3")})
    monkeypatch.setattr(main, "entry_car", {entry: None for entry in ("ENTRY1", "ENTRY2", "ENTRY3")})
    monkeypatch.setattr(main, "entry_queues", {entry: asyncio.Queue() for entry in ("ENTRY1", "ENTRY2", "ENTRY3")})
    monkeypatch.setattr(main, "gate_queues", {gate: asyncio.Queue() for gate in main.ALL_GATES})
    monkeypatch.setattr(main, "gate_inflight", {})


def test_plate_routes_from_entry1_to_zone2_once(monkeypatch):
    reset_entry_state(monkeypatch, {"S1": True, "bay36": True, "P69": True})
    plate = next(f"ZONE-{n}" for n in range(1000)
                 if zlib.crc32(f"ZONE-{n}".encode()) % 3 == 1)
    destinations = []

    async def send(_plate, destination):
        destinations.append(destination)
        return {"status": "success"}

    monkeypatch.setattr(main, "api_send_car_to_destination", send)
    arrival = {"CarPlateNumber": plate, "CarType": "Normal", "SpotName": "ENTRY1",
               "ServerDateTime": "2026-09-20 08:00:00"}

    async def run():
        await main.process_car_entry(arrival)
        await main.process_car_entry(arrival)  # duplicate webhook must not queue gate1
        await main.process_car_entry({**arrival, "SpotName": "ENTRY2"})
        await main.process_car_entry({**arrival, "SpotName": "ENTRY2"})

    asyncio.run(run())
    assert destinations == ["ENTRY2"]
    assert main.active_cars[plate]["assigned_spot"] == "bay36"
    assert main.gate_queues["gate1"].empty()
    assert main.gate_queues["gate3"].qsize() == 1


def test_gate_moves_first_car_without_fixed_delay_and_waits_for_carout(monkeypatch):
    reset_entry_state(monkeypatch, {"S1": False, "S2": False})
    queue = main.gate_queues["gate1"]
    queue.put_nowait({"car_plate": "CAR-1", "destination": "S1"})
    queue.put_nowait({"car_plate": "CAR-2", "destination": "S2"})
    moves = []

    async def open_gate(_gate):
        return {"status": "success"}

    async def send(plate, destination):
        moves.append((plate, destination, time.monotonic()))
        return {"status": "success"}

    monkeypatch.setattr(main, "api_open_barrier_gate", open_gate)
    monkeypatch.setattr(main, "api_send_car_to_destination", send)

    async def run():
        started = time.monotonic()
        worker = asyncio.create_task(main.dedicated_entrance_gate_worker("gate1", queue))
        try:
            await asyncio.wait_for(queue.join(), timeout=0.15)
        except asyncio.TimeoutError:
            pass  # second car is deliberately held until the first clears
        assert len(moves) == 1
        assert moves[0][2] - started < 0.5
        main.gate_inflight.pop("gate1")  # EntrySpot CarOut
        await asyncio.wait_for(queue.join(), timeout=0.5)
        assert [(plate, spot) for plate, spot, _ in moves] == [("CAR-1", "S1"), ("CAR-2", "S2")]
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

    asyncio.run(run())


def test_exit_gate_waits_for_carout_before_closing(monkeypatch):
    queue = asyncio.Queue()
    queue.put_nowait({"car_plate": "CAR-1", "gate_name": "gate2"})
    queue.put_nowait({"car_plate": "CAR-2", "gate_name": "gate2"})
    commands = []

    async def open_gate(_gate):
        commands.append("open")

    async def send(_plate, _destination):
        commands.append("goto")

    async def close_gate(_gate):
        commands.append("close")

    monkeypatch.setattr(main, "api_open_barrier_gate", open_gate)
    monkeypatch.setattr(main, "api_send_car_to_destination", send)
    monkeypatch.setattr(main, "api_close_barrier_gate", close_gate)
    monkeypatch.setattr(main, "component_health", {})
    monkeypatch.setattr(main, "exit_inflight", {})

    async def run():
        worker = asyncio.create_task(main.dedicated_exit_gate_worker("gate2", queue))
        try:
            await asyncio.wait_for(queue.join(), timeout=0.15)
        except asyncio.TimeoutError:
            pass
        assert commands == ["open", "goto"]
        main.exit_inflight.pop("gate2")  # ExitSpot CarOut
        await asyncio.wait_for(queue.join(), timeout=0.5)
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert commands == ["open", "goto", "open", "goto"]


def test_unavailable_fan_repairs_and_ventilates(monkeypatch):
    fan = {"name": "fan1", "zoneParent": "ZONE1", "isOn": False,
           "broken": True, "isUnderMaintenance": True}
    commands = []

    async def simulator_call(endpoint, method="GET", **_):
        if method == "GET":
            return {"list-barriers": [], "list-parking-spots": [],
                    "list-exhaust-fans": [fan], "list-alarms": []}[endpoint]
        commands.append(endpoint)
        if endpoint.endswith("/repair"):
            fan.update(broken=False, isUnderMaintenance=False)
        elif endpoint.endswith("/on"):
            fan["isOn"] = True
        return {"status": "success"}

    async def no_audit(*_, **__):
        return None

    monkeypatch.setattr(main, "call_simulator_api", simulator_call)
    monkeypatch.setattr(main, "audit_system", no_audit)
    monkeypatch.setattr(main, "_audit_fans", no_audit)
    monkeypatch.setattr(main, "has_manual_override", lambda _: False)
    monkeypatch.setattr(main, "component_health", {"fan1": {"broken": True, "under_maintenance": True}})
    monkeypatch.setattr(main, "fan_details", {})
    monkeypatch.setattr(main, "spot_details", {})
    monkeypatch.setattr(main, "barrier_states", {})
    monkeypatch.setattr(main, "repair_attempts", {})
    monkeypatch.setattr(main, "zone_co", {"ZONE1": {"risk": "High", "ppm": 75}})

    asyncio.run(main.maintenance_scan_once())
    assert commands == ["exhaust-fans/fan1/repair", "exhaust-fans/fan1/on"]


def test_fan_maintenance_without_breakage_repairs_during_high_co(monkeypatch):
    fan = {"name": "fan1", "zoneParent": "ZONE1", "isOn": False,
           "broken": False, "isUnderMaintenance": True}
    commands = []

    async def simulator_call(endpoint, method="GET", **_):
        if method == "GET":
            return {"list-barriers": [], "list-parking-spots": [],
                    "list-exhaust-fans": [fan], "list-alarms": []}[endpoint]
        commands.append(endpoint)
        if endpoint.endswith("/repair"):
            fan["isUnderMaintenance"] = False
        elif endpoint.endswith("/on"):
            fan["isOn"] = True
        return {"status": "success"}

    async def no_audit(*_, **__):
        return None

    monkeypatch.setattr(main, "call_simulator_api", simulator_call)
    monkeypatch.setattr(main, "audit_system", no_audit)
    monkeypatch.setattr(main, "_audit_fans", no_audit)
    monkeypatch.setattr(main, "has_manual_override", lambda _: False)
    monkeypatch.setattr(main, "component_health", {})
    monkeypatch.setattr(main, "fan_details", {})
    monkeypatch.setattr(main, "spot_details", {})
    monkeypatch.setattr(main, "barrier_states", {})
    monkeypatch.setattr(main, "repair_attempts", {})
    monkeypatch.setattr(main, "zone_co", {"ZONE1": {"risk": "High", "ppm": 75}})

    asyncio.run(main.maintenance_scan_once())
    assert commands == ["exhaust-fans/fan1/repair", "exhaust-fans/fan1/on"]
