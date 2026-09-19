"""Level 2: login attempt history.

GET /api/auth/login-attempts   the logged-in user's latest attempts, newest first (default 3)

LoginOut is the login response with the last three attempts added; the login route in
auth.py returns it once the FastAPI owner wires it in (see docs/LEVEL2_TASKS.md).
Response models live here, not in app/schemas, so this feature touches no shared file.
"""

from datetime import datetime

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.schemas import ORMModel, TokenOut
from app.services.login_attempts import get_last_login_attempts

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginAttemptOut(ORMModel):
    success: bool
    ip_address: str | None
    attempted_at: datetime


class LoginOut(TokenOut):
    """Login response: the token, plus this user's last login attempts (newest first)."""

    login_attempts: list[LoginAttemptOut] = []


@router.get("/login-attempts", response_model=list[LoginAttemptOut])
def my_login_attempts(db: DbSession, user: CurrentUser, limit: int = Query(3, ge=1, le=50)):
    return get_last_login_attempts(db, user.username, limit)
