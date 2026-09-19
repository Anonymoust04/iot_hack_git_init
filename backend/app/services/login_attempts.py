"""Level 2: record every dashboard login attempt and show a user their last three.

    record_login_attempt(db, username, success, ip_address)   after checking the password
    get_last_login_attempts(db, username, limit=3)             newest first

Each record is its own short transaction (INSERT only), so simultaneous logins never block
or overwrite each other. Passwords, hashes and tokens are never stored.
"""

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.login_attempt import LoginAttempt

USERNAME_MAX = 64  # login_attempts.username VARCHAR(64); a longer typed name is cut, not rejected
IP_MAX = 45        # longest IPv6 text form


def client_ip(request: Request) -> str | None:
    """The caller's address as FastAPI sees it (no proxy-header handling)."""
    return request.client.host if request.client else None


def record_login_attempt(db: Session, username: str, success: bool, ip_address: str | None = None) -> LoginAttempt:
    try:
        attempt = LoginAttempt(
            username=(username or "")[:USERNAME_MAX],
            success=success,
            ip_address=ip_address[:IP_MAX] if ip_address else None,
        )
        db.add(attempt)
        db.commit()
        return attempt
    except Exception:
        db.rollback()
        raise


def get_last_login_attempts(db: Session, username: str, limit: int = 3) -> list[LoginAttempt]:
    """Newest first. Ties within the same second are broken by insert order (id)."""
    return list(db.scalars(
        select(LoginAttempt)
        .where(LoginAttempt.username == username[:USERNAME_MAX])
        .order_by(LoginAttempt.attempted_at.desc(), LoginAttempt.id.desc())
        .limit(limit)
    ).all())
