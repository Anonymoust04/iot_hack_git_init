"""Level 3 resilience features in main.py: request integrity, sensor faults and
maintenance mode, double parking, vehicle locating, and suspicious payments.

These are pure unit tests: no database and no simulator.
"""

import asyncio
import json
import uuid

import main
import pytest

from app.services.webhook_handlers import compute_signature


def sign(payload: dict) -> dict:
    return {**payload, "Signature": compute_signature({k: str(v) for k, v in payload.items()})}


def body(payload: dict) -> bytes:
    return json.dumps(payload).encode()


def webhook(**fields) -> dict:
    return {"EventClass": "car_spot_action", "EventId": str(uuid.uuid4()),
            "SequenceId": next(_seq), "ServerDateTime": "2026-09-20 10:00:00", **fields}


_seq = iter(range(900_000, 10**9))


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """Each test gets its own copy of the module-level state it touches."""
    monkeypatch.setattr(main, "incidents", main.deque(maxlen=100))
    monkeypatch.setattr(main, "incident_counts", main.Counter())
    monkeypatch.setattr(main, "integrity_stats", main.Counter())
    monkeypatch.setattr(main, "integrity_log", main.deque(maxlen=100))
    monkeypatch.setattr(main, "duplicate_calls", {})
    monkeypatch.setattr(main, "_seen_event_ids", {})
    monkeypatch.setattr(main, "_seen_event_order", main.deque())
    monkeypatch.setattr(main, "_last_sequence_id", None)
    monkeypatch.setattr(main, "_last_sim_time", None)
    monkeypatch.setattr(main, "_source_hits", main.defaultdict(main.deque))
    monkeypatch.setattr(main, "spot_sensor", {})
    monkeypatch.setattr(main, "maintenance_mode", {})
    monkeypatch.setattr(main, "vehicle_tracks", {})
    monkeypatch.setattr(main, "double_parking", {})
    monkeypatch.setattr(main, "payment_state", {})
    monkeypatch.setattr(main, "active_cars", {})
    monkeypatch.setattr(main, "charged_cars", set())
    monkeypatch.setattr(main, "component_health", {})
    monkeypatch.setattr(main, "spot_details", {})
    monkeypatch.setattr(main, "gate_failures", {})
    monkeypatch.setattr(main, "spot_lock", asyncio.Lock())


# ---- request integrity -------------------------------------------------------

def test_a_duplicated_call_is_dropped_and_counted():
    payload = sign(webhook(CarPlateNumber="DUP 1", SpotName="S1", SpotType="Park", Direction="CarIn"))

    _, first, _ = main.check_request_integrity(body(payload), "10.0.0.1")
    _, second, reason = main.check_request_integrity(body(payload), "10.0.0.1")
    _, third, _ = main.check_request_integrity(body(payload), "10.0.0.9")

    assert first is None                      # processed once
    assert second == third == "duplicate"     # never processed again
    assert reason == "duplicate EventId"

    record = main.duplicate_calls[str(payload["EventId"])]
    assert record["copies"] == 3              # the original plus two repeats
    assert record["plate"] == "DUP 1"
    assert record["sources"] == ["10.0.0.1", "10.0.0.9"]
    assert main.integrity_stats["duplicate"] == 2


def test_a_tampered_payload_is_rejected():
    payload = sign(webhook(CarPlateNumber="REAL 1", Amount="2.00"))
    forged = {**payload, "Amount": "999.00"}   # signature no longer matches

    _, verdict, reason = main.check_request_integrity(body(forged), "10.0.0.2")

    assert (verdict, reason) == ("tampered", "bad signature")
    assert main.incident_counts["integrity_rejected"] == 1


def test_malformed_and_unusable_requests_are_rejected():
    _, verdict, _ = main.check_request_integrity(b"<not json>", "10.0.0.3")
    assert verdict == "malformed"

    _, verdict, _ = main.check_request_integrity(body({"EventId": "1"}), "10.0.0.3")
    assert verdict == "malformed"             # no EventClass

    _, verdict, _ = main.check_request_integrity(
        body(webhook(CarPlateNumber="<script>alert(1)</script>")), "10.0.0.3")
    assert verdict == "invalid_field"


def test_flooding_one_source_is_cut_off(monkeypatch):
    monkeypatch.setattr(main, "INTEGRITY_RATE_LIMIT", 5)
    verdicts = [main.check_request_integrity(body(webhook()), "10.0.0.4")[1] for _ in range(8)]
    assert verdicts[:5] == [None] * 5
    assert set(verdicts[5:]) == {"flood"}


def test_a_missed_event_is_flagged_but_still_processed():
    main.check_request_integrity(body(webhook(SequenceId=100)), "10.0.0.5")
    _, verdict, _ = main.check_request_integrity(body(webhook(SequenceId=104)), "10.0.0.5")

    assert verdict is None     # a missed event cannot be fetched again: process it
    assert any(e["verdict"] == "gap" for e in main.integrity_log)
    assert main.incident_counts["integrity_warning"] == 1


