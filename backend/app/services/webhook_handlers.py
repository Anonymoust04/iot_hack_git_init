"""Simulator webhook -> database + simulator commands.

Every webhook has: EventClass, EventId (unique, dedup), SequenceId (+1 per call, gap/order
detection), Signature (MD5, integrity), ServerDateTime (the simulator's clock).
The route (api/routes/webhook.py) verifies, dedups and stores the raw payload, then calls
EVENT_HANDLERS[EventClass](db, sim, payload).

Each handler: DB function first (short transaction, commits), THEN simulator call.
"""

import hashlib
import hmac
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models import CarType, GateState
from app.services import components, parking
from app.services.simulator_client import SimulatorClient

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signature / parsing
# ---------------------------------------------------------------------------

def _sig_text(value) -> str:
    # Numbers arrive as their original JSON text (see the route), so 63.564693 stays "63.564693".
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


def parse_event(payload: dict) -> tuple[str | None, str | None]:
    """Return (EventClass, car plate) from a raw webhook payload."""
    return payload.get("EventClass"), payload.get("CarPlateNumber")


def server_time(payload: dict) -> datetime | None:
    try:
        return datetime.strptime(payload["ServerDateTime"], "%Y-%m-%d %H:%M:%S")
    except (KeyError, TypeError, ValueError):
        return None


def car_type(payload: dict) -> CarType | None:
    """Webhook CarType -> our spot type. The simulator calls ordinary cars "Normal"."""
    raw = payload.get("CarType")
    if raw == "Normal":
        return CarType.ANY
    try:
        return CarType(raw)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Car lifecycle (EventClass car_spot_action)
# ---------------------------------------------------------------------------

def on_car_arrived(db: Session, sim: SimulatorClient, plate: str, car_type: CarType | None, payload: dict) -> None:
    spot = parking.allocate_spot(db, plate, car_type, raw_data=payload, at=server_time(payload))
    if spot is None:
        sim.car_goto(plate, "leavepark")  # full: send away at once so the entrance isn't blocked
        return
    try:
        # TODO(confirm): which gate belongs to the entry spot the car is waiting at
        # sim.open_gate("<entry gate>")
        sim.car_goto(plate, spot)
    except Exception:
        parking.cancel_reservation(db, plate, spot)  # don't leak the spot
        raise


def on_car_parked(db: Session, sim: SimulatorClient, plate: str, spot_name: str, payload: dict) -> None:
    parking.mark_parked(db, plate, spot_name, raw_data=payload, at=server_time(payload))


def on_car_left_spot(db: Session, sim: SimulatorClient, plate: str, payload: dict) -> None:
    parking.mark_leaving_spot(db, plate, raw_data=payload)


def on_car_at_exit(db: Session, sim: SimulatorClient, plate: str, is_electric: bool, payload: dict) -> None:
    charge = parking.record_charge(db, plate, is_electric, at=server_time(payload))
    if charge is not None:  # None = already charged -> never charge twice (penalty)
        sim.charge_car(plate, charge.parking_cost, charge.charging_cost)
    # The exit gate should open only after a VALID payment_made (see on_payment_made).


def on_car_departed(db: Session, sim: SimulatorClient, plate: str, payload: dict) -> None:
    parking.complete_departure(db, plate, raw_data=payload)


def handle_car_spot_action(db: Session, sim: SimulatorClient, payload: dict) -> None:
    plate = payload["CarPlateNumber"]
    spot_type, direction = payload.get("SpotType"), payload.get("Direction")
    if spot_type == "EntrySpot" and direction == "CarIn":
        on_car_arrived(db, sim, plate, car_type(payload), payload)
    elif spot_type == "Park" and direction == "CarIn":
        on_car_parked(db, sim, plate, payload["SpotName"], payload)
    elif spot_type == "Park" and direction == "CarOut":
        on_car_left_spot(db, sim, plate, payload)
    elif spot_type == "ExitSpot" and direction == "CarIn":
        on_car_at_exit(db, sim, plate, car_type(payload) == CarType.ELECTRIC, payload)
    elif spot_type == "ExitSpot" and direction == "CarOut":
        on_car_departed(db, sim, plate, payload)
    # EntrySpot/CarOut (car drove into the park) needs no action: the raw WEBHOOK row records it.


# ---------------------------------------------------------------------------
# Payments, gates, components, alerts
# ---------------------------------------------------------------------------

def on_payment_made(db: Session, sim: SimulatorClient, payload: dict) -> None:
    plate = payload["CarPlateNumber"]
    try:
        amount = Decimal(str(payload["Amount"]))
    except (KeyError, InvalidOperation):
        parking.log_event(db, "PAYMENT_REJECTED", car_plate=plate,
                          raw_data={"reason": "missing or invalid Amount", "payload": payload})
        db.commit()
        return
    rejected = parking.record_payment(db, plate, amount, raw_data=payload)
    if rejected:
        log.warning("Payment from %s rejected: %s", plate, rejected)
        return
    # TODO(confirm): which gate belongs to the exit spot; open it here to let the paid car leave.
    # sim.open_gate("<exit gate>")


def on_gate_action(db: Session, sim: SimulatorClient, payload: dict) -> None:
    components.set_gate_state(db, payload["Name"], GateState(payload["Action"]))


def on_component_broken(db: Session, sim: SimulatorClient, payload: dict) -> None:
    components.set_broken(db, payload.get("Type", ""), payload["Name"], True, raw_data=payload)


def on_component_fixed(db: Session, sim: SimulatorClient, payload: dict) -> None:
    components.set_broken(db, payload.get("Type", ""), payload["Name"], False, raw_data=payload)


def on_penalty(db: Session, sim: SimulatorClient, payload: dict) -> None:
    name = payload.get("ComponentName")
    parking.log_event(db, "PENALTY", car_plate=payload.get("CarPlateNumber"),
                      gate_name=name if payload.get("Type") == "BarrierGate" else None, raw_data=payload)
    db.commit()
    log.warning("PENALTY %s: %s", payload.get("FineAmount"), payload.get("Reason"))


def on_carbon_monoxide(db: Session, sim: SimulatorClient, payload: dict) -> None:
    # Only sent for Mid / High / Critical. Level 2+: turn on that zone's exhaust fans here.
    parking.log_event(db, "CO_ALERT", raw_data=payload)
    db.commit()


EVENT_HANDLERS: dict = {
    "car_spot_action": handle_car_spot_action,
    "payment_made": on_payment_made,
    "gate_action": on_gate_action,
    "component_broken": on_component_broken,
    "component_fixed": on_component_fixed,
    "penalty": on_penalty,
    "carbon_monoxide_event": on_carbon_monoxide,
    # "test_webhook": nothing to do, the raw WEBHOOK row is enough
}
