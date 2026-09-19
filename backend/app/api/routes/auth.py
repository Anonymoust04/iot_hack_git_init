"""Login + user management for OUR dashboard (not the simulator's login)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.api.routes.login_attempts import LoginOut
from app.core.permissions import ALL_PERMISSIONS, OPERATOR_DEFAULT_PERMISSIONS, Permission
from app.core.security import create_access_token, hash_password, verify_password
from app.models import Role, User, UserPermission
from app.schemas import UserCreate, UserOut, UserUpdate
from app.services.audit import record_audit
from app.services.login_attempts import client_ip, get_last_login_attempts, record_login_attempt

router = APIRouter(prefix="/api/auth", tags=["auth"])


def default_permissions(role: Role) -> set[Permission]:
    return set(ALL_PERMISSIONS if role == Role.ADMIN else OPERATOR_DEFAULT_PERMISSIONS)


def user_permissions(db: DbSession, user: User) -> list[Permission]:
    rows = db.scalars(select(UserPermission).where(UserPermission.user_id == user.id)).all()
    if not rows:  # Existing Level 1 users get their normal role defaults.
        return sorted(default_permissions(user.role), key=str)
    return sorted((Permission(row.permission) for row in rows if row.enabled), key=str)


def user_out(db: DbSession, user: User) -> UserOut:
    return UserOut(id=user.id, username=user.username, role=user.role, permissions=user_permissions(db, user))


def save_permissions(db: DbSession, user: User, permissions: list[Permission]) -> None:
    requested = set(permissions)
    db.query(UserPermission).filter(UserPermission.user_id == user.id).delete(synchronize_session=False)
    db.add_all([
        UserPermission(user_id=user.id, permission=permission.value, enabled=permission in requested)
        for permission in Permission
    ])


@router.post("/login", response_model=LoginOut)
def login(request: Request, db: DbSession, form: Annotated[OAuth2PasswordRequestForm, Depends()]):
    user = db.scalar(select(User).where(User.username == form.username))
    if user is None or not verify_password(form.password, user.password_hash):
        record_login_attempt(db, form.username, False, client_ip(request))  # Level 2: failed attempt
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password")
    record_login_attempt(db, user.username, True, client_ip(request))
    # Same fields as before (access_token, token_type, role) + the last 3 attempts, newest first
    return LoginOut(access_token=create_access_token(user.username, user.role), role=user.role,
                    login_attempts=get_last_login_attempts(db, user.username))


@router.get("/me", response_model=UserOut)
def me(db: DbSession, user: CurrentUser):
    return user_out(db, user)


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, request: Request, db: DbSession, admin: AdminUser):
    if db.scalar(select(User).where(User.username == body.username)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")
    user = User(username=body.username, password_hash=hash_password(body.password), role=body.role)
    db.add(user)
    db.flush()
    save_permissions(db, user, body.permissions if body.permissions is not None else list(default_permissions(body.role)))
    db.commit()
    created = user_out(db, user)
    record_audit(db, "USER_CREATED", actor=admin.username, target_type="user", target_name=user.username,
                 details={"role": user.role.value, "permissions": [str(p) for p in created.permissions]},
                 ip_address=client_ip(request))
    return created


@router.get("/users", response_model=list[UserOut])
def list_users(db: DbSession, _: AdminUser):
    return [user_out(db, user) for user in db.scalars(select(User).order_by(User.id)).all()]


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, request: Request, db: DbSession, actor: AdminUser):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    before = {"username": user.username, "role": user.role.value,
              "permissions": {str(p) for p in user_permissions(db, user)}}
    if body.username is not None:
        username = body.username.strip()
        if not username:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Username cannot be empty")
        other = db.scalar(select(User).where(User.username == username, User.id != user.id))
        if other:
            raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")
        user.username = username
    if body.password:
        user.password_hash = hash_password(body.password)
    if body.role is not None and body.role != user.role:
        if user.role == Role.ADMIN and body.role != Role.ADMIN and db.scalar(select(func.count()).select_from(User).where(User.role == Role.ADMIN)) <= 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "At least one Admin account is required")
        user.role = body.role
    if body.permissions is not None:
        save_permissions(db, user, body.permissions)
    elif body.role is not None:
        save_permissions(db, user, list(default_permissions(user.role)))
    db.commit()
    updated = user_out(db, user)
    after = {str(p) for p in updated.permissions}
    changes = {}
    if user.username != before["username"]:
        changes.update(old_username=before["username"], new_username=user.username)
    if user.role.value != before["role"]:
        changes.update(old_role=before["role"], new_role=user.role.value)
    if after != before["permissions"]:
        changes.update(added=sorted(after - before["permissions"]), removed=sorted(before["permissions"] - after))
    if body.password:
        changes["credential_reset"] = True  # never the password itself
    if changes:
        record_audit(db, "USER_UPDATED", actor=actor.username, target_type="user", target_name=user.username,
                     details=changes, ip_address=client_ip(request))
    return updated


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, request: Request, db: DbSession, actor: AdminUser):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.id == actor.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete your own account")
    if user.role == Role.ADMIN and db.scalar(select(func.count()).select_from(User).where(User.role == Role.ADMIN)) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "At least one Admin account is required")
    username, role = user.username, user.role.value
    db.delete(user)
    db.commit()
    record_audit(db, "USER_DELETED", actor=actor.username, target_type="user", target_name=username,
                 details={"role": role}, ip_address=client_ip(request))
