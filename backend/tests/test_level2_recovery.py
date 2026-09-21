"""Focused Level 2 safety and recovery behavior."""

from datetime import datetime
from sqlalchemy import select

from app.models import Event, ParkingSession, SessionStatus
from app.services.parking import recover_manual_parking
from app.services.webhook_handlers import receive


def test_unsigned_webhook_is_logged_and_not_processed(db, monkeypatch):
    """Strict policy only: WEBHOOK_REQUIRE_SIGNATURE is off by default because the
    shipped simulator sends no signature at all (docs/LEVEL1.md)."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "webhook_require_signature", True)
    payload = b'{"EventClass":"gate_action","Name":"gateU","Action":"Open"}'

    parsed, reason = receive(db, payload)

    assert parsed["Name"] == "gateU"
    assert reason == "unsigned"
    assert db.scalar(select(Event.event_type).order_by(Event.id.desc())) == "WEBHOOK_UNSIGNED"


def test_manual_exit_recovery_creates_parked_visit(db):
    recovered = recover_manual_parking(
        db,
        "MANUAL-1",
        25,
        at=datetime(2026, 9, 20, 12, 0),
        raw_data={"EventClass": "car_spot_action", "SpotType": "ExitSpot"},
    )

    session = db.scalar(select(ParkingSession).where(ParkingSession.car_plate == "MANUAL-1"))
    assert recovered is True
    assert session is not None
    assert session.status == SessionStatus.PARKED
    assert session.parked_time == datetime(2026, 9, 20, 11, 35)
    assert db.scalar(select(Event.event_type).where(Event.car_plate == "MANUAL-1")) == "MANUAL_PARK_RECOVERED"


def test_manual_recovery_is_idempotent(db):
    assert recover_manual_parking(db, "MANUAL-2", 10) is True
    assert recover_manual_parking(db, "MANUAL-2", 10) is False
    assert len(db.scalars(select(ParkingSession).where(ParkingSession.car_plate == "MANUAL-2")).all()) == 1
