"""Level 2: user management + authorities (services/user_admin.py, routes/admin_users.py).
Routers mounted on a small app here, because db_hook.py registers them only after Zhi Hong wires them in."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, select

from app.api.routes import admin_users
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import SessionLocal
from app.models import Role, User
from app.models.audit_log import AuditLog
from app.models.user_permission import UserPermission
from app.services import user_admin
from app.services.user_admin import CanRepair


@pytest.fixture
def users_db(db):
    """Only this test's users exist (exact last-admin checks); the previous users come back afterwards."""
    saved = [dict(id=u.id, username=u.username, password_hash=u.password_hash, role=u.role, created_at=u.created_at)
             for u in db.scalars(select(User)).all()]
    db.execute(delete(User))
    db.execute(delete(AuditLog))
    db.commit()
    db.add(User(username="ua_admin", password_hash=hash_password("admin-pw"), role=Role.ADMIN))
    db.commit()
    yield db
    db.rollback()
    db.execute(delete(User))
    db.execute(delete(AuditLog))
    if saved:
        db.execute(insert(User), saved)
    db.commit()


def token(username: str, role: Role) -> dict:
    return {"Authorization": f"Bearer {create_access_token(username, role)}"}


@pytest.fixture
def api(users_db):
    app = FastAPI()
    app.include_router(admin_users.router)

    @app.post("/demo/repair")            # how Zhi Hong will protect a real repair route
    def demo_repair(user: CanRepair):
        return {"ok": user.username}

    c = TestClient(app)
    c.admin = token("ua_admin", Role.ADMIN)
    return c


def make_operator(api, name="op1", permissions=()):
    r = api.post("/api/admin/users", headers=api.admin,
                 json={"username": name, "password": "pw1234", "role": "OPERATOR", "permissions": list(permissions)})
    assert r.status_code == 201, r.text
    return r.json()


def audit_actions(db):
    db.expire_all()
    return [(a.action, a.target_name, a.details) for a in db.scalars(select(AuditLog).order_by(AuditLog.id)).all()]


# ---- create / list -----------------------------------------------------------

def test_create_user_with_authorities_and_list(api, users_db):
    created = make_operator(api, permissions=["REPAIR", "GATE_CONTROL"])
    assert (created["username"], created["role"], created["permissions"]) == ("op1", "OPERATOR", ["GATE_CONTROL", "REPAIR"])
    listed = {u["username"]: u for u in api.get("/api/admin/users", headers=api.admin).json()}
    assert listed["op1"]["permissions"] == ["GATE_CONTROL", "REPAIR"]
    assert listed["ua_admin"]["permissions"] == sorted(user_admin.PERMISSIONS)    # admins: everything
    user = users_db.scalar(select(User).where(User.username == "op1"))
    assert verify_password("pw1234", user.password_hash)                           # stored hashed
    assert audit_actions(users_db) == [("USER_CREATED", "op1", {"role": "OPERATOR", "permissions": ["GATE_CONTROL", "REPAIR"]})]


def test_permission_list_for_the_checkboxes(api):
    names = [p["name"] for p in api.get("/api/admin/permissions", headers=api.admin).json()]
    assert names == ["REPAIR", "FINANCIAL_REPORT", "GATE_CONTROL", "LIGHT_CONTROL", "FAN_CONTROL"]


def test_create_refusals(api):
    make_operator(api, "dup")
    body = {"username": "dup", "password": "pw1234"}
    assert api.post("/api/admin/users", headers=api.admin, json=body).status_code == 409          # duplicate
    body = {"username": "x1", "password": "pw1234", "permissions": ["FLY"]}
    r = api.post("/api/admin/users", headers=api.admin, json=body)
    assert r.status_code == 422 and "FLY" in r.json()["detail"]                                     # unknown authority
    assert api.post("/api/admin/users", headers=api.admin, json={"username": "x2", "password": "1"}).status_code == 422
    assert api.post("/api/admin/users", headers=api.admin, json={"username": "   ", "password": "pw1234"}).status_code == 422


# ---- edit ------------------------------------------------------------------------

def test_edit_authorities_role_and_password(api, users_db):
    op = make_operator(api, permissions=["REPAIR"])
    r = api.patch(f"/api/admin/users/{op['id']}", headers=api.admin, json={"permissions": ["FAN_CONTROL", "LIGHT_CONTROL"]})
    assert r.status_code == 200 and r.json()["permissions"] == ["FAN_CONTROL", "LIGHT_CONTROL"]
    r = api.patch(f"/api/admin/users/{op['id']}", headers=api.admin, json={"password": "new-pass"})
    assert r.json()["permissions"] == ["FAN_CONTROL", "LIGHT_CONTROL"]                            # untouched
    users_db.expire_all()
    assert verify_password("new-pass", users_db.get(User, op["id"]).password_hash)
    r = api.patch(f"/api/admin/users/{op['id']}", headers=api.admin, json={"role": "ADMIN"})
    assert r.json()["role"] == "ADMIN" and r.json()["permissions"] == sorted(user_admin.PERMISSIONS)
    updates = [d for a, _, d in audit_actions(users_db) if a == "USER_UPDATED"]
    assert updates == [{"added": ["FAN_CONTROL", "LIGHT_CONTROL"], "removed": ["REPAIR"]},
                       {"credential_reset": True},
                       {"old_role": "OPERATOR", "new_role": "ADMIN"}]
    assert "new-pass" not in str(audit_actions(users_db))                                        # never the password