def test_a_clock_rewind_does_not_stop_the_park():
    """Reloading a level rewinds the simulator clock; that must not reject traffic."""
    main.check_request_integrity(body(webhook(ServerDateTime="2026-09-20 10:00:00")), "10.0.0.6")
    _, verdict, _ = main.check_request_integrity(
        body(webhook(ServerDateTime="2026-09-20 08:00:00")), "10.0.0.6")

    assert verdict is None
    assert any(e["verdict"] == "clock_jump" for e in main.integrity_log)


# ---- sensor faults and maintenance mode --------------------------------------

def test_two_cars_in_one_spot_without_a_carout_is_a_sensor_fault():
    assert main.note_sensor_reading("S1", "CarIn", "CAR A", believed_free=True) is None
    fault = main.note_sensor_reading("S1", "CarIn", "CAR B", believed_free=False)
    assert "second CarIn without a CarOut" in fault


def test_a_flapping_sensor_is_detected():
    faults = [main.note_sensor_reading("S2", d, "CAR F", believed_free=False)
              for d in ["CarIn", "CarOut"] * 4]
    assert any(f and "flapping" in f for f in faults)


def test_a_repeatedly_faulty_sensor_takes_its_spot_out_of_service(monkeypatch):
    calls = []

    async def fake_call(endpoint, method="GET", **_):
        calls.append(endpoint)
        return {"status": "success"}

    async def no_audit(*_, **__):
        return None

    monkeypatch.setattr(main, "call_simulator_api", fake_call)
    monkeypatch.setattr(main, "audit_system", no_audit)
    main.parking_spots["S3"] = True

    async def run():
        # One fault is only noted; the second withdraws the spot.
        await main.handle_sensor_fault("S3", "occupancy without a vehicle")
        assert "S3" not in main.maintenance_mode
        main._sensor("S3")["faults"] = 2
        await main.handle_sensor_fault("S3", "occupancy without a vehicle")

    asyncio.run(run())

    assert "S3" in main.maintenance_mode
    assert main.maintenance_mode["S3"]["auto"] is True
    assert main.parking_spots["S3"] is False          # no longer allocatable
    assert main.spot_is_serviceable("S3") is False
    assert "parking-spots/S3/repair" in calls          # a repair was requested


def test_maintenance_mode_can_be_switched_by_an_operator():
    main.parking_spots["S4"] = True

    asyncio.run(main.set_maintenance_mode("S4", True, reason="kerb damage", actor="op1"))
    assert main.spot_is_serviceable("S4") is False
    assert main.maintenance_mode["S4"]["by"] == "op1"

    asyncio.run(main.set_maintenance_mode("S4", False, reason="fixed", actor="op1"))
    assert "S4" not in main.maintenance_mode
    assert main.spot_is_serviceable("S4") is True
    assert main.parking_spots["S4"] is True           # back in the pool


# ---- double parking and locating --------------------------------------------

def test_a_car_holding_two_spots_raises_an_early_warning():
    main.parking_spots.update({"S1": True, "S2": True})

    async def run():
        await main.note_vehicle_parked("TWO 1", "S1")
        assert "TWO 1" not in main.double_parking     # one spot is fine
        await main.note_vehicle_parked("TWO 1", "S2")

    asyncio.run(run())

    warning = main.double_parking["TWO 1"]
    assert warning["spots"] == ["S1", "S2"]
    assert main.incident_counts["double_parking"] == 1
    assert main.locate_vehicle("TWO 1")["double_parked"] is True

    # Leaving one of them clears the warning again.
    main.note_vehicle_left_spot("TWO 1", "S1")
    assert "TWO 1" not in main.double_parking


def test_one_spot_reporting_several_cars_is_double_parking():
    main.note_double_parked_spot("S7", 2)
    assert main.double_parking["spot:S7"]["cars"] == 2
    main.note_double_parked_spot("S7", 2)                 # not raised twice
    assert main.incident_counts["double_parking"] == 1


def test_a_car_that_ignored_its_assignment_is_found_where_it_actually_is():
    main.parking_spots.update({"S1": True, "S9": False})
    main.active_cars["LOST 1"] = {"assigned_spot": "S9", "car_type": "Normal",
                                  "parked": False, "charged": False}

    asyncio.run(main.note_vehicle_parked("LOST 1", "S1"))   # parked in the wrong spot

    located = main.locate_vehicle("LOST 1")
    assert located["found"] is True
    assert located["location"] == "S1"              # where it IS
    assert located["assigned_spot"] == "S9"         # where it was SENT
    assert located["in_assigned_spot"] is False
    assert main.incident_counts["vehicle_misparked"] == 1
    assert main.parking_spots["S9"] is True         # the abandoned reservation is returned


