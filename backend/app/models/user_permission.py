from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class UserPermission(Base):
    """Per-user authority, including RBAC decisions and grant metadata."""

    __tablename__ = "user_permissions"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    permission: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    granted_by: Mapped[str | None] = mapped_column(String(64))
    granted_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
