from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Event(Base):
    """Audit log / activity feed. raw_data keeps the simulator payload untouched."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64))
    car_plate: Mapped[str | None] = mapped_column(String(32))
    parking_spot: Mapped[str | None] = mapped_column(String(32))  # spot NAME
    gate_name: Mapped[str | None] = mapped_column(String(32))
    event_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    raw_data: Mapped[dict | list | None] = mapped_column(JSON)
    event_id: Mapped[str | None] = mapped_column(String(64), unique=True)  # simulator EventId
    sequence_id: Mapped[int | None] = mapped_column(BigInteger)          # simulator SequenceId