def test_edit_refusals(api):
    assert api.patch("/api/admin/users/999999", headers=api.admin, json={"role": "OPERATOR"}).status_code == 404
    admin_id = next(u["id"] for u in api.get("/api/admin/users", headers=api.admin).json() if u["username"] == "ua_admin")
    r = api.patch(f"/api/admin/users/{admin_id}", headers=api.admin, json={"role": "OPERATOR"})
    assert r.status_code == 409 and "last admin" in r.json()["detail"]


# ---- remove ----------------------------------------------------------------------

def test_delete_user_removes_its_authorities(api, users_db):
    op = make_operator(api, permissions=["REPAIR", "FAN_CONTROL"])
    assert api.delete(f"/api/admin/users/{op['id']}", headers=api.admin).status_code == 204
    users_db.expire_all()
    assert users_db.get(User, op["id"]) is None
    assert users_db.scalars(select(UserPermission).where(UserPermission.user_id == op["id"])).all() == []
    assert audit_actions(users_db)[-1] == ("USER_DELETED", "op1", {"role": "OPERATOR"})
    assert api.delete(f"/api/admin/users/{op['id']}", headers=api.admin).status_code == 404


def test_cannot_delete_self_or_last_admin(api):
    me = next(u for u in api.get("/api/admin/users", headers=api.admin).json() if u["username"] == "ua_admin")
    r = api.delete(f"/api/admin/users/{me['id']}", headers=api.admin)
    assert r.status_code == 409 and "own account" in r.json()["detail"]
    boss2 = api.post("/api/admin/users", headers=api.admin,
                     json={"username": "boss2", "password": "pw1234", "role": "ADMIN"}).json()
    assert api.delete(f"/api/admin/users/{me['id']}", headers=token("boss2", Role.ADMIN)).status_code == 204
    r = api.delete(f"/api/admin/users/{boss2['id']}", headers=token("boss2", Role.ADMIN))
    assert r.status_code == 409                                                                   # self + last admin


def test_two_admins_demoting_each_other_at_once_keep_one_admin(api, users_db):
    b = api.post("/api/admin/users", headers=api.admin, json={"username": "boss_b", "password": "pw1234", "role": "ADMIN"}).json()
    a_id = users_db.scalar(select(User.id).where(User.username == "ua_admin"))

    def demote(user_id):
        with SessionLocal() as s:
            try:
                user_admin.update_user(s, user_id, role=Role.OPERATOR, actor="race")
                return "demoted"
            except user_admin.UserAdminError:
                return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(demote, [a_id, b["id"]]))
    users_db.expire_all()
    assert sorted(results) == ["demoted", "refused"]
    assert users_db.scalar(select(User).where(User.role == Role.ADMIN)) is not None


# ---- who may do what ---------------------------------------------------------------

def test_admin_only(api):
    make_operator(api, "plainop")
    op = token("plainop", Role.OPERATOR)
    assert api.get("/api/admin/users").status_code == 401
    for method, url in (("get", "/api/admin/users"), ("get", "/api/admin/permissions"),
                        ("post", "/api/admin/users"), ("patch", "/api/admin/users/1"), ("delete", "/api/admin/users/1")):
        assert getattr(api, method)(url, headers=op).status_code == 403, (method, url)


def test_my_permissions_and_require_permission(api):
    make_operator(api, "fixer", permissions=["REPAIR"])
    make_operator(api, "viewer")
    fixer, viewer = token("fixer", Role.OPERATOR), token("viewer", Role.OPERATOR)
    assert api.get("/api/auth/me/permissions", headers=fixer).json() == {
        "username": "fixer", "role": "OPERATOR", "permissions": ["REPAIR"]}
    assert api.get("/api/auth/me/permissions", headers=viewer).json()["permissions"] == []
    assert api.get("/api/auth/me/permissions", headers=api.admin).json()["permissions"] == sorted(user_admin.PERMISSIONS)

    assert api.post("/demo/repair", headers=fixer).json() == {"ok": "fixer"}
    r = api.post("/demo/repair", headers=viewer)
    assert r.status_code == 403 and r.json()["detail"] == "Missing permission: REPAIR"
    assert api.post("/demo/repair", headers=api.admin).status_code == 200
    assert api.post("/demo/repair").status_code == 401


def test_unknown_permission_name_is_a_coding_error():
    with pytest.raises(ValueError):
        user_admin.require_permission("REPAIRS")
