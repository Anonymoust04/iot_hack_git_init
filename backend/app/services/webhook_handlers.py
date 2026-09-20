"""Simulator webhook -> database. Used by fastapi_project/db_hook.py (called from POST /webhook).

Every webhook has: EventClass, EventId (unique, dedup), SequenceId (+1 per call, gap/order
detection), Signature (MD5, integrity), ServerDateTime (the simulator's clock).

    receive()  check signature, dedup on EventId, check SequenceId, store the raw payload
    process()  update the tables (car parked / left, gates, penalties...). Sending cars and
               opening gates is done by main.py's own queues, not here.

Functions here are sync and take a Session; db_hook.py runs them in a worker thread.
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CarType, Event, GateState
from app.services import components, parking
from app.services.audit import record_audit
from app.services.parking import log_event

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signature / parsing
# ---------------------------------------------------------------------------

def _sig_text(value) -> str:
    # Numbers arrive as their original JSON text (see receive()), so 63.564693 stays "63.564693".
    # TODO(confirm): how the server writes booleans and nulls; none appear in the documented payloads.
    if value is None:
        return ""
    return str(value)


def compute_signature(fields: dict) -> str:
    """MD5 of all values except Signature, ordered by field name, joined with '|'."""
    text = "|".join(_sig_text(fields[k]) for k in sorted(fields) if k != "Signature")
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def signature_is_valid(fields: dict) -> bool:
    received = fields.get("Signature")
    if not isinstance(received, str):
        return False
    return hmac.compare_digest(compute_signature(fields), received.lower())


def server_time(payload: dict) -> datetime | None:
    try:
        return datetime.strptime(payload["ServerDateTime"], "%Y-%m-%d %H:%M:%S")
    except (KeyError, TypeError, ValueError):
        return None


def car_type(payload: dict) -> CarType | None:
    """Webhook CarType -> our spot type. The simulator calls ordinary cars "Normal"."""
    raw = str(payload.get("CarType") or "")
    if raw == "Normal":
        return CarType.ANY
    if raw.upper() == "EV" or "ELECTRIC" in raw.upper():
        return CarType.ELECTRIC
    try:
        return CarType(raw)
    except ValueError:
        return None


def _is_spot(payload: dict, spot_type: str, prefix: str) -> bool:
    # same test as Tee's original router: SpotType, or the spot name (ENTRY1 / EXIT)
    name = str(payload.get("SpotName") or "").upper()
    return payload.get("SpotType") == spot_type or name.startswith(prefix)


# ---------------------------------------------------------------------------
# Step 1: receive (verify, dedup, order, store raw)
# ---------------------------------------------------------------------------

def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def receive(db: Session, body: bytes) -> tuple[dict, str | None]:
    """Returns (payload, None) if the webhook should be processed,
    or (payload, reason) if not: "bad signature" or "duplicate".
    The raw payload is committed BEFORE processing, so nothing is lost if a handler fails."""
    try:
        payload = json.loads(body)
        # same JSON, numbers kept as their original text: the signature is computed over that text
        sig_fields = json.loads(body, parse_float=str, parse_int=str)
    except ValueError:
        payload = sig_fields = {"raw": body.decode(errors="replace")}
    if not isinstance(payload, dict):
        payload = sig_fields = {"raw": payload}
    plate = payload.get("CarPlateNumber")

    # Integrity: both missing and invalid signatures are rejected when verification is enabled.
    settings = get_settings()
    unsigned = sig_fields.get("Signature") in (None, "")
    check = settings.webhook_verify_signature and (settings.webhook_require_signature or not unsigned)
    if check and (unsigned or not signature_is_valid(sig_fields)):
        reason = "unsigned" if unsigned else "bad signature"
        log.warning("Rejected %s webhook: %s", reason, payload)
        # no event_id here: a forged copy must not block the real event with the same EventId
        log_event(db, "WEBHOOK_UNSIGNED" if unsigned else "WEBHOOK_BAD_SIGNATURE",
                  car_plate=plate, raw_data=payload)
        db.commit()
        return payload, reason

    event_id = payload.get("EventId")
    sequence_id = _as_int(payload.get("SequenceId"))
    last_seq = db.scalar(select(func.max(Event.sequence_id))) if sequence_id is not None else None

    # dedup: event_id is UNIQUE, so a repeat fails to insert even if both copies arrive at once
    log_event(db, "WEBHOOK", car_plate=plate, raw_data=payload,
              event_id=str(event_id) if event_id else None, sequence_id=sequence_id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        log.info("Duplicate webhook %s ignored", event_id)
        return payload, "duplicate"

    # order: log gaps / older numbers, still process (missed events can't be fetched again)
    if last_seq is not None and sequence_id != last_seq + 1:
        kind = "SEQUENCE_GAP" if sequence_id > last_seq else "SEQUENCE_OUT_OF_ORDER"
        log.warning("%s: last %s, got %s", kind, last_seq, sequence_id)
        log_event(db, kind, car_plate=plate, raw_data={"last": last_seq, "got": sequence_id, "event_id": event_id})
        db.commit()
    return payload, None


# ---------------------------------------------------------------------------
# Step 2: database handlers (car at ENTRY / EXIT is handled by main.py's queues)
# ---------------------------------------------------------------------------

def on_car_spot_action(db: Session, payload: dict) -> None:
    plate = payload["CarPlateNumber"]
    spot_type, direction = payload.get("SpotType"), payload.get("Direction")
    if _is_spot(payload, "EntrySpot", "ENTRY") and direction == "CarIn":
        parking.record_arrival(db, plate, car_type(payload), raw_data=payload, at=server_time(payload))
    elif _is_spot(payload, "EntrySpot", "ENTRY") and direction == "CarOut":
        # drove through the entrance: now inside (before this it was queuing outside)
        log_event(db, "CAR_ENTERED", car_plate=plate, raw_data=payload)
        db.commit()
    elif spot_type == "Park" and direction == "CarIn":
        parking.mark_parked(db, plate, payload["SpotName"], raw_data=payload, at=server_time(payload))
    elif spot_type == "Park" and direction == "CarOut":
        parking.mark_leaving_spot(db, plate, raw_data=payload)  # frees the spot for the next car now
    elif (_is_spot(payload, "ExitSpot", "EXIT") and direction == "CarOut") or spot_type == "LeaveParking":
        # left through the exit, or sent away ('leavepark' when full) without parking
        parking.complete_departure(db, plate, raw_data=payload, at=server_time(payload))


def on_payment_made(db: Session, payload: dict) -> bool:
    """Returns True if the payment is valid (matches what we charged, first payment)."""
    plate = payload["CarPlateNumber"]
    try:
        amount = Decimal(str(payload["Amount"]))
    except (KeyError, InvalidOperation):
        log_event(db, "PAYMENT_REJECTED", car_plate=plate,
                  raw_data={"reason": "missing or invalid Amount", "payload": payload})
        db.commit()
        return False
    rejected = parking.record_payment(db, plate, amount, raw_data=payload)
    if rejected:
        log.warning("Payment from %s rejected: %s", plate, rejected)
    return rejected is None


def on_gate_action(db: Session, payload: dict) -> None:
    components.set_gate_state(db, payload["Name"], GateState(payload["Action"]))


COMPONENT_TARGETS = {"BarrierGate": "gate", "ParkingSpot": "spot", "ExhaustFan": "fan", "Light": "light"}


def _audit_component(db: Session, payload: dict, broken: bool) -> None:
    """System event in the audit log: the simulator reported a component broken / fixed."""
    kind = payload.get("Type", "")
    record_audit(db, "COMPONENT_BROKEN" if broken else "COMPONENT_FIXED",
                 target_type=COMPONENT_TARGETS.get(kind, (kind or "component").lower()),
                 target_name=payload.get("Name"), success=not broken,
                 details={k: payload[k] for k in ("Type", "FineAmount", "RepairCost") if k in payload} or None)


def on_component_broken(db: Session, payload: dict) -> None:
    components.set_broken(db, payload.get("Type", ""), payload["Name"], True, raw_data=payload)
    _audit_component(db, payload, broken=True)


def on_component_fixed(db: Session, payload: dict) -> None:
    components.set_broken(db, payload.get("Type", ""), payload["Name"], False, raw_data=payload)
    _audit_component(db, payload, broken=False)


def on_penalty(db: Session, payload: dict) -> None:
    name = payload.get("ComponentName")
    log_event(db, "PENALTY", car_plate=payload.get("CarPlateNumber"),
              gate_name=name if payload.get("Type") == "BarrierGate" else None, raw_data=payload)
    db.commit()
    log.warning("PENALTY %s: %s", payload.get("FineAmount"), payload.get("Reason"))


def on_carbon_monoxide(db: Session, payload: dict) -> None:
    # Only sent for Mid / High / Critical. Level 2+: turn on that zone's exhaust fans here.
    log_event(db, "CO_ALERT", raw_data=payload)
    db.commit()


EVENT_HANDLERS: dict = {
    "car_spot_action": on_car_spot_action,
    "payment_made": on_payment_made,
    "gate_action": on_gate_action,
    "component_broken": on_component_broken,
    "component_fixed": on_component_fixed,
    "penalty": on_penalty,
    "carbon_monoxide_event": on_carbon_monoxide,
    # "test_webhook": nothing to do, the raw WEBHOOK row is enough
}


def process(db: Session, payload: dict):
    """Run the database handler for this EventClass. Never raises: a failure is logged as
    HANDLER_ERROR so the simulator keeps getting 200s. Returns the handler's result."""
    handler = EVENT_HANDLERS.get(payload.get("EventClass"))
    if handler is None:
        return None
    try:
        return handler(db, payload)
    except Exception as exc:
        log.exception("Handler failed for %s", payload.get("EventClass"))
        db.rollback()
        log_event(db, "HANDLER_ERROR", car_plate=payload.get("CarPlateNumber"),
                  raw_data={"error": str(exc)[:500], "payload": payload})
        db.commit()
        return None
