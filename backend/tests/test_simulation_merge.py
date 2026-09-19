"""Focused car-flow checks for the combined simulator branch, without a real simulator."""

import asyncio

import main


def test_concurrent_arrivals_reserve_different_spots(monkeypatch):
    monkeypatch.setattr(main, "parking_spots", {"S1": True, "S2": True})
    monkeypatch.setattr(main, "active_cars", {})
    monkeypatch.setattr(main, "recent_car_arrivals", [])
    monkeypatch.setattr(main, "component_health", {})
    monkeypatch.setattr(main, "spot_lock", asyncio.Lock())
    queues = {gate: asyncio.Queue() for gate in main.ALL_GATES}
    monkeypatch.setattr(main, "gate_queues", queues)

    async def arrive(plate):
        await main.process_car_entry({
            "CarPlateNumber": plate, "CarType": "Normal", "SpotName": "ENTRY1",
            "ServerDateTime": "2026-09-20 08:00:00",
        })

    async def run_both():
        await asyncio.gather(arrive("CAR-1"), arrive("CAR-2"))

    asyncio.run(run_both())

    assigned = {car["assigned_spot"] for car in main.active_cars.values()}
    assert assigned == {"S1", "S2"}
    assert queues["gate1"].qsize() == 2


def test_electric_car_can_use_any_spot_when_ev_spots_are_full(monkeypatch):
    monkeypatch.setattr(main, "parking_spots", {"S1": True, "S5": False})
    monkeypatch.setattr(main, "active_cars", {})
    monkeypatch.setattr(main, "recent_car_arrivals", [])
    monkeypatch.setattr(main, "component_health", {})
    monkeypatch.setattr(main, "spot_lock", asyncio.Lock())
    queues = {gate: asyncio.Queue() for gate in main.ALL_GATES}
    monkeypatch.setattr(main, "gate_queues", queues)

    asyncio.run(main.process_car_entry({
        "CarPlateNumber": "EV-1", "CarType": "Electric", "SpotName": "ENTRY1",
        "ServerDateTime": "2026-09-20 08:00:00",
    }))

    assert main.active_cars["EV-1"]["assigned_spot"] == "S1"
    assert queues["gate1"].get_nowait()["destination"] == "S1"


def test_blocked_entrance_gate_does_not_send_car(monkeypatch):
    queue = asyncio.Queue()
    queue.put_nowait({"car_plate": "CAR-1", "destination": "S1"})
    commands = []
    monkeypatch.setattr(main, "parking_spots", {"S1": False})
    monkeypatch.setattr(main, "active_cars", {"CAR-1": {"assigned_spot": "S1", "parked": False}})
    monkeypatch.setattr(main, "spot_lock", asyncio.Lock())
    monkeypatch.setattr(main, "component_health", {})

    async def blocked(_):
        commands.append("open")
        return {"status": "blocked"}

    async def send(_, destination):
        commands.append(f"goto:{destination}")

    monkeypatch.setattr(main, "api_open_barrier_gate", blocked)
    monkeypatch.setattr(main, "api_send_car_to_destination", send)

    async def run_one():
        worker = asyncio.create_task(main.dedicated_entrance_gate_worker("gate1", queue))
        await queue.join()
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

    asyncio.run(run_one())
    assert commands == ["open", "goto:leavepark"]
    assert main.parking_spots["S1"] is True
    assert "CAR-1" not in main.active_cars


def test_blocked_gate_redirects_to_compatible_free_spot(monkeypatch):
    queue = asyncio.Queue()
    queue.put_nowait({"car_plate": "EV-1", "destination": "S5"})
    monkeypatch.setattr(main, "parking_spots", {"S5": False, "bay58": True, "bay36": True})
    monkeypatch.setattr(main, "active_cars", {
        "EV-1": {"assigned_spot": "S5", "car_type": "Electric", "parked": False},
    })
    monkeypatch.setattr(main, "spot_lock", asyncio.Lock())
    monkeypatch.setattr(main, "entry_lock", asyncio.Lock())
    monkeypatch.setattr(main, "component_health", {"gate1": {"broken": True}})
    monkeypatch.setattr(main, "entry_occupied", {name: False for name in ("ENTRY1", "ENTRY2", "ENTRY3")})
    monkeypatch.setattr(main, "entry_queues", {name: asyncio.Queue() for name in main.entry_occupied})
    sent = []

    async def blocked(_):
        return {"status": "blocked"}

    async def send(_, destination):
        sent.append(destination)
        return {"status": "success"}

    monkeypatch.setattr(main, "api_open_barrier_gate", blocked)
    monkeypatch.setattr(main, "api_send_car_to_destination", send)

    async def run_one():
        worker = asyncio.create_task(main.dedicated_entrance_gate_worker("gate1", queue))
        await queue.join()
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

    asyncio.run(run_one())
    assert sent == ["ENTRY2"]
    assert main.parking_spots["S5"] is True
    assert main.parking_spots["bay36"] is False
    assert main.parking_spots["bay58"] is True
    assert main.active_cars["EV-1"]["assigned_spot"] == "bay36"


def test_occupied_entry_releases_waiting_cars_in_order(monkeypatch):
    monkeypatch.setattr(main, "entry_lock", asyncio.Lock())
    monkeypatch.setattr(main, "entry_occupied", {"ENTRY1": False, "ENTRY2": True, "ENTRY3": False})
    monkeypatch.setattr(main, "entry_queues", {name: asyncio.Queue() for name in main.entry_occupied})
    sent = []

    async def send(car, destination):
        sent.append((car, destination))
        return {"status": "success"}

    monkeypatch.setattr(main, "api_send_car_to_destination", send)

    async def run_queue():
        await main.send_car_to_entry_or_queue("CAR-1", "ENTRY2", "bay36")
        await main.send_car_to_entry_or_queue("CAR-2", "ENTRY2", "bay37")
        assert sent == []
        await main.process_waiting_entry("ENTRY2")
        await main.process_waiting_entry("ENTRY2")

    asyncio.run(run_queue())
    assert sent == [("CAR-1", "ENTRY2"), ("CAR-2", "ENTRY2")]
    assert main.entry_queues["ENTRY2"].empty()


def test_preventive_spot_repair_remains_available(monkeypatch):
    monkeypatch.setattr(main, "parking_spots", {"S1": True})
    monkeypatch.setattr(main, "active_cars", {})
    monkeypatch.setattr(main, "component_health", {})
    monkeypatch.setattr(main, "spot_lock", asyncio.Lock())
    commands = []

    async def simulator_call(endpoint, method="GET", **_):
        commands.append((endpoint, method))
        return {"status": "success"}

    async def no_audit(*_, **__):
        return None

    monkeypatch.setattr(main, "call_simulator_api", simulator_call)
    monkeypatch.setattr(main, "audit_system", no_audit)

    asyncio.run(main._maintain_spot("S1"))

    assert commands == [("parking-spots/S1/repair", "POST")]
    assert main.component_health["S1"]["under_maintenance"] is True
