"""Reports data: charges stored from main.py, the daily and financial summaries built from them."""

from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api.routes import event_log as event_log_routes
from app.core.security import create_access_token, hash_password
from app.models import Event, ParkingSession, PaymentStatus, Role, User
from app.models.payment_record import PaymentRecord
from app.models.user_permission import UserPermission
from app.services.event_log import daily_summary, financial_summary, peak_occupancy
from app.services.parking import store_charge

DAY = date(2026, 3, 17)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(DAY.year, DAY.month, DAY.day, hour, minute)


@pytest.fixture
def reports_db(db):
    db.execute(delete(PaymentRecord))
    db.execute(delete(ParkingSession))
    db.execute(delete(Event))
    db.commit()
    yield db
    db.execute(delete(PaymentRecord))
    db.execute(delete(ParkingSession))
    db.execute(delete(Event))
    db.commit()


def visit(db, plate, parked, left, parking_cost=None, charging_cost=None, paid=True):
    db.add(ParkingSession(
        car_plate=plate, entry_time=parked - timedelta(minutes=2), parked_time=parked, exit_time=left,
        parking_cost=parking_cost, charging_cost=charging_cost,
        payment_status=PaymentStatus.PAID if paid else PaymentStatus.PENDING,
    ))
    db.commit()


# ---- charges are stored (the gap that left the financial report empty) ----------

def test_store_charge_saves_amounts_and_marks_paid(reports_db):
    store_charge(reports_db, "PAY 001", 7, 14, minutes=7, at=at(10))
    [session] = reports_db.scalars(select(ParkingSession)).all()
    assert (float(session.parking_cost), float(session.charging_cost)) == (7.0, 14.0)
    assert session.payment_status == PaymentStatus.PAID and session.exit_time == at(10)
    [payment] = reports_db.scalars(select(PaymentRecord)).all()
    assert (payment.parking_fee, payment.ev_fee, payment.total_amount) == (7, 14, 21)
    assert reports_db.scalar(select(Event.event_type).where(Event.car_plate == "PAY 001")) == "CAR_CHARGED"


def test_a_repeated_exit_event_does_not_double_count(reports_db):
    store_charge(reports_db, "PAY 002", 5, 0, minutes=5, at=at(11))
    store_charge(reports_db, "PAY 002", 5, 0, minutes=5, at=at(11))   # same car charged twice
    money = financial_summary(reports_db, DAY)
    assert float(money["parking_revenue"]) == 5.0 and money["parking_transactions"] == 1
    assert len(reports_db.scalars(select(PaymentRecord)).all()) == 1


# ---- financial summary ---------------------------------------------------------

def test_financial_summary_totals_and_breakdown(reports_db):
    visit(reports_db, "CAR A", at(9), at(9, 30), parking_cost=30, charging_cost=0)
    visit(reports_db, "EV  B", at(10), at(11), parking_cost=60, charging_cost=120)     # electric
    visit(reports_db, "CAR C", at(12), at(12, 10), parking_cost=10, charging_cost=0, paid=False)  # unpaid
    for plate, parking, ev, paid_at in (("CAR A", 30, 0, at(9, 30)), ("EV  B", 60, 120, at(11))):
        session = reports_db.scalar(select(ParkingSession).where(ParkingSession.car_plate == plate))
        reports_db.add(PaymentRecord(
            parking_session_id=session.id, car_plate=plate,
            parking_fee=parking, ev_fee=ev, total_amount=parking + ev, paid_at=paid_at,
        ))
    reports_db.add(Event(event_type="PENALTY", event_time=at(13),
                         raw_data={"EventClass": "penalty", "Reason": "Gate broken", "FineAmount": "10.00",
                                   "Type": "BarrierGate", "ComponentName": "gate1"}))
    reports_db.commit()

    money = financial_summary(reports_db, DAY)
    assert float(money["parking_revenue"]) == 90.0          # paid visits only
    assert float(money["ev_charging_revenue"]) == 120.0
    assert float(money["total_revenue"]) == 220.0
    assert float(money["penalty_income"]) == 10.0
    assert float(money["penalty_cost"]) == 10.0
    assert {row["category"]: (row["transactions"], float(row["amount"])) for row in money["breakdown"]} == {
        "Parking": (2, 90.0), "EV charging": (1, 120.0), "Penalties": (1, 10.0)}


