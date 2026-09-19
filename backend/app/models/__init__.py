"""SQLAlchemy models — one file per table. Structure is defined by database/schema.sql;
these classes must match it (tests/test_schema_matches_models.py checks)."""

from app.db.session import utcnow
from app.models.enums import (
    CarType,
    GateState,
    PaymentStatus,
    Role,
    SessionStatus,
    SpotPurpose,
    SpotStatus,
)
from app.models.event import Event
from app.models.gate import Gate
from app.models.parking_session import ParkingSession
from app.models.parking_spot import ParkingSpot
from app.models.user import User
from app.models.user_permission import UserPermission

__all__ = [
    "CarType", "Event", "Gate", "GateState", "ParkingSession", "ParkingSpot", "PaymentStatus",
    "Role", "SessionStatus", "SpotPurpose", "SpotStatus", "User", "UserPermission", "utcnow",
]
