from datetime import datetime

from sqlalchemy import Boolean, DateTime, FetchedValue, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.enums import CarType, SpotPurpose, SpotStatus, db_enum


class ParkingSpot(Base):
    """Latest known state of one simulator spot. Upserted by services/sync.py."""

    __tablename__ = "parking_spots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)
    zone: Mapped[str] = mapped_column(String(32), default="")
    purpose: Mapped[SpotPurpose] = mapped_column(db_enum(SpotPurpose))
    car_type: Mapped[CarType] = mapped_column(db_enum(CarType), default=CarType.ANY)
    status: Mapped[SpotStatus] = mapped_column(db_enum(SpotStatus), default=SpotStatus.FREE)
    current_car: Mapped[str | None] = mapped_column(String(32))
    broken: Mapped[bool] = mapped_column(Boolean, default=False)
    under_maintenance: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), server_onupdate=FetchedValue()
    )
