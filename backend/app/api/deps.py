from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.core.permissions import OPERATOR_DEFAULT_PERMISSIONS, Permission
from app.models import Role, User, UserPermission

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="api/auth/login",
    description="Dashboard account from BOOTSTRAP_ADMIN_USERNAME / BOOTSTRAP_ADMIN_PASSWORD. "
    "Enter these in Swagger's username and password fields; simulator tokens are separate.",
)

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(db: DbSession, token: Annotated[str, Depends(oauth2_scheme)]) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        raise unauthorized
    user = db.scalar(select(User).where(User.username == payload.get("sub")))
    if user is None:
        raise unauthorized
    return user


def require_roles(*roles: Role):
    """Usage: `user: User = Depends(require_roles(Role.ADMIN))`."""

    def checker(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user

    return checker


def require_permission(permission: Permission):
    """Require a persisted dashboard permission (admins retain full access)."""
    def checker(db: DbSession, user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role == Role.ADMIN:
            return user
        rows = db.scalars(select(UserPermission).where(UserPermission.user_id == user.id)).all()
        allowed = (
            permission in OPERATOR_DEFAULT_PERMISSIONS if not rows
            else any(row.permission == permission.value and row.enabled for row in rows)
        )
        if not allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Missing permission: {permission.value}")
        return user
    return checker


CurrentUser = Annotated[User, Depends(get_current_user)]
OperatorUser = Annotated[User, Depends(require_roles(Role.ADMIN, Role.OPERATOR))]
AdminUser = Annotated[User, Depends(require_roles(Role.ADMIN))]
GateControlUser = Annotated[User, Depends(require_permission(Permission.GATE_CONTROL))]
LightControlUser = Annotated[User, Depends(require_permission(Permission.LIGHT_CONTROL))]
FanControlUser = Annotated[User, Depends(require_permission(Permission.FAN_CONTROL))]
RepairUser = Annotated[User, Depends(require_permission(Permission.REPAIR))]
