"""Database-backed financial report coverage."""

from datetime import date, datetime
from decimal import Decimal

from app.models import ParkingSession, PaymentStatus, SessionStatus
from app.services.event_log import financial_summary
from app.models.event import Event


def test_financial_summary_uses_paid_sessions_and_penalties(db):
    db.add_all([
        ParkingSession(
            car_plate="CAR-1", entry_time=datetime(2026, 9, 20, 8),
            exit_time=datetime(2026, 9, 20, 9), parking_cost=Decimal("12.50"),
            charging_cost=Decimal("4.00"), payment_status=PaymentStatus.PAID,
            status=SessionStatus.EXITING,
        ),
        ParkingSession(
            car_plate="CAR-2", entry_time=datetime(2026, 9, 20, 8),
            exit_time=datetime(2026, 9, 20, 9), parking_cost=Decimal("99.00"),
            charging_cost=Decimal("0.00"), payment_status=PaymentStatus.PENDING,
            status=SessionStatus.EXITING,
        ),
    ])
    db.add(Event(
        event_type="PENALTY", event_time=datetime(2026, 9, 20, 10),
        raw_data={"FineAmount": "3.50", "Reason": "Test"},
    ))
    db.commit()

    report = financial_summary(db, date(2026, 9, 20))

    assert report["parking_revenue"] == Decimal("12.50")
    assert report["ev_charging_revenue"] == Decimal("4.00")
    assert report["penalty_cost"] == Decimal("3.50")
    assert report["total_revenue"] == Decimal("16.50")
    assert report["parking_transactions"] == 1
    assert report["charging_transactions"] == 1
    assert report["penalty_transactions"] == 1