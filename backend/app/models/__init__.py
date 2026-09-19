"""SQLAlchemy ORM models. Import everything here so Base.metadata sees all tables."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Role(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"


class SessionStatus(StrEnum):
    ARRIVED = "arrived"      # detected at entry spot
    ASSIGNED = "assigned"    # told to go to a spot
    PARKED = "parked"        # detected in its spot
    EXITING = "exiting"      # sent to exit
    CHARGED = "charged"      # payment requested at exit spot
    LEFT = "left"            # gone / leavepark


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16), default=Role.OPERATOR)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---- Current-state tables (mirror of simulator, updated from webhooks) ----

class ParkingSpot(Base):
    __tablename__ = "parking_spots"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(16))              # Park | EntrySpot | ExitSpot
    car_type: Mapped[str] = mapped_column(String(16))             # Electric | Accessible | Any
    zone: Mapped[str] = mapped_column(String(32), index=True, default="")
    occupied_by: Mapped[str | None] = mapped_column(String(32), nullable=True)  # plate
    reserved_for: Mapped[str | None] = mapped_column(String(32), nullable=True)  # plate en route
    broken: Mapped[bool] = mapped_column(Boolean, default=False)
    under_maintenance: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Gate(Base):
    __tablename__ = "gates"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    zone: Mapped[str] = mapped_column(String(32), default="")
    state: Mapped[str] = mapped_column(String(16), default="Closed")  # Open | Closed | Opening | Closing
    broken: Mapped[bool] = mapped_column(Boolean, default=False)
    under_maintenance: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Zone(Base):
    __tablename__ = "zones"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    co_level: Mapped[float] = mapped_column(Float, default=0)
    risk: Mapped[str] = mapped_column(String(16), default="Safe")


# ---- History tables (searchable on dashboard) ----

class ParkingSession(Base):
    """One visit of one car: arrival -> park -> exit -> charge."""

    __tablename__ = "parking_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plate: Mapped[str] = mapped_column(String(32), index=True)
    is_electric: Mapped[bool] = mapped_column(Boolean, default=False)
    car_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    spot_name: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default=SessionStatus.ARRIVED, index=True)
    arrived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    parked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parking_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    charging_cost: Mapped[float | None] = mapped_column(Float, nullable=True)


class EventLog(Base):
    """Raw webhook payloads, stored before processing (useful for debugging/replay)."""

    __tablename__ = "event_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    plate: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
