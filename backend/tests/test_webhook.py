"""POST /webhook (Tee's main.py) -> db_hook.py -> MySQL: signature, dedup, sequence checks,
and the tables the dashboard reads. Simulator calls are faked, so no real car is moved."""

import time
import uuid

import db_hook
import main
import pytest
from sqlalchemy import select

from app.models import Event, Gate, ParkingSession, PaymentStatus, SessionStatus, SpotStatus
from app.services.sync import upsert_parking_spots
from app.services.webhook_handlers import compute_signature, signature_is_valid
from tests.test_parking_flow import get_spot, spot

# ---- signature (no database) ----------------------------------------------

DOC_EXAMPLE = {
    "EventClass": "car_spot_action", "CarPlateNumber": "WAW 228", "SpotName": "ENTRY1",
    "SpotType": "EntrySpot", "Direction": "CarOut", "PlannedParkingDurationInMinutes": "0",
    "EventId": "efa2d3ac-1a6e-47d4-9099-3457270e30ee", "SequenceId": "405",
    "ServerDateTime": "2026-09-12 14:25:50", "RealDateTime": "2026-09-12 14:51:37",
}


def test_signature_matches_the_documented_example():
    assert compute_signature(DOC_EXAMPLE) == "80beadedc24aea52b9c6222aba1815d3"
    assert signature_is_valid({**DOC_EXAMPLE, "Signature": "80beadedc24aea52b9c6222aba1815d3"})
    assert not signature_is_valid({**DOC_EXAMPLE, "Amount": "999", "Signature": "80beadedc24aea52b9c6222aba1815d3"})
    assert not signature_is_valid(DOC_EXAMPLE)  # no Signature at all


# ---- helpers ----------------------------------------------------------------

@pytest.fixture(autouse=True)
def no_simulator(monkeypatch):
    """main.py's workers and db_hook's sync must not talk to a real simulator during tests."""
    async def fake_call(*args, **kwargs):
        return {}
    monkeypatch.setattr(main, "call_simulator_api", fake_call)
    monkeypatch.setattr(db_hook, "_sync_if_empty", lambda: None)


def wait_until(check, timeout=10.0):
    """Webhooks are stored by db_hook's background worker: wait for it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError("condition not reached in time")


_seq = iter(range(10_000, 10**9))


def send(client, event_class, **fields):
    payload = {"EventClass": event_class, "EventId": str(uuid.uuid4()), "SequenceId": next(_seq),
               "ServerDateTime": "2026-09-12 15:40:00", **fields}
    payload["Signature"] = compute_signature({k: str(v) for k, v in payload.items()})
    r = client.post("/webhook", json=payload)
    assert r.status_code == 200
    return payload


def car_event(client, plate, spot_name, spot_type, direction, time="2026-09-12 15:40:00"):
    return send(client, "car_spot_action", CarPlateNumber=plate, SpotName=spot_name, SpotType=spot_type,
                CarType="Normal", Direction=direction, PlannedParkingDurationInMinutes="2",
                ServerDateTime=time)


def event_types(db, plate=None, event_type=None):
    db.expire_all()
    q = select(Event.event_type).order_by(Event.id)
    if plate:
        q = q.where(Event.car_plate == plate)
    if event_type:
        q = q.where(Event.event_type == event_type)
    return db.scalars(q).all()


# ---- checks on every webhook --------------------------------------------------

def test_bad_signature_is_stored_but_not_processed(db, client):
    client.post("/webhook", json={"EventClass": "gate_action", "CarPlateNumber": "FAKE 1", "Name": "gateZ",
                                  "Action": "Open", "EventId": "x", "SequenceId": 1, "Signature": "0" * 32})
    wait_until(lambda: event_types(db, "FAKE 1") == ["WEBHOOK_BAD_SIGNATURE"])
    assert db.scalar(select(Gate).where(Gate.name == "gateZ")) is None


def test_duplicate_event_id_is_stored_once(db, client):
    payload = send(client, "gate_action", Name="gate0", Action="Open")
    client.post("/webhook", json=payload)  # same EventId again
    send(client, "test_webhook")           # stored after both copies (the worker keeps order)
    wait_until(lambda: event_types(db, event_type="WEBHOOK") == ["WEBHOOK", "WEBHOOK"])
    assert len(db.scalars(select(Event).where(Event.event_id == payload["EventId"])).all()) == 1


def test_sequence_gap_is_logged(db, client):
    send(client, "test_webhook")
    next(_seq)  # skip one number
    send(client, "test_webhook")
    wait_until(lambda: event_types(db, event_type="SEQUENCE_GAP") == ["SEQUENCE_GAP"])


def test_float_values_keep_their_original_text(db, client):
    # 63.564693 must be signed as written, not as Python re-formats the float
    payload = {"EventClass": "carbon_monoxide_event", "ZoneName": "ZONE1", "CarbonMonoxideLevel": 63.564693,
               "DangerLevel": "Mid", "EventId": str(uuid.uuid4()), "SequenceId": next(_seq),
               "ServerDateTime": "2026-09-12 15:35:21"}
    payload["Signature"] = compute_signature({k: str(v) for k, v in payload.items()})
    client.post("/webhook", json=payload)
    wait_until(lambda: event_types(db, event_type="CO_ALERT") == ["CO_ALERT"])


def test_gate_action_updates_gate(db, client):
    send(client, "gate_action", Name="gateA", Action="Opening")
    send(client, "component_broken", Type="BarrierGate", Name="gateA", FineAmount="10.00")

    def gate_updated():
        db.expire_all()
        gate = db.scalar(select(Gate).where(Gate.name == "gateA"))
        return gate is not None and (gate.state.value, gate.broken) == ("Opening", True)
    wait_until(gate_updated)


# ---- a car's visit, as the dashboard sees it -------------------------------------

def test_car_visit_updates_spots_and_history(db, client):
    upsert_parking_spots(db, [spot("S5")])
    db.commit()
    plate = "WCT 759"

    car_event(client, plate, "ENTRY1", "EntrySpot", "CarIn")  # main.py's entry worker handles this one
    car_event(client, plate, "S5", "Park", "CarIn", time="2026-09-12 15:40:21")
    wait_until(lambda: get_spot(db, "S5").current_car == plate)
    assert get_spot(db, "S5").status == SpotStatus.OCCUPIED

    car_event(client, plate, "S5", "Park", "CarOut")
    wait_until(lambda: get_spot(db, "S5").status == SpotStatus.FREE)  # free again for the next car

    send(client, "payment_made", CarPlateNumber=plate, Amount="2.00", Reason="Car Payment")
    car_event(client, plate, "EXIT_EXIT", "ExitSpot", "CarOut")
    wait_until(lambda: "CAR_DEPARTED" in event_types(db, plate))

    db.expire_all()
    session = db.scalars(select(ParkingSession).where(ParkingSession.car_plate == plate)).one()
    assert (session.status, session.payment_status) == (SessionStatus.COMPLETED, PaymentStatus.PAID)
    assert [t for t in event_types(db, plate) if t != "WEBHOOK"] == ["CAR_PARKED", "CAR_EXITING", "CAR_DEPARTED"]


def test_main_py_still_answers_from_its_own_queues(client):
    # Tee's route replies right away; the database copy happens in the background
    r = client.post("/webhook", json={"EventClass": "test_webhook"})
    assert r.json()["status"] == "queued"
