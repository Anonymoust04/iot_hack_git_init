"""Pydantic request/response schemas (API contract for the frontend)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import Role


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


class UserOut(ORMModel):
    id: int
    username: str
    role: Role


# ---- park state ----

class SpotOut(ORMModel):
    name: str
    purpose: str
    car_type: str
    zone: str
    occupied_by: str | None
    reserved_for: str | None
    broken: bool
    under_maintenance: bool


class GateOut(ORMModel):
    name: str
    zone: str
    state: str
    broken: bool
    under_maintenance: bool


class ZoneSummary(BaseModel):
    zone: str
    total: int
    occupied: int
    free: int
    unavailable: int  # broken / under maintenance


class DashboardOut(BaseModel):
    zones: list[ZoneSummary]
    gates: list[GateOut]
    total_free: int
    park_full: bool


# ---- history ----

class ParkingSessionOut(ORMModel):
    id: int
    plate: str
    is_electric: bool
    spot_name: str | None
    status: str
    arrived_at: datetime
    parked_at: datetime | None
    exited_at: datetime | None
    minutes: int | None
    parking_cost: float | None
    charging_cost: float | None
