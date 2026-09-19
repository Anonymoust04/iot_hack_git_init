"""Searchable history of parking sessions and events."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models import Event, ParkingSession, ParkingSpot
from app.schemas import EventOut, ParkingSessionOut

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("/sessions", response_model=list[ParkingSessionOut])
def search_sessions(
    db: DbSession,
    _: CurrentUser,
    plate: str | None = None,
    status: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(50, le=500),
    offset: int = 0,
):
    q = (select(ParkingSession, ParkingSpot.name)
         .outerjoin(ParkingSpot, ParkingSession.parking_spot_id == ParkingSpot.id))
    if plate:
        q = q.where(ParkingSession.car_plate.like(f"{plate}%"))  # prefix match can use the index
    if status:
        q = q.where(ParkingSession.status == status)
    if since:
        q = q.where(ParkingSession.entry_time >= since)
    if until:
        q = q.where(ParkingSession.entry_time <= until)
    q = q.order_by(ParkingSession.id.desc()).limit(limit).offset(offset)
    return [
        ParkingSessionOut.model_validate(s).model_copy(update={"spot_name": spot_name})
        for s, spot_name in db.execute(q).all()
    ]


@router.get("/events", response_model=list[EventOut])
def search_events(
    db: DbSession,
    _: CurrentUser,
    plate: str | None = None,
    event_type: Annotated[list[str] | None, Query()] = None,  # repeat to match several: ?event_type=A&event_type=B
    limit: int = Query(20, le=500),
):
    q = select(Event)
    if plate:
        q = q.where(Event.car_plate == plate)
    if event_type:
        q = q.where(Event.event_type.in_(event_type))
    return db.scalars(q.order_by(Event.id.desc()).limit(limit)).all()