def test_locating_an_unknown_plate_says_so():
    assert main.locate_vehicle("GHOST 1")["found"] is False


# ---- payments ----------------------------------------------------------------

def test_a_failed_charge_is_retried_then_gives_up(monkeypatch):
    attempts = []

    async def always_fail(plate, parking, charging):
        attempts.append(plate)
        raise RuntimeError("simulator refused")

    monkeypatch.setattr(main, "api_charge_car", always_fail)
    monkeypatch.setattr(main, "PAYMENT_RETRY_BACKOFF", 0)

    assert asyncio.run(main.charge_with_verification("PAY 1", 4.0, 0.0)) is False
    assert len(attempts) == main.MAX_PAYMENT_ATTEMPTS
    assert main.payment_is_settled("PAY 1") is False
    assert main.incident_counts["payment_retry"] == main.MAX_PAYMENT_ATTEMPTS - 1


def test_a_settled_visit_is_never_charged_twice(monkeypatch):
    charges = []

    async def ok(plate, parking, charging):
        charges.append((plate, parking, charging))

    monkeypatch.setattr(main, "api_charge_car", ok)
    monkeypatch.setattr(main, "store_charge_async", lambda *a, **k: asyncio.sleep(0))

    async def run():
        assert await main.charge_with_verification("PAY 2", 3.0, 0.0) is True
        assert await main.charge_with_verification("PAY 2", 3.0, 0.0) is True

    asyncio.run(run())
    assert charges == [("PAY 2", 3.0, 0.0)]     # the simulator penalises a second charge


def test_a_payment_for_the_wrong_amount_is_rejected_and_asked_for_again(monkeypatch):
    charges = []

    async def ok(plate, parking, charging):
        charges.append((plate, parking, charging))

    monkeypatch.setattr(main, "api_charge_car", ok)
    monkeypatch.setattr(main, "store_charge_async", lambda *a, **k: asyncio.sleep(0))

    async def run():
        await main.charge_with_verification("PAY 3", 5.0, 0.0)     # we charged 5.00
        await main.review_payment_event({"CarPlateNumber": "PAY 3", "Amount": "1.00"})

    asyncio.run(run())

    state = main.payment_state["PAY 3"]
    assert state["suspicions"] == ["paid 1.00 but we charged 5.00"]
    assert main.incident_counts["payment_suspicious"] == 1
    assert main.incident_counts["payment_retry"] == 1
    assert len(charges) == 2          # payment was requested a second time


def test_a_payment_we_never_charged_is_suspicious():
    asyncio.run(main.review_payment_event({"CarPlateNumber": "PAY 4", "Amount": "20.00"}))
    assert main.incident_counts["payment_suspicious"] == 1
    assert "PAY 4" not in main.payment_state


def test_a_matching_payment_is_accepted(monkeypatch):
    async def ok(plate, parking, charging):
        return None

    monkeypatch.setattr(main, "api_charge_car", ok)
    monkeypatch.setattr(main, "store_charge_async", lambda *a, **k: asyncio.sleep(0))

    async def run():
        await main.charge_with_verification("PAY 5", 2.0, 4.0)     # EV: 6.00 total
        await main.review_payment_event({"CarPlateNumber": "PAY 5", "Amount": "6.00"})

    asyncio.run(run())
    assert main.payment_state["PAY 5"]["suspicions"] == []
    assert main.payment_is_settled("PAY 5") is True


# ---- gate failure tracking ---------------------------------------------------

def test_a_gate_that_keeps_refusing_is_taken_out_of_the_rotation():
    for _ in range(main.GATE_FAILURE_LIMIT):
        main.note_gate_failure("gate1", "open failed")

    assert main.gate_presumed_failed("gate1") is True
    assert asyncio.run(main.is_component_operable("gate1")) is False

    main.note_gate_success("gate1")
    assert main.gate_presumed_failed("gate1") is False
    assert asyncio.run(main.is_component_operable("gate1")) is True


# ---- ordered dispatch --------------------------------------------------------

def test_events_for_one_car_are_processed_in_arrival_order():
    """A burst of events for the same plate must not be reordered."""
    seen = []

    async def run():
        async def step(n):
            await asyncio.sleep(0.01 if n == 0 else 0)   # the first one is the slowest
            seen.append(n)

        for n in range(6):
            main.dispatch("SAME 1", lambda n=n: step(n), f"event {n}")
        while main.dispatch_depth():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert seen == [0, 1, 2, 3, 4, 5]


def test_different_cars_are_processed_concurrently():
    running = []
    overlapped = []

    async def run():
        async def step(plate):
            running.append(plate)
            await asyncio.sleep(0.02)
            overlapped.append(len(running))
            running.remove(plate)

        for plate in ("A", "B", "C"):
            main.dispatch(plate, lambda p=plate: step(p), "event")
        await asyncio.sleep(0.1)

    asyncio.run(run())
    assert max(overlapped) == 3      # all three ran at the same time
