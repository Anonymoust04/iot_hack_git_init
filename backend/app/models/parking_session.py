from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, FetchedValue, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.enums import CarType, PaymentStatus, SessionStatus, db_enum
from app.models.parking_spot import ParkingSpot


class ParkingSession(Base):
    """One car visit: ENTERING -> PARKED -> EXITING -> COMPLETED. Never deleted."""

    __tablename__ = "parking_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    car_plate: Mapped[str] = mapped_column(String(32))
    car_type: Mapped[CarType | None] = mapped_column(db_enum(CarType))
    parking_spot_id: Mapped[int | None] = mapped_column(ForeignKey("parking_spots.id"))
    entry_time: Mapped[datetime] = mapped_column(DateTime)
    parked_time: Mapped[datetime | None] = mapped_column(DateTime)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime)
    parking_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    charging_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    payment_status: Mapped[PaymentStatus] = mapped_column(db_enum(PaymentStatus), default=PaymentStatus.PENDING)
    status: Mapped[SessionStatus] = mapped_column(db_enum(SessionStatus), default=SessionStatus.ENTERING)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), server_onupdate=FetchedValue()
    )

    parking_spot: Mapped[ParkingSpot | None] = relationship()
