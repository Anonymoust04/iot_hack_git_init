"""Authenticated event search and UTC daily report endpoints."""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from app.api.deps import CurrentUser, DbSession
from app.api.deps import require_permission
from app.core.permissions import Permission
from app.models import User
from app.services.event_log import daily_summary, financial_summary, search_events


router = APIRouter(prefix="/api/logs", tags=["logs"])


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    car_plate: str | None
    parking_spot: str | None
    gate_name: str | None
    event_time: datetime
    raw_data: dict[str, Any] | list[Any] | None
    event_id: str | None
    sequence_id: int | None


class DailySummaryOut(BaseModel):
    date: date
    cars_arrived: int
    cars_parked: int
    cars_departed: int
    penalties: int
    penalty_total: Decimal
    components_broken: int
    components_fixed: int
    co_alerts: int
    busiest_hour: int | None
    peak_occupancy: int
    events_by_type: dict[str, int]


class FinancialSummaryOut(BaseModel):
    date: date
    parking_revenue: Decimal
    ev_charging_revenue: Decimal
    penalty_cost: Decimal
    total_revenue: Decimal
    parking_transactions: int
    charging_transactions: int
    penalty_transactions: int
    breakdown: list[dict]


FinancialReportUser = Annotated[
    User,
    Depends(require_permission(Permission.FINANCIAL_REPORTS)),
]


@router.get("/events", response_model=list[EventOut])
def events(
    db: DbSession,
    _: CurrentUser,
    types: Annotated[list[str] | None, Query()] = None,
    plate: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    exclude_raw_webhooks: bool = True,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return search_events(
        db, types=types, plate=plate, since=since, until=until,
        exclude_raw_webhooks=exclude_raw_webhooks, limit=limit, offset=offset,
    )


@router.get("/daily-summary", response_model=DailySummaryOut)
def summary(db: DbSession, _: CurrentUser, day: date | None = None):
    return daily_summary(db, day or datetime.now(timezone.utc).date())


@router.get("/financial-summary", response_model=FinancialSummaryOut)
def financial(
    db: DbSession,
    _: FinancialReportUser,
    day: date | None = None,
):
    """Financial data is database-backed and restricted to financial-report permission."""
    return financial_summary(db, day or datetime.now(timezone.utc).date())
