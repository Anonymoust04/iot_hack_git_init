"""Database-backed financial report and simulator charge coverage."""

import asyncio
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select

from app.models import ParkingSession, PaymentStatus, SessionStatus
from app.services.event_log import financial_summary
from app.models.event import Event
from app.services.parking import store_charge


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
    assert report["net_revenue"] == Decimal("13.00")
    assert report["parking_transactions"] == 1
    assert report["charging_transactions"] == 1
    assert report["penalty_transactions"] == 1


def test_financial_summary_uses_charge_date_and_ignores_uncharged_exits(db):
    db.add_all([
        ParkingSession(
            car_plate="EV-NIGHT", entry_time=datetime(2026, 9, 20, 23, 50),
            exit_time=datetime(2026, 9, 21, 0, 1), parking_cost=Decimal("11.00"),
            charging_cost=Decimal("11.00"), payment_status=PaymentStatus.PAID,
            status=SessionStatus.COMPLETED,
        ),
        ParkingSession(
            car_plate="NO-CHARGE", entry_time=datetime(2026, 9, 21, 1),
            exit_time=datetime(2026, 9, 21, 2), payment_status=PaymentStatus.PAID,
            status=SessionStatus.COMPLETED,
        ),
    ])
    db.commit()

    assert financial_summary(db, date(2026, 9, 20))["total_revenue"] == Decimal("0.00")
    report = financial_summary(db, date(2026, 9, 21))
    assert report["total_revenue"] == Decimal("22.00")
    assert report["net_revenue"] == Decimal("22.00")
    assert report["parking_transactions"] == 1
    assert report["charging_transactions"] == 1


def test_store_charge_attaches_to_completed_visit_after_exit_webhook(db):
    charge_at = datetime(2026, 9, 21, 0, 1)
    db.add(ParkingSession(
        car_plate="RACE-EV", entry_time=datetime(2026, 9, 20, 23, 50),
        exit_time=datetime(2026, 9, 21, 0, 2), payment_status=PaymentStatus.PAID,
        status=SessionStatus.COMPLETED,
    ))
    db.commit()

    store_charge(db, "RACE-EV", 11, 11, minutes=11, at=charge_at)

    sessions = db.scalars(select(ParkingSession).where(ParkingSession.car_plate == "RACE-EV")).all()
    assert len(sessions) == 1
    assert sessions[0].parking_cost == Decimal("11.00")
    assert sessions[0].exit_time == charge_at
    assert financial_summary(db, date(2026, 9, 21))["total_revenue"] == Decimal("22.00")


def test_simulator_exit_bills_actual_minutes_and_ev_double(monkeypatch):
    import main

    monkeypatch.setattr(main, "active_cars", {
        "EV-1": {
            "entry_time": "2026-09-20 23:50:00", "planned_duration": 100,
            "car_type": "Electric", "parked": True, "assigned_spot": "S5",
        },
    })
    monkeypatch.setattr(main, "charged_cars", set())
    monkeypatch.setattr(main, "parking_spots", {"S5": False})
    monkeypatch.setattr(main, "spot_lock", asyncio.Lock())
    monkeypatch.setattr(main, "webhook_events", [])
    monkeypatch.setattr(main, "gate_queues", {gate: asyncio.Queue() for gate in main.ALL_GATES})
    charges = []
    stored = []

    async def charge(plate, parking, charging):
        charges.append((plate, parking, charging))

    async def store(*args):
        stored.append(args)

    monkeypatch.setattr(main, "api_charge_car", charge)
    monkeypatch.setattr(main, "store_charge_async", store)

    async def run():
        await main.process_car_exit({
            "CarPlateNumber": "EV-1", "CarType": "Electric", "SpotName": "EXIT1",
            "ServerDateTime": "2026-09-21 00:01:00",
        })
        await asyncio.sleep(0)

    asyncio.run(run())
    assert charges == [("EV-1", 11.0, 11.0)]
    assert stored == [("EV-1", 11.0, 11.0, 11, "Electric", datetime(2026, 9, 21, 0, 1))]
