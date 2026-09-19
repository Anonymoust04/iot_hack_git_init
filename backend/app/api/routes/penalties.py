"""Authenticated penalty list and summary for the operations page."""

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.deps import CurrentUser, DbSession
from app.services.penalties import list_penalties, penalty_summary


router = APIRouter(prefix="/api/penalties", tags=["penalties"])


class PenaltyOut(BaseModel):
    id: int
    received_at: datetime
    reason: str | None
    fine_amount: Decimal | None
    type: str | None
    component: str | None
    car_plate: str | None
    event_id: str | None


class TypeTotalOut(BaseModel):
    type: str | None
    count: int
    total_fine: Decimal


class ReasonCountOut(BaseModel):
    reason: str | None
    count: int


class PenaltySummaryOut(BaseModel):
    count: int
    total_fine: Decimal
    by_type: list[TypeTotalOut]
    top_reasons: list[ReasonCountOut]


@router.get("", response_model=list[PenaltyOut])
def penalties(
    db: DbSession,
    _: CurrentUser,
    since: datetime | None = None,
    until: datetime | None = None,
    type: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return list_penalties(db, since=since, until=until, type=type, limit=limit, offset=offset)


@router.get("/summary", response_model=PenaltySummaryOut)
def summary(db: DbSession, _: CurrentUser, since: datetime | None = None, until: datetime | None = None):
    return penalty_summary(db, since=since, until=until)
