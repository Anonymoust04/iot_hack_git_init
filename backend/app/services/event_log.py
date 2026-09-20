"""Search existing operational events and summarize one UTC day."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import Numeric, case, cast, func, or_, select
from sqlalchemy.orm import Session

from app.models import ParkingSession, PaymentStatus
from app.models.event import Event
from app.services.penalties import penalty_summary


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


def peak_occupancy(db: Session, day: date) -> int:
    """Most cars parked at the same moment during the UTC day, from the visits themselves:
    +1 when a car parks, -1 when it leaves (a visit still open counts until the end of the day)."""
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)
    rows = db.execute(
        select(ParkingSession.parked_time, ParkingSession.exit_time)
        .where(ParkingSession.parked_time.is_not(None), ParkingSession.parked_time < end)
        .where(or_(ParkingSession.exit_time.is_(None), ParkingSession.exit_time >= start))
    ).all()
    moments: list[tuple[datetime, int]] = []
    for parked_at, left_at in rows:
        moments.append((max(parked_at, start), 1))
        if left_at is not None and left_at < end:
            moments.append((max(left_at, start), -1))
    peak = parked = 0
    for _, change in sorted(moments, key=lambda m: (m[0], m[1])):
        parked += change
        peak = max(peak, parked)
    return peak


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
        "peak_occupancy": peak_occupancy(db, day),
        "events_by_type": dict(sorted(counts.items())),
    }


def financial_summary(db: Session, day: date) -> dict:
    """Summarize paid database transactions and penalties for one UTC day."""
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)
    parking_revenue, charging_revenue, parking_transactions, charging_transactions = db.execute(
        select(
            func.coalesce(func.sum(ParkingSession.parking_cost), ZERO),
            func.coalesce(func.sum(ParkingSession.charging_cost), ZERO),
            func.count(ParkingSession.id),
            func.sum(case((ParkingSession.charging_cost > ZERO, 1), else_=0)),
        ).where(
            ParkingSession.payment_status == PaymentStatus.PAID,
            ParkingSession.parking_cost.is_not(None),
            ParkingSession.exit_time >= start,
            ParkingSession.exit_time < end,
        )
    ).one()
    penalties = penalty_summary(db, since=start, until=end - timedelta(microseconds=1))
    parking_revenue = parking_revenue or ZERO
    charging_revenue = charging_revenue or ZERO
    penalty_total = penalties["total_fine"] or ZERO
    return {
        "date": day,
        "parking_revenue": parking_revenue,
        "ev_charging_revenue": charging_revenue,
        "penalty_cost": penalty_total,
        "total_revenue": parking_revenue + charging_revenue,
        "parking_transactions": parking_transactions,
        "charging_transactions": charging_transactions or 0,
        "penalty_transactions": penalties["count"],
        "breakdown": [
            {"category": "Parking", "transactions": parking_transactions, "amount": parking_revenue},
            {"category": "EV charging", "transactions": charging_transactions or 0, "amount": charging_revenue},
            {"category": "Penalties", "transactions": penalties["count"], "amount": penalty_total},
        ],
    }
