"""Receives events pushed by the simulator.

Configure the simulator's webhook URL to: http://<this-host>:<port>/webhook
Every payload is stored in event_logs first, then dispatched to a handler.
"""

import logging

from fastapi import APIRouter, Request

from app.api.deps import DbSession
from app.models import EventLog
from app.services.parking import EVENT_HANDLERS
from app.services.simulator_client import get_simulator

log = logging.getLogger(__name__)
router = APIRouter(tags=["webhook"])


@router.post("/webhook")
async def receive_event(request: Request, db: DbSession):
    try:
        payload = await request.json()
    except ValueError:
        payload = {"raw": (await request.body()).decode(errors="replace")}

    # TODO(team): adjust these keys once the real payload shape is known
    event_type = str(payload.get("type") or payload.get("eventType") or "unknown")
    plate = payload.get("plate") or payload.get("carName")

    event = EventLog(event_type=event_type, plate=plate, payload=payload)
    db.add(event)
    db.commit()

    handler = EVENT_HANDLERS.get(event_type)
    if handler is None:
        log.warning("No handler for event %s: %s", event_type, payload)
        return {"ok": True, "handled": False}

    try:
        handler(db, get_simulator(), payload)
        event.processed = True
    except Exception as exc:  # keep accepting webhooks even if one handler fails
        log.exception("Handler failed for %s", event_type)
        event.error = str(exc)[:500]
    db.commit()
    return {"ok": True, "handled": event.processed}
