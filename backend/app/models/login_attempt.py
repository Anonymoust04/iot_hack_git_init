from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class LoginAttempt(Base):
    """One dashboard login attempt, successful or not. Never holds passwords or tokens.
    `username` is what was typed, so failed attempts may name users that don't exist."""

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(64))
    success: Mapped[bool] = mapped_column(Boolean)
    ip_address: Mapped[str | None] = mapped_column(String(45))  # IPv4 or IPv6
    attempted_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
