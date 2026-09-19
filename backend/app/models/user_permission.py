from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class UserPermission(Base):
    """One extra authority granted to a user (REPAIR, FINANCIAL_REPORT, ...). Admins have all of them
    implicitly; rows here matter for Operators. Removed automatically when the user is deleted."""

    __tablename__ = "user_permissions"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    permission: Mapped[str] = mapped_column(String(32), primary_key=True)
    granted_by: Mapped[str | None] = mapped_column(String(64))
    granted_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
