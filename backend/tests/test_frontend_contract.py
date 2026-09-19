"""The fields web-interface/src/services/api.js reads from each endpoint. If one of these fails,
the frontend breaks: change api.js together with the backend."""

import pytest
from sqlalchemy import select

from app.api.routes import control
from app.models import ParkingSession, SessionStatus
from app.services.sync import upsert_gates, upsert_parking_spots
from tests.test_parking_flow import spot

FRONTEND_ORIGIN = "http://localhost:5173"  # `npm run dev`


@pytest.fixture
def park(db):
    upsert_parking_spots(db, [spot("S1"), spot("S2"), spot("ENTRY1", purpose="EntrySpot")])
    upsert_gates(db, [{"name": "gateA", "zoneParent": "ZONE1", "state": "Open"},
                      {"name": "gateB", "zoneParent": "ZONE1", "state": "Closed", "broken": True},
                      {"name": "gateC", "zoneParent": "", "state": "Open"}])
    db.commit()
    return db


def test_login_and_me(client):
    # api.js login(): form POST, then /me with the token
    r = client.post("/api/auth/login", data={"username": "admin", "password": "admin-pw"},
                    headers={"Origin": FRONTEND_ORIGIN})
    assert r.headers["access-control-allow-origin"] == FRONTEND_ORIGIN  # CORS for the dev server
    token = r.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert {"username", "role"} <= me.keys() and me["role"] == "ADMIN"


def test_health(client):
    assert client.get("/health").status_code == 200  # getBackendStatus()


def test_cors_allows_local_dev_servers_on_any_port(client):
    for origin in ("http://127.0.0.1:5173", "http://localhost:5174"):
        r = client.get("/health", headers={"Origin": origin})
        assert r.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-origin" not in client.get("/health", headers={"Origin": "http://evil.example"}).headers


def test_dashboard_stats(park, client, admin_headers):
    d = client.get("/api/dashboard", headers=admin_headers).json()
    assert {"zones", "total_free", "cars_inside"} <= d.keys()
    assert {"total", "free", "occupied", "reserved"} <= d["zones"][0].keys()
    assert (d["zones"][0]["total"], d["total_free"], d["cars_inside"]) == (2, 2, 0)


def test_parking_spots(park, client, admin_headers):
    spots = client.get("/api/dashboard/spots", headers=admin_headers).json()
    assert all({"name", "status", "zone", "purpose"} <= s.keys() for s in spots)
    assert {s["status"] for s in spots} <= {"FREE", "RESERVED", "OCCUPIED", "BROKEN", "MAINTENANCE"}


def test_gates_have_roles_and_can_be_controlled(park, client, admin_headers, monkeypatch):
    gates = client.get("/api/dashboard/gates", headers=admin_headers).json()
    assert all({"name", "role", "zone", "state", "broken", "under_maintenance"} <= g.keys() for g in gates)
    assert {g["name"]: g["role"] for g in gates} == {"gateA": "entrance", "gateB": "exit", "gateC": None}

    calls = []

    class FakeSim:
        def open_gate(self, name):
            calls.append(("open", name))

        def close_gate(self, name):
            calls.append(("close", name))

        def repair_gate(self, name):
            calls.append(("repair", name))

    monkeypatch.setattr(control, "get_simulator", lambda: FakeSim())
    assert client.post("/api/control/gates/gateA/close", headers=admin_headers).status_code == 202
    assert client.post("/api/control/gates/gateB/open", headers=admin_headers).status_code == 202
    assert calls == [("close", "gateA"), ("open", "gateB")]
    # the dashboard shows the movement at once (the webhook later confirms Open / Closed)
    states = {g["name"]: g["state"] for g in client.get("/api/dashboard/gates", headers=admin_headers).json()}
    assert (states["gateA"], states["gateB"]) == ("Closing", "Opening")


def test_simulator_refusal_is_a_readable_error(park, client, admin_headers, monkeypatch):
    import httpx

    class RefusingSim:
        def open_gate(self, name):
            request = httpx.Request("POST", "http://sim/barrier-gates/gateA/open")
            raise httpx.HTTPStatusError("broken", request=request,
                                        response=httpx.Response(400, text="Gate is broken", request=request))

        close_gate = repair_gate = open_gate

    monkeypatch.setattr(control, "get_simulator", lambda: RefusingSim())
    r = client.post("/api/control/gates/gateA/open", headers=admin_headers)
    assert r.status_code == 502 and "Gate is broken" in r.json()["detail"]  # api.js shows `detail`


def test_activity_feed_and_vehicle_search(park, client, admin_headers):
    from app.services import parking

    parking.record_arrival(park, "WCT 759")
    parking.mark_parked(park, "WCT 759", "S1")
    feed = client.get("/api/history/events", headers=admin_headers,
                      params={"limit": 10, "event_type": ["CAR_ARRIVED", "CAR_PARKED", "CAR_DEPARTED"]}).json()
    assert [e["event_type"] for e in feed] == ["CAR_PARKED", "CAR_ARRIVED"]
    assert all({"id", "car_plate", "parking_spot", "event_time"} <= e.keys() for e in feed)

    sessions = client.get("/api/history/sessions", headers=admin_headers,
                          params={"plate": "wct", "limit": 50}).json()  # prefix, any case
    assert len(sessions) == 1
    assert {"car_plate", "car_type", "spot_name", "status", "entry_time", "exit_time"} <= sessions[0].keys()
    assert (sessions[0]["spot_name"], sessions[0]["status"]) == ("S1", "PARKED")
    park.expire_all()
    assert park.scalar(select(ParkingSession.status)) == SessionStatus.PARKED
