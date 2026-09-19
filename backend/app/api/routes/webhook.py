"""Receives events pushed by the simulator.

Configure the simulator's webhook URL to: http://<this-host>:<port>/webhook

For every call, in this order:
  1. Signature check. Bad signature -> stored as WEBHOOK_BAD_SIGNATURE, NOT processed
     (this is how fake payments are caught).
  2. Dedup on EventId: the raw payload is stored as a WEBHOOK row whose event_id is UNIQUE,
     so a repeated EventId fails to insert and is ignored, even if both copies arrive at once.
  3. SequenceId check: a gap or an older number is logged (SEQUENCE_GAP / SEQUENCE_OUT_OF_ORDER).
     The event is still processed, since the missing ones can't be fetched again.
  4. EVENT_HANDLERS[EventClass].
The raw payload is committed BEFORE processing, so nothing is lost if a handler fails.
"""

import json
import logging

from fastapi import APIRouter, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession
from app.config import get_settings
from app.models import Event
from app.services.parking import log_event
from app.services.simulator_client import get_simulator
from app.services.webhook_handlers import EVENT_HANDLERS, parse_event, signature_is_valid

log = logging.getLogger(__name__)
router = APIRouter(tags=["webhook"])


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@router.post("/webhook")
async def receive_event(request: Request, db: DbSession):
    body = await request.body()
    try:
        payload = json.loads(body)
        # same JSON, numbers kept as their original text: the signature is computed over that text
        sig_fields = json.loads(body, parse_float=str, parse_int=str)
    except ValueError:
        payload = sig_fields = {"raw": body.decode(errors="replace")}
    if not isinstance(payload, dict):
        payload = sig_fields = {"raw": payload}

    event_type, plate = parse_event(payload)

    # 1. integrity
    if get_settings().webhook_verify_signature and not signature_is_valid(sig_fields):
        log.warning("Bad webhook signature: %s", payload)
        # no event_id here: a forged copy must not block the real event with the same EventId
        log_event(db, "WEBHOOK_BAD_SIGNATURE", car_plate=plate, raw_data=payload)
        db.commit()
        return {"ok": False, "handled": False, "reason": "bad signature"}

    # 3. ordering (read before inserting this one)
    event_id = payload.get("EventId")
    sequence_id = _as_int(payload.get("SequenceId"))
    last_seq = db.scalar(select(func.max(Event.sequence_id))) if sequence_id is not None else None

    # 2. dedup + store raw
    log_event(db, "WEBHOOK", car_plate=plate, raw_data=payload,
              event_id=str(event_id) if event_id else None, sequence_id=sequence_id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        log.info("Duplicate webhook %s ignored", event_id)
        return {"ok": True, "handled": False, "duplicate": True}

    if last_seq is not None and sequence_id != last_seq + 1:
        kind = "SEQUENCE_GAP" if sequence_id > last_seq else "SEQUENCE_OUT_OF_ORDER"
        log.warning("%s: last %s, got %s", kind, last_seq, sequence_id)
        log_event(db, kind, car_plate=plate, raw_data={"last": last_seq, "got": sequence_id, "event_id": event_id})
        db.commit()

    # 4. process
    handler = EVENT_HANDLERS.get(event_type)
    if handler is None:
        return {"ok": True, "handled": False}
    try:
        handler(db, get_simulator(), payload)
    except Exception as exc:  # keep accepting webhooks even if one handler fails
        log.exception("Handler failed for %s", event_type)
        db.rollback()
        log_event(db, "HANDLER_ERROR", car_plate=plate, raw_data={"error": str(exc)[:500], "payload": payload})
        db.commit()
        return {"ok": True, "handled": False}
    return {"ok": True, "handled": True}
