"""Level 2: user management + per-user authorities (RBAC data).

Roles (users.role): ADMIN = everything; OPERATOR = view + only the authorities granted below.
Authorities live in user_permissions, so no existing table changes.

    list_users(db)                                    users with their effective authorities
    create_user(db, username, password, role, permissions, actor=...)
    update_user(db, user_id, role=..., permissions=..., password=..., actor=...)
    delete_user(db, user_id, actor=...)
    has_permission(db, user, "REPAIR")                True/False (admins always True)
    require_permission("REPAIR")                      FastAPI dependency -> 403 if missing

Safety: nobody can delete their own account, and the last ADMIN can't be deleted or demoted
(admin rows are locked while checking, so two admins can't demote each other at the same time).
Every change is written to the audit log.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession
from app.core.security import hash_password
from app.models import Role, User
from app.models.user_permission import UserPermission
from app.services.audit import record_audit
from app.services.parking import retry_on_deadlock

# The authorities an admin can grant (Level 2 task split). Admins have all of them.
PERMISSIONS: dict[str, str] = {
    "REPAIR": "Send repair commands (gates, spots, fans)",
    "FINANCIAL_REPORT": "Generate and view financial reports",
    "GATE_CONTROL": "Open / close barrier gates",
    "LIGHT_CONTROL": "Switch lights on / off",
    "FAN_CONTROL": "Switch exhaust fans on / off",
}


class UserAdminError(Exception):
    """A refused change; `status_code` + message go straight to the API response."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class UserWithPermissions:
    user: User
    permissions: list[str]


