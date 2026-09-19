"""Search existing operational events and summarize one UTC day."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import Numeric, case, cast, func, select
from sqlalchemy.orm import Session

from app.models.event import Event


ZERO = Decimal("0")


def _utc_naive(value: datetime) -> datetime:
    """The events column stores UTC without a timezone marker."""
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def search_events(
    db: Session,
    *,
    types: list[str] | None = None,
    plate: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    exclude_raw_webhooks: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> list[Event]:
    """Return matching events newest first, using event time and id to break ties."""
    query = select(Event)
    if exclude_raw_webhooks:
        query = query.where(Event.event_type != "WEBHOOK")
    if types is not None:
        query = query.where(Event.event_type.in_(types))
    if plate is not None:
        query = query.where(Event.car_plate == plate)
    if since is not None:
        query = query.where(Event.event_time >= _utc_naive(since))
    if until is not None:
        query = query.where(Event.event_time <= _utc_naive(until))
    return db.scalars(query.order_by(Event.event_time.desc(), Event.id.desc()).limit(limit).offset(offset)).all()


def daily_summary(db: Session, day: date) -> dict:
    """Count event types and arrival hours with one grouped query over a UTC day."""
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)
    hour = func.hour(Event.event_time)

    # MySQL casts malformed JSON strings to 0, but validate first so they cannot
    # contribute a misleading amount. Non-penalty rows always contribute zero.
    fine_text = Event.raw_data["FineAmount"].as_string()
    numeric_fine = case(
        (fine_text.op("REGEXP")(r"^[+-]?[0-9]+(\.[0-9]+)?$"), cast(fine_text, Numeric(20, 2))),
        else_=ZERO,
    )
    penalty_fine = case((Event.event_type == "PENALTY", numeric_fine), else_=ZERO)
    rows = db.execute(
        select(Event.event_type, hour, func.count(Event.id), func.sum(penalty_fine))
        .where(Event.event_time >= start, Event.event_time < end)
        .group_by(Event.event_type, hour)
    ).all()

    counts: dict[str, int] = {}
    arrival_hours: dict[int, int] = {}
    penalty_total = ZERO
    for event_type, event_hour, count, fine in rows:
        counts[event_type] = counts.get(event_type, 0) + count
        if event_type == "CAR_ARRIVED":
            arrival_hours[event_hour] = count
        penalty_total += fine or ZERO

    busiest_hour = min(arrival_hours, key=lambda h: (-arrival_hours[h], h)) if arrival_hours else None
    return {
        "date": day,
        "cars_arrived": counts.get("CAR_ARRIVED", 0),
        "cars_parked": counts.get("CAR_PARKED", 0),
        "cars_departed": counts.get("CAR_DEPARTED", 0),
        "penalties": counts.get("PENALTY", 0),
        "penalty_total": penalty_total,
        "components_broken": counts.get("COMPONENT_BROKEN", 0),
        "components_fixed": counts.get("COMPONENT_FIXED", 0),
        "co_alerts": counts.get("CO_ALERT", 0),
        "busiest_hour": busiest_hour,
        "events_by_type": dict(sorted(counts.items())),
    }
