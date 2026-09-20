"""Automatic repair decisions with fake simulator responses."""

import asyncio

import main


def setup_maintenance(monkeypatch, *, barriers=None, spots=None, fans=None, alarms=None):
    state = {
        "list-barriers": barriers or [],
        "list-parking-spots": spots or [],
        "list-exhaust-fans": fans or [],
        "list-alarms": alarms or [],
    }
    commands = []

    async def simulator_call(endpoint, method="GET", **_):
        if method == "GET":
            return state[endpoint]
        commands.append((endpoint, method))
        return {"status": "success"}

    async def no_audit(*_, **__):
        return None

    monkeypatch.setattr(main, "call_simulator_api", simulator_call)
    monkeypatch.setattr(main, "audit_system", no_audit)
    monkeypatch.setattr(main, "has_manual_override", lambda _: False)
    monkeypatch.setattr(main, "component_health", {})
    monkeypatch.setattr(main, "barrier_states", {})
    monkeypatch.setattr(main, "spot_details", {})
    monkeypatch.setattr(main, "fan_details", {})
    monkeypatch.setattr(main, "repair_attempts", {})
    monkeypatch.setattr(main, "usage_cycles", {})
    monkeypatch.setattr(main, "zone_co", {})
    monkeypatch.setattr(main, "active_cars", {})
    monkeypatch.setattr(main, "parking_spots", {"S1": True})
    monkeypatch.setattr(main, "spot_lock", asyncio.Lock())
    monkeypatch.setattr(main, "gate_queues", {gate: asyncio.Queue() for gate in main.ALL_GATES})
    monkeypatch.setattr(main, "entry_occupied", {entry: False for entry in main.entry_occupied})
    return state, commands


def test_broken_closed_gate_repairs_once_per_cooldown(monkeypatch):
    _, commands = setup_maintenance(
        monkeypatch,
        barriers=[{"name": "gate1", "state": "Closed", "broken": True, "isUnderMaintenance": False}],
    )

    asyncio.run(main.maintenance_scan_once())
    asyncio.run(main.maintenance_scan_once())

    assert commands == [("barrier-gates/gate1/repair", "POST")]
    assert main.usage_cycles["gate1"]["type"] == "gate"


def test_busy_or_open_gate_waits_until_safe(monkeypatch):
    state, commands = setup_maintenance(
        monkeypatch,
        barriers=[{"name": "gate1", "state": "Open", "broken": True}],
    )
    main.gate_queues["gate1"].put_nowait({"car_plate": "WAITING"})

    asyncio.run(main.maintenance_scan_once())
    assert commands == []

    main.gate_queues["gate1"].get_nowait()
    state["list-barriers"][0]["state"] = "Closed"
    asyncio.run(main.maintenance_scan_once())
    assert commands == [("barrier-gates/gate1/repair", "POST")]


def test_broken_spot_repairs_after_car_leaves(monkeypatch):
    state, commands = setup_maintenance(
        monkeypatch,
        spots=[{"name": "S1", "isOccupied": True, "broken": True, "detectedCars": ["CAR"]}],
    )

    asyncio.run(main.maintenance_scan_once())
    assert commands == []

    state["list-parking-spots"][0].update(isOccupied=False, detectedCars=[])
    asyncio.run(main.maintenance_scan_once())
    assert commands == [("parking-spots/S1/repair", "POST")]
    assert main.usage_cycles["S1"]["type"] == "spot"


def test_working_fan_waits_during_high_co_but_broken_fan_repairs(monkeypatch):
    state, commands = setup_maintenance(
        monkeypatch,
        fans=[{"name": "fan1", "zoneParent": "ZONE1", "isOn": True, "broken": False}],
        alarms=[{"name": "fan1", "problem": "maintenance due"}],
    )
    asyncio.run(main.maintenance_scan_once())
    assert commands == []

    main.zone_co["ZONE1"] = {"risk": "High", "ppm": 70}
    asyncio.run(main.maintenance_scan_once())
    assert commands == []

    state["list-exhaust-fans"][0]["broken"] = True
    asyncio.run(main.maintenance_scan_once())
    assert commands == [("exhaust-fans/fan1/repair", "POST")]
