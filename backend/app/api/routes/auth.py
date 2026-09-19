"""Login + user management for OUR dashboard (not the simulator's login)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.core.permissions import ALL_PERMISSIONS, OPERATOR_DEFAULT_PERMISSIONS, Permission
from app.core.security import create_access_token, hash_password, verify_password
from app.models import Role, User, UserPermission
from app.schemas import TokenOut, UserCreate, UserOut, UserUpdate

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


@router.post("/login", response_model=TokenOut)
def login(db: DbSession, form: Annotated[OAuth2PasswordRequestForm, Depends()]):
    user = db.scalar(select(User).where(User.username == form.username))
    if user is None or not verify_password(form.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password")
    return TokenOut(access_token=create_access_token(user.username, user.role), role=user.role)


@router.get("/me", response_model=UserOut)
def me(db: DbSession, user: CurrentUser):
    return user_out(db, user)


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, db: DbSession, _: AdminUser):
    if db.scalar(select(User).where(User.username == body.username)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")
    user = User(username=body.username, password_hash=hash_password(body.password), role=body.role)
    db.add(user)
    db.flush()
    save_permissions(db, user, body.permissions if body.permissions is not None else list(default_permissions(body.role)))
    db.commit()
    return user_out(db, user)


@router.get("/users", response_model=list[UserOut])
def list_users(db: DbSession, _: AdminUser):
    return [user_out(db, user) for user in db.scalars(select(User).order_by(User.id)).all()]


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, db: DbSession, actor: AdminUser):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
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
    return user_out(db, user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, db: DbSession, actor: AdminUser):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.id == actor.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete your own account")
    if user.role == Role.ADMIN and db.scalar(select(func.count()).select_from(User).where(User.role == Role.ADMIN)) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "At least one Admin account is required")
    db.delete(user)
    db.commit()
