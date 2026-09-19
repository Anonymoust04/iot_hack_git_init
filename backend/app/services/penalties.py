"""Read simulator penalties from the existing events table."""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event import Event


ZERO = Decimal("0")


def _payload(event: Event) -> dict:
    return event.raw_data if isinstance(event.raw_data, dict) else {}


def _fine_amount(payload: dict) -> Decimal | None:
    if "FineAmount" not in payload or payload["FineAmount"] is None:
        return None
    try:
        amount = Decimal(str(payload["FineAmount"]))
    except (InvalidOperation, TypeError, ValueError):
        return ZERO
    return amount if amount.is_finite() else ZERO


def _base_query(since: datetime | None, until: datetime | None):
    query = select(Event).where(Event.event_type == "PENALTY")
    if since is not None:
        query = query.where(Event.event_time >= since)
    if until is not None:
        query = query.where(Event.event_time <= until)
    return query


def list_penalties(
    db: Session,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Return the newest penalties; component type is filtered before pagination."""
    query = _base_query(since, until)
    if type is not None:
        query = query.where(Event.raw_data["Type"].as_string() == type)
    events = db.scalars(
        query.order_by(Event.event_time.desc(), Event.id.desc()).limit(limit).offset(offset)
    ).all()
    return [
        {
            "id": event.id,
            "received_at": event.event_time,
            "reason": (payload := _payload(event)).get("Reason"),
            "fine_amount": _fine_amount(payload),
            "type": payload.get("Type"),
            "component": payload.get("ComponentName"),
            "car_plate": payload.get("CarPlateNumber"),
            "event_id": payload.get("EventId"),
        }
        for event in events
    ]


def penalty_summary(
    db: Session,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict:
    """Group penalty counts and fines by component type and reason."""
    totals_by_type: dict[str | None, dict] = defaultdict(lambda: {"count": 0, "total_fine": ZERO})
    counts_by_reason: dict[str | None, int] = defaultdict(int)
    count = 0
    total_fine = ZERO

    for event in db.scalars(_base_query(since, until)).yield_per(500):
        payload = _payload(event)
        fine = _fine_amount(payload) or ZERO
        component_type = payload.get("Type")
        reason = payload.get("Reason")
        count += 1
        total_fine += fine
        totals_by_type[component_type]["count"] += 1
        totals_by_type[component_type]["total_fine"] += fine
        counts_by_reason[reason] += 1

    by_type = [
        {"type": name, **totals_by_type[name]}
        for name in sorted(totals_by_type, key=lambda name: (-totals_by_type[name]["count"], name or ""))
    ]
    top_reasons = [
        {"reason": reason, "count": counts_by_reason[reason]}
        for reason in sorted(counts_by_reason, key=lambda reason: (-counts_by_reason[reason], reason or ""))
    ]
    return {"count": count, "total_fine": total_fine, "by_type": by_type, "top_reasons": top_reasons}
