"""Searchable history of arrivals / parking / departures / charges."""

from datetime import datetime

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models import ParkingSession
from app.schemas import ParkingSessionOut

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("/sessions", response_model=list[ParkingSessionOut])
def search_sessions(
    db: DbSession,
    _: CurrentUser,
    plate: str | None = None,
    status: str | None = None,
    spot: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(50, le=500),
    offset: int = 0,
):
    q = select(ParkingSession)
    if plate:
        q = q.where(ParkingSession.plate.ilike(f"%{plate}%"))
    if status:
        q = q.where(ParkingSession.status == status)
    if spot:
        q = q.where(ParkingSession.spot_name == spot)
    if since:
        q = q.where(ParkingSession.arrived_at >= since)
    if until:
        q = q.where(ParkingSession.arrived_at <= until)
    q = q.order_by(ParkingSession.arrived_at.desc()).limit(limit).offset(offset)
    return db.scalars(q).all()