def test_financial_summary_of_a_quiet_day_is_zero_not_an_error(reports_db):
    money = financial_summary(reports_db, DAY - timedelta(days=5))
    assert float(money["total_revenue"]) == 0.0 and money["breakdown"][0]["transactions"] == 0


# ---- daily operations ----------------------------------------------------------

def test_peak_occupancy_counts_cars_parked_at_the_same_time(reports_db):
    visit(reports_db, "P1", at(8), at(12))            # 08-12
    visit(reports_db, "P2", at(9), at(10))            # 09-10  -> 2 at once
    visit(reports_db, "P3", at(9, 30), at(9, 45))     # 09:30  -> 3 at once
    visit(reports_db, "P4", at(13), None)             # still parked at the end of the day
    assert peak_occupancy(reports_db, DAY) == 3
    assert peak_occupancy(reports_db, DAY - timedelta(days=1)) == 0


def test_daily_summary_reports_the_day(reports_db):
    for event_type, when in (("CAR_ARRIVED", at(8)), ("CAR_ARRIVED", at(8, 30)), ("CAR_PARKED", at(8, 40)),
                             ("CAR_DEPARTED", at(9)), ("CO_ALERT", at(10)), ("COMPONENT_BROKEN", at(11)),
                             ("COMPONENT_FIXED", at(11, 30))):
        reports_db.add(Event(event_type=event_type, event_time=when))
    visit(reports_db, "P1", at(8, 40), at(9))
    reports_db.commit()

    summary = daily_summary(reports_db, DAY)
    assert (summary["cars_arrived"], summary["cars_departed"]) == (2, 1)
    assert (summary["co_alerts"], summary["components_broken"], summary["components_fixed"]) == (1, 1, 1)
    assert summary["busiest_hour"] == 8 and summary["peak_occupancy"] == 1


# ---- who may read the financial report ------------------------------------------

def test_financial_report_needs_the_finance_permission(reports_db):
    store_charge(reports_db, "REPORT-EV", 3, 3, minutes=3, at=at(12))
    for name, role in (("fin_admin", Role.ADMIN), ("fin_op", Role.OPERATOR)):
        if reports_db.scalar(select(User).where(User.username == name)) is None:
            reports_db.add(User(username=name, password_hash=hash_password("pw"), role=role))
    reports_db.commit()
    operator = reports_db.scalar(select(User).where(User.username == "fin_op"))
    reports_db.execute(delete(UserPermission).where(UserPermission.user_id == operator.id))
    reports_db.add(UserPermission(user_id=operator.id, permission="FINANCIAL_REPORTS", enabled=False))
    reports_db.commit()

    app = FastAPI()
    app.include_router(event_log_routes.router)
    client = TestClient(app)
    url = f"/api/logs/financial-summary?day={DAY}"
    payments_url = f"/api/logs/payments?day={DAY}"
    assert client.get(url).status_code == 401                                                        # no token
    assert client.get(payments_url).status_code == 401
    assert client.get(url, headers={"Authorization": f"Bearer {create_access_token('fin_op', Role.OPERATOR)}"}
                      ).status_code == 403                                                           # not allowed
    assert client.get(payments_url, headers={"Authorization": f"Bearer {create_access_token('fin_op', Role.OPERATOR)}"}
                      ).status_code == 403
    assert client.get(url, headers={"Authorization": f"Bearer {create_access_token('fin_admin', Role.ADMIN)}"}
                      ).status_code == 200                                                           # admin
    response = client.get(payments_url, headers={"Authorization": f"Bearer {create_access_token('fin_admin', Role.ADMIN)}"})
    assert response.status_code == 200
    assert [(item["car_plate"], item["parking_fee"], item["ev_fee"], item["total_amount"])
            for item in response.json()] == [("REPORT-EV", "3.00", "3.00", "6.00")]
    reports_db.execute(delete(User).where(User.username.in_(["fin_admin", "fin_op"])))
    reports_db.commit()
