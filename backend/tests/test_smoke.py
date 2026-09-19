"""Checks that the app starts, the DB schema works, and auth/roles are enforced."""

import time
import uuid

from app.services.webhook_handlers import compute_signature
from tests.conftest import login


def signed(payload):
    return {**payload, "Signature": compute_signature(payload)}


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_login_rejects_wrong_password(client):
    r = client.post("/api/auth/login", data={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_protected_routes_need_token(client):
    assert client.get("/api/dashboard").status_code == 401


def test_admin_creates_operator_and_roles_are_enforced(client, admin_headers):
    r = client.post("/api/auth/users", json={"username": "op1", "password": "pw"}, headers=admin_headers)
    assert r.status_code in (201, 409)  # 409 = already created by a previous test run
    op = login(client, "op1", "pw")
    assert client.get("/api/auth/me", headers=op).json()["role"] == "OPERATOR"
    # operator can read the dashboard but cannot do admin-only things
    assert client.get("/api/dashboard", headers=op).status_code == 200
    assert client.post("/api/control/sync", headers=op).status_code == 403
    assert client.get("/api/auth/users", headers=op).status_code == 403


def test_dashboard_and_history_on_empty_db(db, client, admin_headers):
    d = client.get("/api/dashboard", headers=admin_headers).json()
    assert d["zones"] == [] and d["park_full"] is True
    assert client.get("/api/history/sessions?plate=AB", headers=admin_headers).json() == []


def test_webhook_stores_unknown_events(client):
    headers = login(client, "admin", "admin-pw")
    url = "/api/history/events?event_type=WEBHOOK&limit=500"
    before = len(client.get(url, headers=headers).json())
    r = client.post("/webhook", json=signed({"EventClass": "SomethingNew", "EventId": str(uuid.uuid4())}))
    assert r.json()["status"] in ("queued", "dispatched")  # main.py's reply wording varies by version
    for _ in range(100):  # stored as event_type='WEBHOOK' by db_hook's background worker
        if len(client.get(url, headers=headers).json()) > before:
            return
        time.sleep(0.1)
    raise AssertionError("webhook was not stored")
