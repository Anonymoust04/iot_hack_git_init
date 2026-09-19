"""Penalty list and summary read existing PENALTY events, without simulator calls."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.penalties import router
from app.core.security import create_access_token, hash_password
from app.models import Event, Role, User
from app.services.penalties import list_penalties, penalty_summary


START = datetime(2026, 9, 20, 12, 0, 0)


@pytest.fixture
def penalty_client(client):
    """Mount the new route without editing the shared db_hook.py."""
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client


def add_event(db, *, minutes=0, kind="PENALTY", payload=None):
    event = Event(
        event_type=kind,
        event_time=START + timedelta(minutes=minutes),
        raw_data=payload,
    )
    db.add(event)
    db.commit()
    return event.id


def test_list_is_newest_first_and_parses_payload(db):
    older = add_event(db, minutes=1, payload={
        "Reason": "wrong charge", "FineAmount": "2.50", "Type": "Car",
        "ComponentName": "EXIT_EXIT", "CarPlateNumber": "CAR 1", "EventId": "evt-old",
    })
    newer = add_event(db, minutes=3, payload={
        "Reason": "occupied spot", "FineAmount": "50.00", "Type": "ParkingSpot",
        "ComponentName": "S3", "CarPlateNumber": "CAR 2", "EventId": "evt-new",
    })
    add_event(db, minutes=4, kind="WEBHOOK", payload={"EventClass": "penalty"})

    penalties = list_penalties(db)
    assert [row["id"] for row in penalties] == [newer, older]
    assert penalties[0] == {
        "id": newer, "received_at": START + timedelta(minutes=3),
        "reason": "occupied spot", "fine_amount": Decimal("50.00"),
        "type": "ParkingSpot", "component": "S3", "car_plate": "CAR 2", "event_id": "evt-new",
    }


def test_filters_run_before_pagination(db, penalty_client, admin_headers):
    oldest = add_event(db, minutes=0, payload={"Type": "Car", "FineAmount": "1"})
    middle = add_event(db, minutes=1, payload={"Type": "ParkingSpot", "FineAmount": "2"})
    add_event(db, minutes=2, payload={"Type": "Car", "FineAmount": "3"})
    newest = add_event(db, minutes=3, payload={"Type": "ParkingSpot", "FineAmount": "4"})

    response = penalty_client.get("/api/penalties", headers=admin_headers, params={
        "since": START.isoformat(), "until": (START + timedelta(minutes=3)).isoformat(),
        "type": "ParkingSpot", "limit": 1, "offset": 1,
    })
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [middle]
    assert [row["id"] for row in list_penalties(db, type="ParkingSpot")] == [newest, middle]
    assert [row["id"] for row in list_penalties(db, until=START + timedelta(minutes=1))] == [middle, oldest]


def test_missing_and_invalid_fields_are_retained(db):
    add_event(db, payload={})
    add_event(db, minutes=1, payload={"FineAmount": "not-a-number"})
    add_event(db, minutes=2, payload={"FineAmount": "NaN"})
    add_event(db, minutes=3, payload=["not", "an", "object"])

    rows = list_penalties(db)
    assert [row["fine_amount"] for row in rows] == [None, Decimal("0"), Decimal("0"), None]
    assert all(row["reason"] is None and row["event_id"] is None for row in rows)


def test_summary_counts_totals_types_and_reasons(db, penalty_client, admin_headers):
    add_event(db, minutes=0, payload={"Type": "Car", "Reason": "wrong charge", "FineAmount": "1.25"})
    add_event(db, minutes=1, payload={"Type": "ParkingSpot", "Reason": "occupied", "FineAmount": "50"})
    add_event(db, minutes=2, payload={"Type": "ParkingSpot", "Reason": "occupied", "FineAmount": "bad"})
    add_event(db, minutes=3, payload={"Type": "Car", "Reason": "wrong charge"})
    add_event(db, minutes=4, kind="WEBHOOK", payload={"Type": "Car", "FineAmount": "999"})

    summary = penalty_summary(db)
    assert summary["count"] == 4
    assert summary["total_fine"] == Decimal("51.25")
    assert summary["by_type"] == [
        {"type": "Car", "count": 2, "total_fine": Decimal("1.25")},
        {"type": "ParkingSpot", "count": 2, "total_fine": Decimal("50")},
    ]
    assert summary["top_reasons"] == [
        {"reason": "occupied", "count": 2}, {"reason": "wrong charge", "count": 2},
    ]

    response = penalty_client.get("/api/penalties/summary", headers=admin_headers, params={
        "since": (START + timedelta(minutes=1)).isoformat(),
        "until": (START + timedelta(minutes=2)).isoformat(),
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 2
    assert Decimal(str(body["total_fine"])) == Decimal("50")
    assert body["by_type"][0]["type"] == "ParkingSpot"


def test_empty_summary(db):
    assert penalty_summary(db) == {"count": 0, "total_fine": Decimal("0"), "by_type": [], "top_reasons": []}


def test_routes_require_login_and_allow_operator(db, penalty_client):
    assert penalty_client.get("/api/penalties").status_code == 401
    assert penalty_client.get("/api/penalties/summary").status_code == 401

    db.add(User(username="penalty-operator", password_hash=hash_password("test-password"), role=Role.OPERATOR))
    db.commit()
    token = create_access_token("penalty-operator", Role.OPERATOR)
    headers = {"Authorization": f"Bearer {token}"}
    assert penalty_client.get("/api/penalties", headers=headers).status_code == 200
    assert penalty_client.get("/api/penalties/summary", headers=headers).status_code == 200
