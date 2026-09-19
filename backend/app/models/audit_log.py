from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, String, func, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class AuditLog(Base):
    """Who did what to which component, and whether it worked. actor None = the system (automation)."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_name: Mapped[str | None] = mapped_column(String(64))
    success: Mapped[bool] = mapped_column(Boolean, server_default=true())
    details: Mapped[dict | list | None] = mapped_column(JSON)
    ip_address: Mapped[str | None] = mapped_column(String(45))  # IPv4 or IPv6
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
