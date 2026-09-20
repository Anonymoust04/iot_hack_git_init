"""One accepted simulator charge for one parking visit."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.enums import CarType, db_enum


class PaymentRecord(Base):
    __tablename__ = "payment_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parking_session_id: Mapped[int] = mapped_column(ForeignKey("parking_sessions.id"), unique=True)
    car_plate: Mapped[str] = mapped_column(String(32))
    car_type: Mapped[CarType | None] = mapped_column(db_enum(CarType))
    parking_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    ev_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    paid_at: Mapped[datetime] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(32), default="SIMULATOR_CHARGE")
