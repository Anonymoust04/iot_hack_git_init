"""Task C searches and summarizes the existing events table."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.event_log import router
from app.models.event import Event
from app.services.event_log import daily_summary, search_events


DAY = date(2026, 9, 20)
START = datetime(2026, 9, 20)


@pytest.fixture
def logs_client(client):
    """Mount only our new router; db_hook.py belongs to another agent."""
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client


def add_event(db, kind: str, when: datetime, *, plate=None, raw=None):
    event = Event(event_type=kind, event_time=when, car_plate=plate, raw_data=raw)
    db.add(event)
    db.commit()
    return event.id


def test_search_order_filters_and_pagination(db):
    first = add_event(db, "CAR_ARRIVED", START + timedelta(hours=1), plate="CAR-1")
    middle = add_event(db, "CAR_PARKED", START + timedelta(hours=2), plate="CAR-1")
    latest = add_event(db, "CAR_ARRIVED", START + timedelta(hours=3), plate="CAR-2")
    add_event(db, "WEBHOOK", START + timedelta(hours=4), plate="CAR-1")

    assert [row.id for row in search_events(db)] == [latest, middle, first]
    assert [row.id for row in search_events(db, types=["CAR_ARRIVED"])] == [latest, first]
    assert [row.id for row in search_events(db, plate="CAR-1", limit=1, offset=1)] == [first]
    assert [row.id for row in search_events(
        db, since=START + timedelta(hours=1, minutes=30),
        until=START + timedelta(hours=3),
    )] == [latest, middle]
    assert [row.id for row in search_events(db, types=[])] == []


def test_raw_webhooks_are_opt_in_and_route_filters_work(db, logs_client, admin_headers):
    logged = add_event(db, "PENALTY", START, plate="CAR-1", raw={"FineAmount": "2"})
    webhook = add_event(db, "WEBHOOK", START + timedelta(minutes=1), plate="CAR-1",
                        raw={"EventClass": "penalty"})

    response = logs_client.get("/api/logs/events", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [logged]
    assert response.json()[0]["raw_data"] == {"FineAmount": "2"}
    response = logs_client.get("/api/logs/events", headers=admin_headers, params={
        "types": "WEBHOOK", "plate": "CAR-1", "exclude_raw_webhooks": "false",
    })
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [webhook]
    assert logs_client.get("/api/logs/events", headers=admin_headers,
                           params={"types": "WEBHOOK"}).json() == []


def test_daily_summary_counts_boundary_and_penalty_total(db, logs_client, admin_headers):
    yesterday = START - timedelta(minutes=1)
    tomorrow = START + timedelta(days=1)
    add_event(db, "CAR_ARRIVED", yesterday)
    add_event(db, "CAR_ARRIVED", START)
    add_event(db, "CAR_ARRIVED", START + timedelta(hours=8))
    add_event(db, "CAR_ARRIVED", START + timedelta(hours=8, minutes=30))
    add_event(db, "CAR_PARKED", START + timedelta(hours=8, minutes=31))
    add_event(db, "CAR_DEPARTED", START + timedelta(hours=23, minutes=59))
    add_event(db, "PENALTY", START + timedelta(hours=9), raw={"FineAmount": "12.50"})
    add_event(db, "PENALTY", START + timedelta(hours=10), raw={"FineAmount": "bad"})
    add_event(db, "COMPONENT_BROKEN", START + timedelta(hours=11))
    add_event(db, "COMPONENT_FIXED", START + timedelta(hours=12))
    add_event(db, "CO_ALERT", START + timedelta(hours=13))
    add_event(db, "WEBHOOK", START + timedelta(hours=13), raw={"EventClass": "penalty"})
    add_event(db, "CAR_ARRIVED", tomorrow)
    add_event(db, "PENALTY", tomorrow, raw={"FineAmount": "100"})

    result = daily_summary(db, DAY)
    assert result == {
        "date": DAY, "cars_arrived": 3, "cars_parked": 1, "cars_departed": 1,
        "penalties": 2, "penalty_total": Decimal("12.50"),
        "components_broken": 1, "components_fixed": 1, "co_alerts": 1,
        "busiest_hour": 8,
        "events_by_type": {
            "CAR_ARRIVED": 3, "CAR_DEPARTED": 1, "CAR_PARKED": 1,
            "CO_ALERT": 1, "COMPONENT_BROKEN": 1, "COMPONENT_FIXED": 1,
            "PENALTY": 2, "WEBHOOK": 1,
        },
    }
    response = logs_client.get("/api/logs/daily-summary", headers=admin_headers,
                               params={"day": DAY.isoformat()})
    assert response.status_code == 200, response.text
    assert response.json()["cars_arrived"] == 3
    assert Decimal(str(response.json()["penalty_total"])) == Decimal("12.50")


def test_empty_day_and_busiest_hour_tie(db):
    empty = daily_summary(db, DAY)
    assert empty == {
        "date": DAY, "cars_arrived": 0, "cars_parked": 0, "cars_departed": 0,
        "penalties": 0, "penalty_total": Decimal("0"), "components_broken": 0,
        "components_fixed": 0, "co_alerts": 0, "busiest_hour": None,
        "events_by_type": {},
    }
    add_event(db, "CAR_ARRIVED", START + timedelta(hours=7))
    add_event(db, "CAR_ARRIVED", START + timedelta(hours=6))
    assert daily_summary(db, DAY)["busiest_hour"] == 6


def test_authenticated_routes_and_utc_offset_filter(db, logs_client, admin_headers):
    assert logs_client.get("/api/logs/events").status_code == 401
    assert logs_client.get("/api/logs/daily-summary").status_code == 401
    event_id = add_event(db, "CAR_ARRIVED", START)
    response = logs_client.get("/api/logs/events", headers=admin_headers, params={
        "since": datetime(2026, 9, 20, 8, tzinfo=timezone(timedelta(hours=8))).isoformat(),
        "until": START.isoformat(),
    })
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [event_id]
