"""Level 2: audit log (services/audit.py, routes/audit.py). Router mounted on a small app here,
because db_hook.py registers it only after Zhi Hong wires it in."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from app.api.routes import audit as audit_routes
from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal
from app.models import Role, User, utcnow
from app.models.audit_log import AuditLog
from app.services.audit import list_audit, record_audit


@pytest.fixture
def audit_db(db):
    db.execute(delete(AuditLog))
    db.commit()
    yield db
    db.execute(delete(AuditLog))
    db.commit()


@pytest.fixture
def api(audit_db):
    """TestClient with the audit router, plus Admin / Operator tokens."""
    for name, role in (("audit_admin", Role.ADMIN), ("audit_op", Role.OPERATOR)):
        if audit_db.scalar(select(User).where(User.username == name)) is None:
            audit_db.add(User(username=name, password_hash=hash_password("pw"), role=role))
    audit_db.commit()
    app = FastAPI()
    app.include_router(audit_routes.router)
    client = TestClient(app)
    client.admin = {"Authorization": f"Bearer {create_access_token('audit_admin', Role.ADMIN)}"}
    client.operator = {"Authorization": f"Bearer {create_access_token('audit_op', Role.OPERATOR)}"}
    return client


def test_record_and_read_back(audit_db):
    saved = record_audit(audit_db, "GATE_OPEN", actor="op1", target_type="gate", target_name="gateA",
                         ip_address="10.0.0.5")
    assert saved is not None
    [row] = list_audit(audit_db)
    assert (row.actor, row.action, row.target_type, row.target_name, row.success, row.ip_address) == (
        "op1", "GATE_OPEN", "gate", "gateA", True, "10.0.0.5")
    assert row.created_at is not None


def test_system_actions_have_no_actor_and_failures_are_kept(audit_db):
    record_audit(audit_db, "AUTO_REPAIR", target_type="fan", target_name="fan3", success=False,
                 details={"error": "Simulator refused (400)"})
    [row] = list_audit(audit_db)
    assert (row.actor, row.success, row.details) == (None, False, {"error": "Simulator refused (400)"})


def test_newest_first(audit_db):
    for action in ("FIRST", "SECOND", "THIRD"):
        record_audit(audit_db, action)
    assert [r.action for r in list_audit(audit_db)] == ["THIRD", "SECOND", "FIRST"]


def test_each_filter(audit_db):
    record_audit(audit_db, "GATE_OPEN", actor="op1", target_type="gate", target_name="gateA")
    record_audit(audit_db, "SPOT_REPAIR", actor="op2", target_type="spot", target_name="S12")
    record_audit(audit_db, "FAN_ON", target_type="fan", target_name="fan1")
    assert [r.action for r in list_audit(audit_db, actor="op1")] == ["GATE_OPEN"]
    assert [r.action for r in list_audit(audit_db, action="SPOT_REPAIR")] == ["SPOT_REPAIR"]
    assert [r.action for r in list_audit(audit_db, target_type="fan")] == ["FAN_ON"]
    assert [r.action for r in list_audit(audit_db, target_name="S12")] == ["SPOT_REPAIR"]
    now = audit_db.scalar(select(AuditLog.created_at).order_by(AuditLog.id.desc()).limit(1))
    assert len(list_audit(audit_db, since=now - timedelta(minutes=5))) == 3
    assert list_audit(audit_db, since=now + timedelta(minutes=5)) == []
    assert list_audit(audit_db, until=now - timedelta(minutes=5)) == []
    assert [r.action for r in list_audit(audit_db, limit=1, offset=1)] == ["SPOT_REPAIR"]


def test_details_round_trip_and_secrets_are_masked(audit_db):
    record_audit(audit_db, "USER_ROLE_CHANGED", actor="audit_admin", target_type="user", target_name="op1",
                 details={"old_role": "OPERATOR", "new_role": "ADMIN", "cost": Decimal("10.50"),
                          "at": utcnow(), "password": "hunter2", "nested": {"api_token": "abc"}})
    [row] = list_audit(audit_db)
    assert row.details["old_role"] == "OPERATOR" and row.details["new_role"] == "ADMIN"
    assert row.details["cost"] == "10.50"                                   # Decimal saved as text
    assert row.details["password"] == "***" and row.details["nested"]["api_token"] == "***"
    assert "hunter2" not in str(row.details) and "abc" not in str(row.details)


def test_long_values_are_cut_not_rejected(audit_db):
    record_audit(audit_db, "A" * 200, actor="u" * 200, target_type="t" * 200, target_name="n" * 200,
                 ip_address="9" * 100)
    [row] = list_audit(audit_db)
    assert (len(row.action), len(row.actor), len(row.target_type), len(row.target_name), len(row.ip_address)) == (
        64, 64, 32, 64, 45)


def test_never_raises_when_database_is_down():
    dead = create_engine("mysql+pymysql://x:y@127.0.0.1:9/nodb", connect_args={"connect_timeout": 2})
    with Session(bind=dead) as db:
        assert record_audit(db, "GATE_OPEN", actor="op1") is None  # logged, caller carries on


def test_does_not_commit_the_callers_unfinished_work(audit_db):
    with SessionLocal() as caller:
        caller.add(User(username="audit_uncommitted", password_hash="x", role=Role.OPERATOR))
        caller.flush()                                   # pending in the caller's transaction
        assert record_audit(caller, "USER_CREATED", actor="audit_admin", target_name="audit_uncommitted")
        caller.rollback()                                # caller changes its mind
    audit_db.expire_all()
    assert audit_db.scalar(select(User).where(User.username == "audit_uncommitted")) is None
    assert [r.action for r in list_audit(audit_db)] == ["USER_CREATED"]   # audit row kept on its own


def test_twenty_at_once_all_saved(audit_db):
    def one(i):
        with SessionLocal() as s:
            record_audit(s, "FAN_ON", target_type="fan", target_name=f"fan{i}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(one, range(20)))
    assert len(list_audit(audit_db, limit=100)) == 20


def test_api_is_admin_only(api, audit_db):
    record_audit(audit_db, "GATE_CLOSE", actor="op1", target_type="gate", target_name="gateB")
    assert api.get("/api/audit").status_code == 401                          # no token
    assert api.get("/api/audit", headers=api.operator).status_code == 403    # Operator
    r = api.get("/api/audit", headers=api.admin, params={"target_name": "gateB"})
    assert r.status_code == 200
    [row] = r.json()
    assert (row["action"], row["actor"], row["target_type"]) == ("GATE_CLOSE", "op1", "gate")
    assert set(row) == {"id", "actor", "action", "target_type", "target_name", "success", "details",
                        "ip_address", "created_at"}
    assert api.get("/api/audit", headers=api.admin, params={"limit": 501}).status_code == 422
