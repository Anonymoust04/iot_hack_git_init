"""Level 2: user management for the Admin page (Jackson's UI) + "what may I do" for every page.

Admin only:
    GET    /api/admin/permissions          the grantable authorities (for the checkboxes)
    GET    /api/admin/users                users with role + effective authorities
    POST   /api/admin/users                create  {username, password, role, permissions}
    PATCH  /api/admin/users/{user_id}      edit    {role?, permissions?, password?}  (only fields sent change)
    DELETE /api/admin/users/{user_id}      remove
Any logged-in user:
    GET    /api/auth/me/permissions        {username, role, permissions}  -> show/hide buttons

Response models live here, not in app/schemas, so this feature touches no shared file.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.models import Role
from app.services import user_admin
from app.services.login_attempts import client_ip

router = APIRouter(tags=["admin: users"])


class PermissionOut(BaseModel):
    name: str
    description: str


class UserAdminOut(BaseModel):
    id: int
    username: str
    role: Role
    permissions: list[str]   # effective: an ADMIN has all of them
    created_at: datetime


class MyPermissionsOut(BaseModel):
    username: str
    role: Role
    permissions: list[str]


class UserCreateIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=4, max_length=128)
    role: Role = Role.OPERATOR
    permissions: list[str] = []


class UserUpdateIn(BaseModel):
    role: Role | None = None
    permissions: list[str] | None = None                       # replaces the whole list
    password: str | None = Field(default=None, min_length=4, max_length=128)


def _out(item: user_admin.UserWithPermissions) -> UserAdminOut:
    u = item.user
    return UserAdminOut(id=u.id, username=u.username, role=u.role, permissions=item.permissions,
                        created_at=u.created_at)


def _refused(exc: user_admin.UserAdminError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


@router.get("/api/admin/permissions", response_model=list[PermissionOut])
def permissions(_: AdminUser):
    return [PermissionOut(name=n, description=d) for n, d in user_admin.PERMISSIONS.items()]


@router.get("/api/admin/users", response_model=list[UserAdminOut])
def users(db: DbSession, _: AdminUser):
    return [_out(item) for item in user_admin.list_users(db)]


@router.post("/api/admin/users", response_model=UserAdminOut, status_code=status.HTTP_201_CREATED)
def create(body: UserCreateIn, request: Request, db: DbSession, admin: AdminUser):
    try:
        return _out(user_admin.create_user(db, body.username, body.password, body.role, body.permissions,
                                           actor=admin.username, ip_address=client_ip(request)))
    except user_admin.UserAdminError as exc:
        raise _refused(exc)


@router.patch("/api/admin/users/{user_id}", response_model=UserAdminOut)
def update(user_id: int, body: UserUpdateIn, request: Request, db: DbSession, admin: AdminUser):
    try:
        return _out(user_admin.update_user(db, user_id, role=body.role, permissions=body.permissions,
                                           password=body.password, actor=admin.username,
                                           ip_address=client_ip(request)))
    except user_admin.UserAdminError as exc:
        raise _refused(exc)


@router.delete("/api/admin/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove(user_id: int, request: Request, db: DbSession, admin: AdminUser):
    try:
        user_admin.delete_user(db, user_id, actor=admin.username, ip_address=client_ip(request))
    except user_admin.UserAdminError as exc:
        raise _refused(exc)


@router.get("/api/auth/me/permissions", response_model=MyPermissionsOut)
def my_permissions(db: DbSession, user: CurrentUser):
    return MyPermissionsOut(username=user.username, role=user.role,
                            permissions=user_admin.effective_permissions(db, user))
