from datetime import datetime

from sqlalchemy import Boolean, DateTime, FetchedValue, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.enums import GateState, db_enum


class Gate(Base):
    """Latest known state of one barrier gate. Upserted by services/sync.py."""

    __tablename__ = "gates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)
    zone: Mapped[str] = mapped_column(String(32), default="")
    state: Mapped[GateState] = mapped_column(db_enum(GateState), default=GateState.CLOSED)
    broken: Mapped[bool] = mapped_column(Boolean, default=False)
    under_maintenance: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), server_onupdate=FetchedValue()
    )
