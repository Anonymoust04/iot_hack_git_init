"""Webhook route: signature, dedup, sequence checks, and the car / payment flow end to end."""

import uuid

import pytest
from sqlalchemy import select

from app.api.routes import webhook as webhook_route
from app.models import Event, Gate, ParkingSession, PaymentStatus, SessionStatus
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


# ---- route (real MySQL) -----------------------------------------------------

class FakeSimulator:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, *args))


@pytest.fixture
def sim(monkeypatch):
    fake = FakeSimulator()
    monkeypatch.setattr(webhook_route, "get_simulator", lambda: fake)
    return fake


_seq = iter(range(10_000, 10**9))


def send(client, event_class, **fields):
    payload = {"EventClass": event_class, "EventId": str(uuid.uuid4()), "SequenceId": next(_seq),
               "ServerDateTime": "2026-09-12 15:40:00", **fields}
    payload["Signature"] = compute_signature({k: str(v) for k, v in payload.items()})
    return client.post("/webhook", json=payload), payload


def car_event(client, plate, spot_name, spot_type, direction, car_type="Normal", time="2026-09-12 15:40:00"):
    return send(client, "car_spot_action", CarPlateNumber=plate, SpotName=spot_name, SpotType=spot_type,
                CarType=car_type, Direction=direction, PlannedParkingDurationInMinutes="0",
                ServerDateTime=time)[0]


def event_types(db, plate):
    db.expire_all()
    return db.scalars(select(Event.event_type).where(Event.car_plate == plate).order_by(Event.id)).all()


def test_bad_signature_is_stored_but_not_processed(db, client, sim):
    r = client.post("/webhook", json={"EventClass": "payment_made", "CarPlateNumber": "FAKE 1",
                                      "Amount": "1.00", "EventId": "x", "SequenceId": 1, "Signature": "0" * 32})
    assert r.json()["handled"] is False
    assert event_types(db, "FAKE 1") == ["WEBHOOK_BAD_SIGNATURE"]


def test_duplicate_event_id_is_processed_once(db, client, sim):
    r, payload = send(client, "gate_action", Name="gate0", Action="Open")
    assert r.json() == {"ok": True, "handled": True}
    assert client.post("/webhook", json=payload).json()["duplicate"] is True
    assert len(db.scalars(select(Event).where(Event.event_id == payload["EventId"])).all()) == 1


def test_sequence_gap_is_logged(db, client, sim):
    send(client, "test_webhook")
    next(_seq)  # skip one number
    send(client, "test_webhook")
    db.expire_all()
    assert db.scalar(select(Event.event_type).where(Event.event_type == "SEQUENCE_GAP")) == "SEQUENCE_GAP"


def test_float_values_keep_their_original_text(db, client, sim):
    # 63.564693 must be signed as written, not as Python re-formats the float
    payload = {"EventClass": "carbon_monoxide_event", "ZoneName": "ZONE1", "CarbonMonoxideLevel": 63.564693,
               "DangerLevel": "Mid", "EventId": str(uuid.uuid4()), "SequenceId": next(_seq),
               "ServerDateTime": "2026-09-12 15:35:21"}
    payload["Signature"] = compute_signature({k: str(v) for k, v in payload.items()})
    assert client.post("/webhook", json=payload).json()["handled"] is True


def test_gate_action_updates_gate(db, client, sim):
    send(client, "gate_action", Name="gate0", Action="Opening")
    send(client, "component_broken", Type="BarrierGate", Name="gate0", FineAmount="10.00")
    db.expire_all()
    gate = db.scalars(select(Gate).where(Gate.name == "gate0")).one()
    assert (gate.state.value, gate.broken) == ("Opening", True)


def test_full_car_flow_with_valid_and_fake_payment(db, client, sim):
    upsert_parking_spots(db, [spot("S153")])
    db.commit()
    plate = "WCT 759"

    car_event(client, plate, "ENTRY1", "EntrySpot", "CarIn", time="2026-09-12 15:40:17")
    assert ("car_goto", plate, "S153") in sim.calls
    car_event(client, plate, "ENTRY1", "EntrySpot", "CarOut")
    car_event(client, plate, "S153", "Park", "CarIn")
    assert get_spot(db, "S153").current_car == plate
    car_event(client, plate, "S153", "Park", "CarOut")
    car_event(client, plate, "EXIT", "ExitSpot", "CarIn", time="2026-09-12 15:42:41")
    # billed on the simulator clock: 15:40:17 -> 15:42:41 = 2m24s -> 3 minutes (rounded up)
    assert ("charge_car", plate, 3.0, 0.0) in sim.calls

    send(client, "payment_made", CarPlateNumber=plate, Amount="1.00", Reason="Car Payment")  # wrong amount
    send(client, "payment_made", CarPlateNumber=plate, Amount="3.00", Reason="Car Payment")
    send(client, "payment_made", CarPlateNumber=plate, Amount="3.00", Reason="Car Payment")  # second payment

    car_event(client, plate, "EXIT", "ExitSpot", "CarOut")
    db.expire_all()
    session = db.scalars(select(ParkingSession).where(ParkingSession.car_plate == plate)).one()
    assert (session.status, session.payment_status) == (SessionStatus.COMPLETED, PaymentStatus.PAID)
    handled = [t for t in event_types(db, plate) if t != "WEBHOOK"]
    assert handled == ["SPOT_ASSIGNED", "CAR_PARKED", "CAR_EXITING", "CAR_CHARGED",
                       "PAYMENT_REJECTED", "PAYMENT_ACCEPTED", "PAYMENT_REJECTED", "CAR_DEPARTED"]