def _check_permissions(permissions: list[str]) -> list[str]:
    unknown = sorted(set(permissions) - PERMISSIONS.keys())
    if unknown:
        raise UserAdminError(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown permission(s): {', '.join(unknown)}")
    return sorted(set(permissions))


def _granted(db: Session, user_id: int) -> list[str]:
    return sorted(db.scalars(select(UserPermission.permission).where(UserPermission.user_id == user_id)).all())


def effective_permissions(db: Session, user: User) -> list[str]:
    return sorted(PERMISSIONS) if user.role == Role.ADMIN else _granted(db, user.id)


def has_permission(db: Session, user: User, permission: str) -> bool:
    return permission in effective_permissions(db, user)


def list_users(db: Session) -> list[UserWithPermissions]:
    users = db.scalars(select(User).order_by(User.id)).all()
    rows = db.execute(select(UserPermission.user_id, UserPermission.permission)).all()
    granted: dict[int, list[str]] = {}
    for user_id, permission in rows:
        granted.setdefault(user_id, []).append(permission)
    return [UserWithPermissions(u, sorted(PERMISSIONS) if u.role == Role.ADMIN else sorted(granted.get(u.id, [])))
            for u in users]


def _lock_admins(db: Session) -> int:
    """Lock every ADMIN row until commit and return how many there are (last-admin checks)."""
    return len(db.scalars(select(User.id).where(User.role == Role.ADMIN).with_for_update()).all())


def _set_permissions(db: Session, user_id: int, permissions: list[str], actor: str | None) -> None:
    db.execute(delete(UserPermission).where(UserPermission.user_id == user_id))
    db.add_all(UserPermission(user_id=user_id, permission=p, granted_by=actor) for p in permissions)


def create_user(db: Session, username: str, password: str, role: Role = Role.OPERATOR,
                permissions: list[str] | None = None, *, actor: str | None = None,
                ip_address: str | None = None) -> UserWithPermissions:
    username = username.strip()
    if not username:
        raise UserAdminError(status.HTTP_422_UNPROCESSABLE_ENTITY, "Username is required")
    perms = _check_permissions(permissions or [])
    try:
        user = User(username=username, password_hash=hash_password(password), role=role)
        db.add(user)
        db.flush()  # get user.id; a duplicate username fails here
        _set_permissions(db, user.id, perms, actor)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise UserAdminError(status.HTTP_409_CONFLICT, "Username already exists")
    except Exception:
        db.rollback()
        raise
    record_audit(db, "USER_CREATED", actor=actor, target_type="user", target_name=user.username,
                 details={"role": user.role.value, "permissions": perms}, ip_address=ip_address)
    return UserWithPermissions(user, effective_permissions(db, user))


@retry_on_deadlock
def update_user(db: Session, user_id: int, *, role: Role | None = None, permissions: list[str] | None = None,
                password: str | None = None, actor: str | None = None,
                ip_address: str | None = None) -> UserWithPermissions:
    """Only the fields given change. permissions replaces the whole list."""
    perms = _check_permissions(permissions) if permissions is not None else None
    try:
        # admin rows first, always in the same order, then the user: no deadlock between two admins
        admins = _lock_admins(db) if role is not None else None
        user = db.get(User, user_id, with_for_update=True)
        if user is None:
            raise UserAdminError(status.HTTP_404_NOT_FOUND, "User not found")
        changes: dict = {}
        if role is not None and role != user.role:
            if user.role == Role.ADMIN and admins <= 1:
                raise UserAdminError(status.HTTP_409_CONFLICT, "Can't demote the last admin")
            changes.update(old_role=user.role.value, new_role=role.value)
            user.role = role
        if perms is not None:
            old = _granted(db, user.id)
            if perms != old:
                changes.update(added=sorted(set(perms) - set(old)), removed=sorted(set(old) - set(perms)))
                _set_permissions(db, user.id, perms, actor)
        if password is not None:
            user.password_hash = hash_password(password)
            changes["credential_reset"] = True  # never the password itself
        db.commit()
    except Exception:
        db.rollback()
        raise
    if changes:
        record_audit(db, "USER_UPDATED", actor=actor, target_type="user", target_name=user.username,
                     details=changes, ip_address=ip_address)
    return UserWithPermissions(user, effective_permissions(db, user))


@retry_on_deadlock
def delete_user(db: Session, user_id: int, *, actor: str | None = None, ip_address: str | None = None) -> None:
    try:
        admins = _lock_admins(db)  # admin rows first (same order as update_user)
        user = db.get(User, user_id, with_for_update=True)
        if user is None:
            raise UserAdminError(status.HTTP_404_NOT_FOUND, "User not found")
        if actor is not None and user.username == actor:
            raise UserAdminError(status.HTTP_409_CONFLICT, "You can't delete your own account")
        if user.role == Role.ADMIN and admins <= 1:
            raise UserAdminError(status.HTTP_409_CONFLICT, "Can't delete the last admin")
        username, role = user.username, user.role.value
        db.delete(user)  # its user_permissions rows go with it (ON DELETE CASCADE)
        db.commit()
    except Exception:
        db.rollback()
        raise
    record_audit(db, "USER_DELETED", actor=actor, target_type="user", target_name=username,
                 details={"role": role}, ip_address=ip_address)


def require_permission(permission: str):
    """Route dependency: `_: Annotated[User, Depends(require_permission("REPAIR"))]`.
    401 without login (from CurrentUser), 403 without the authority. Admins always pass."""
    if permission not in PERMISSIONS:
        raise ValueError(f"Unknown permission {permission!r}")

    def checker(user: CurrentUser, db: DbSession) -> User:
        if not has_permission(db, user, permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {permission}")
        return user

    return checker


# Ready-made annotations for route parameters, e.g. `def repair_spot(name: str, user: CanRepair)`
CanRepair = Annotated[User, Depends(require_permission("REPAIR"))]
CanViewFinance = Annotated[User, Depends(require_permission("FINANCIAL_REPORT"))]
CanControlGates = Annotated[User, Depends(require_permission("GATE_CONTROL"))]
CanControlLights = Annotated[User, Depends(require_permission("LIGHT_CONTROL"))]
CanControlFans = Annotated[User, Depends(require_permission("FAN_CONTROL"))]
