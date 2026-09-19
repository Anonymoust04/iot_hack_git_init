"""Pydantic request/response schemas (API contract for the frontend)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import Role
from app.core.permissions import Permission


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- auth / users ----

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: Role


class UserCreate(BaseModel):
    username: str
    password: str
    role: Role = Role.OPERATOR
    permissions: list[Permission] | None = None


class UserUpdate(BaseModel):
    username: str | None = None
    password: str | None = None
    role: Role | None = None
    permissions: list[Permission] | None = None


class UserOut(ORMModel):
    id: int
    username: str
    role: Role
    permissions: list[Permission]


# ---- park state ----

class SpotOut(ORMModel):
    name: str
    zone: str
    purpose: str
    car_type: str
    status: str          # FREE | RESERVED | OCCUPIED | BROKEN | MAINTENANCE
    current_car: str | None
    broken: bool
    under_maintenance: bool


class GateOut(ORMModel):
    name: str
    zone: str
    state: str           # Open | Closed | Opening | Closing
    broken: bool
    under_maintenance: bool
    role: str | None = None  # "entrance" / "exit" (from ENTRY_GATE / EXIT_GATE), else None


class ZoneSummary(BaseModel):
    zone: str
    total: int
    free: int
    reserved: int
    occupied: int
    unavailable: int  # BROKEN / MAINTENANCE


class DashboardOut(BaseModel):
    zones: list[ZoneSummary]
    gates: list[GateOut]
    total_free: int
    park_full: bool
    cars_inside: int  # visits not COMPLETED: entering, parked or on the way out


# ---- history ----

class ParkingSessionOut(ORMModel):
    id: int
    car_plate: str
    car_type: str | None
    spot_name: str | None = None  # filled from the joined parking_spots row
    status: str
    payment_status: str
    entry_time: datetime
    parked_time: datetime | None
    exit_time: datetime | None
    parking_cost: float | None
    charging_cost: float | None


class EventOut(ORMModel):
    id: int
    event_type: str
    car_plate: str | None
    parking_spot: str | None
    gate_name: str | None
    event_time: datetime
