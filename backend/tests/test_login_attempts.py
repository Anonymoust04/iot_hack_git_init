"""Level 2: login attempt logging (services/login_attempts.py, routes/login_attempts.py).

`integrated_login` below is the FastAPI owner's handoff for auth.py's /login, copied as-is
(docs/LEVEL2_TASKS.md), so these tests prove the snippet works before it is wired in."""

from concurrent.futures import ThreadPoolExecutor
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api.deps import DbSession
from app.api.routes import login_attempts as login_attempts_routes
from app.api.routes.login_attempts import LoginOut
from app.core.security import create_access_token, verify_password
from app.db.session import SessionLocal
from app.models import User
from app.models.login_attempt import LoginAttempt
from app.services.login_attempts import client_ip, get_last_login_attempts, record_login_attempt


# ---- the handoff snippet, exactly as auth.py's login would look --------------

def integrated_login(request: Request, db: DbSession, form: Annotated[OAuth2PasswordRequestForm, Depends()]):
    user = db.scalar(select(User).where(User.username == form.username))
    if user is None or not verify_password(form.password, user.password_hash):
        record_login_attempt(db, form.username, False, client_ip(request))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password")
    record_login_attempt(db, user.username, True, client_ip(request))
    return LoginOut(access_token=create_access_token(user.username, user.role), role=user.role,
                    login_attempts=get_last_login_attempts(db, user.username))


@pytest.fixture
def attempts(db, client):
    """Clean login_attempts table; `client` makes sure the test admin (admin / admin-pw) exists."""
    db.execute(delete(LoginAttempt))
    db.commit()
    yield db
    db.execute(delete(LoginAttempt))
    db.commit()


@pytest.fixture
def login_app():
    app = FastAPI()
    app.add_api_route("/api/auth/login", integrated_login, methods=["POST"], response_model=LoginOut)
    app.include_router(login_attempts_routes.router)
    return TestClient(app)


def rows(db, username):
    db.expire_all()
    return db.scalars(select(LoginAttempt).where(LoginAttempt.username == username).order_by(LoginAttempt.id)).all()


# ---- the 5 required cases ----------------------------------------------------

def test_1_successful_login_adds_one_success_row(attempts, login_app):
    r = login_app.post("/api/auth/login", data={"username": "admin", "password": "admin-pw"})
    assert r.status_code == 200
    assert [(a.success, a.ip_address) for a in rows(attempts, "admin")] == [(True, "testclient")]
    body = r.json()
    assert body["access_token"] and body["role"] == "ADMIN"                    # old fields unchanged
    assert [a["success"] for a in body["login_attempts"]] == [True]            # includes this login


def test_2_wrong_password_adds_one_failed_row_and_no_secret(attempts, login_app):
    r = login_app.post("/api/auth/login", data={"username": "admin", "password": "not-the-password"})
    assert r.status_code == 401 and r.json() == {"detail": "Wrong username or password"}
    [row] = rows(attempts, "admin")
    assert row.success is False
    stored = " ".join(str(v) for v in (row.username, row.ip_address, row.attempted_at))
    assert "not-the-password" not in stored and "admin-pw" not in stored


def test_3_latest_three_newest_first(attempts, login_app):
    for password in ("bad-1", "admin-pw", "bad-2", "bad-3"):
        login_app.post("/api/auth/login", data={"username": "admin", "password": password})
    r = login_app.post("/api/auth/login", data={"username": "admin", "password": "admin-pw"})
    # newest first: this success, bad-3, bad-2 (older bad-1 / first success not shown)
    assert [a["success"] for a in r.json()["login_attempts"]] == [True, False, False]
    assert [a.success for a in get_last_login_attempts(attempts, "admin")] == [True, False, False]
    assert len(rows(attempts, "admin")) == 5


def test_4_only_one_previous_attempt(attempts):
    record_login_attempt(attempts, "admin", True, "10.0.0.1")
    [only] = get_last_login_attempts(attempts, "admin", 3)
    assert (only.success, only.ip_address) == (True, "10.0.0.1")
    assert get_last_login_attempts(attempts, "nobody-yet") == []


def test_5_unknown_username_is_logged_safely(attempts, login_app):
    r = login_app.post("/api/auth/login", data={"username": "ghost'); DROP TABLE users;--", "password": "x"})
    assert r.status_code == 401
    [row] = rows(attempts, "ghost'); DROP TABLE users;--")
    assert row.success is False
    assert attempts.scalar(select(User).where(User.username == "admin")) is not None  # users intact


# ---- robustness ----------------------------------------------------------------

def test_simultaneous_attempts_are_all_recorded(attempts):
    def attempt(i):
        with SessionLocal() as s:  # each login = its own connection / transaction
            record_login_attempt(s, "admin", i % 2 == 0, f"10.0.0.{i}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(attempt, range(20)))
    assert len(rows(attempts, "admin")) == 20
    assert len(get_last_login_attempts(attempts, "admin")) == 3


def test_very_long_username_and_ip_are_cut_not_rejected(attempts):
    record_login_attempt(attempts, "u" * 500, False, "9" * 100)
    [row] = rows(attempts, "u" * 64)
    assert (len(row.username), len(row.ip_address)) == (64, 45)


def test_endpoint_shows_only_my_attempts(attempts, login_app):
    record_login_attempt(attempts, "someone-else", False)
    token = login_app.post("/api/auth/login", data={"username": "admin", "password": "admin-pw"}).json()["access_token"]
    assert login_app.get("/api/auth/login-attempts").status_code == 401          # needs login
    mine = login_app.get("/api/auth/login-attempts", headers={"Authorization": f"Bearer {token}"}).json()
    assert [a["success"] for a in mine] == [True]
    assert set(mine[0]) == {"success", "ip_address", "attempted_at"}             # no username/secret fields
