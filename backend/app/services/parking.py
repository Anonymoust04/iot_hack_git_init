"""Car lifecycle database operations (Steps 6 & 7).

Every public function here = ONE short transaction, and they never call the simulator.
Pattern for callers (webhook handlers):

    spot = allocate_spot(db, plate, car_type)      # 1. DB transaction (commits)
    if spot is None:  sim.car_goto(plate, "leavepark")   # park full -> don't block the entrance
    else:             sim.car_goto(plate, spot)          # 2. simulator call AFTER commit

WHY never call the simulator inside a transaction: a slow HTTP call while holding a row
lock makes every other arriving car wait -> traffic jam at the entrance.

Lifecycle:
    allocate_spot      spot FREE->RESERVED        session ENTERING (created)
    mark_parked        spot RESERVED->OCCUPIED    session PARKED
    mark_leaving_spot  spot ->FREE                session EXITING   (frees the spot EARLY)
    record_charge      -                          costs + exit_time (once only!)
    record_payment     -                          PAID, only if the amount matches our charge
    complete_departure spot ->FREE (if still held) session COMPLETED, PAID

`at` parameters: the simulator's own clock (webhook ServerDateTime). It runs apart from real
time, so entry and exit must both come from it for the billed minutes to be right.
"""

import functools
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models import (
    CarType,
    Event,
    ParkingSession,
    ParkingSpot,
    PaymentStatus,
    SessionStatus,
    SpotPurpose,
    SpotStatus,
    utcnow,
)
from app.services.billing import calculate_charge

log = logging.getLogger(__name__)

# A RESERVED spot whose car never arrived is released after this long (keeps capacity from leaking).
STALE_RESERVATION = timedelta(minutes=5)


MYSQL_DEADLOCK, MYSQL_LOCK_WAIT_TIMEOUT = 1213, 1205


def retry_on_deadlock(fn):
    """Re-run the whole transaction if MySQL aborts it because of a deadlock.
    Safe because every function here is ONE transaction that fully rolls back on error."""
    @functools.wraps(fn)
    def wrapper(db: Session, *args, **kwargs):
        for attempt in range(3):
            try:
                return fn(db, *args, **kwargs)
            except OperationalError as exc:
                code = exc.orig.args[0] if exc.orig and exc.orig.args else None
                if code not in (MYSQL_DEADLOCK, MYSQL_LOCK_WAIT_TIMEOUT) or attempt == 2:
                    raise
                db.rollback()
                log.warning("%s: MySQL error %s, retrying (%d)", fn.__name__, code, attempt + 1)
                time.sleep(0.05 * (attempt + 1))
    return wrapper


def log_event(db: Session, event_type: str, *, car_plate: str | None = None, parking_spot: str | None = None,
              gate_name: str | None = None, raw_data: dict | list | None = None,
              event_id: str | None = None, sequence_id: int | None = None) -> None:
    """Add an audit row to the CURRENT transaction (committed together with the change)."""
    db.add(Event(event_type=event_type, car_plate=car_plate, parking_spot=parking_spot,
                 gate_name=gate_name, raw_data=raw_data, event_id=event_id, sequence_id=sequence_id))


def active_session(db: Session, plate: str, *, lock: bool = False) -> ParkingSession | None:
    """The car's current (not COMPLETED) session, newest first."""
    q = (select(ParkingSession)
         .where(ParkingSession.car_plate == plate, ParkingSession.status != SessionStatus.COMPLETED)
         .order_by(ParkingSession.id.desc()).limit(1))
    if lock:
        q = q.with_for_update()
    return db.scalars(q).first()


# ---------------------------------------------------------------------------
# Step 6 — allocation
# ---------------------------------------------------------------------------

def _spot_preferences(car_type: CarType | None) -> list[CarType]:
    """Which spot types a car may use, best first.
    TODO(confirm with simulator rules): may a normal car use an Electric/Accessible spot
    when 'Any' spots are full (more capacity), or is that a penalty? Currently: no."""
    if car_type in (CarType.ELECTRIC, CarType.ACCESSIBLE):
        return [car_type, CarType.ANY]
    return [CarType.ANY]


def _lock_free_spot(db: Session, car_type: CarType | None) -> ParkingSpot | None:
    for spot_type in _spot_preferences(car_type):
        spot = db.scalars(
            select(ParkingSpot)
            .where(
                ParkingSpot.purpose == SpotPurpose.PARK,
                ParkingSpot.status == SpotStatus.FREE,
                ParkingSpot.car_type == spot_type,
                ParkingSpot.broken.is_(False),
                ParkingSpot.under_maintenance.is_(False),
            )
            .order_by(ParkingSpot.name)
            .limit(1)
            # FOR UPDATE      -> lock the row until COMMIT, no one else can take it
            # SKIP LOCKED     -> if another car's transaction has locked a spot, skip it and
            #                    take the next free one instead of WAITING (no queue/jam)
            .with_for_update(skip_locked=True)
        ).first()
        if spot:
            return spot
    return None


def release_stale_reservations(db: Session, now: datetime | None = None) -> int:
    """RESERVED for too long (car never arrived) -> FREE. Returns number released."""
    cutoff = (now or utcnow()) - STALE_RESERVATION
    stale = db.scalars(
        select(ParkingSpot)
        .where(ParkingSpot.status == SpotStatus.RESERVED, ParkingSpot.updated_at < cutoff)
        .with_for_update(skip_locked=True)
    ).all()
    for spot in stale:
        log_event(db, "RESERVATION_EXPIRED", car_plate=spot.current_car, parking_spot=spot.name)
        spot.status, spot.current_car = SpotStatus.FREE, None
    return len(stale)


@retry_on_deadlock
def allocate_spot(db: Session, plate: str, car_type: CarType | None = None,
                  raw_data: dict | None = None, at: datetime | None = None) -> str | None:
    """Reserve a spot for an arriving car and open its session, atomically.

    Returns the spot NAME to send the car to, or None if the park is full
    (caller should then send the car to 'leavepark' immediately).
    Safe when many cars arrive at once: two cars can never get the same spot.
    """
    try:
        # (1) the transaction starts automatically with the first query
        session = active_session(db, plate, lock=True)
        if session and session.parking_spot_id:
            # duplicate "arrived" webhook: return the spot we already gave this car
            spot = db.get(ParkingSpot, session.parking_spot_id)
            db.commit()
            return spot.name if spot else None

        # (2)+(3) find a FREE spot and lock it
        spot = _lock_free_spot(db, car_type)
        if spot is None and release_stale_reservations(db):
            spot = _lock_free_spot(db, car_type)

        if spot is None:
            log_event(db, "CAR_REJECTED", car_plate=plate, raw_data=raw_data)
            db.commit()
            return None

        # (4) mark it reserved for this car
        spot.status, spot.current_car = SpotStatus.RESERVED, plate

        # (5) create/update the session
        if session is None:
            session = ParkingSession(car_plate=plate, car_type=car_type, entry_time=at or utcnow())
            db.add(session)
        session.parking_spot_id = spot.id
        session.status = SessionStatus.ENTERING
        log_event(db, "SPOT_ASSIGNED", car_plate=plate, parking_spot=spot.name, raw_data=raw_data)

        # (6) commit -> releases the lock; the spot is now RESERVED for everyone else
        db.commit()
        return spot.name
    except Exception:
        db.rollback()
        raise


@retry_on_deadlock
def cancel_reservation(db: Session, plate: str, spot_name: str) -> None:
    """Undo allocate_spot, e.g. when the simulator 'goto' call failed."""
    db.execute(
        update(ParkingSpot)
        .where(ParkingSpot.name == spot_name, ParkingSpot.status == SpotStatus.RESERVED,
               ParkingSpot.current_car == plate)
        .values(status=SpotStatus.FREE, current_car=None)
    )
    session = active_session(db, plate, lock=True)
    if session and session.status == SessionStatus.ENTERING:
        session.parking_spot_id = None
    log_event(db, "RESERVATION_CANCELLED", car_plate=plate, parking_spot=spot_name)
    db.commit()


# ---------------------------------------------------------------------------
# Car in its spot / leaving its spot
# ---------------------------------------------------------------------------

@retry_on_deadlock
def mark_parked(db: Session, plate: str, spot_name: str, raw_data: dict | None = None,
                at: datetime | None = None) -> None:
    """Car detected in a spot. Handles a car that parked in a DIFFERENT spot than assigned."""
    try:
        spot = db.scalars(select(ParkingSpot).where(ParkingSpot.name == spot_name).with_for_update()).first()
        session = active_session(db, plate, lock=True)
        if session is None:  # we never saw it arrive (e.g. backend restarted)
            session = ParkingSession(car_plate=plate, entry_time=at or utcnow())
            db.add(session)
        if session.parking_spot_id and spot and session.parking_spot_id != spot.id:
            # parked somewhere else: free the spot we had reserved for it
            db.execute(update(ParkingSpot)
                       .where(ParkingSpot.id == session.parking_spot_id, ParkingSpot.current_car == plate)
                       .values(status=SpotStatus.FREE, current_car=None))
            log_event(db, "WRONG_SPOT", car_plate=plate, parking_spot=spot_name)
        if spot:
            spot.status, spot.current_car = SpotStatus.OCCUPIED, plate
            session.parking_spot_id = spot.id
        session.status = SessionStatus.PARKED
        session.parked_time = session.parked_time or at or utcnow()
        log_event(db, "CAR_PARKED", car_plate=plate, parking_spot=spot_name, raw_data=raw_data)
        db.commit()
    except Exception:
        db.rollback()
        raise


@retry_on_deadlock
def mark_leaving_spot(db: Session, plate: str, raw_data: dict | None = None) -> None:
    """Car left its spot / was sent to exit. Frees the spot NOW so the next car can use it
    while this one is still driving to the exit (more capacity, less queueing)."""
    try:
        session = active_session(db, plate, lock=True)
        spot_name = None
        if session and session.parking_spot_id:
            spot = db.get(ParkingSpot, session.parking_spot_id, with_for_update=True)
            if spot and spot.current_car == plate:
                spot.status, spot.current_car = SpotStatus.FREE, None
                spot_name = spot.name
        if session and session.status != SessionStatus.COMPLETED:
            session.status = SessionStatus.EXITING
        log_event(db, "CAR_EXITING", car_plate=plate, parking_spot=spot_name, raw_data=raw_data)
        db.commit()
    except Exception:
        db.rollback()
        raise


# ---------------------------------------------------------------------------
# Step 7 — charging and departure
# ---------------------------------------------------------------------------

@dataclass
class ChargeToRequest:
    parking_cost: float
    charging_cost: float


@retry_on_deadlock
def record_charge(db: Session, plate: str, is_electric: bool, at: datetime | None = None) -> ChargeToRequest | None:
    """Car is at the exit spot: compute + store the charge, ONCE.

    Returns the amounts to send to POST /car/{plate}/charge, or None if this car was already
    charged (duplicate webhook) -> caller must NOT charge again (the simulator penalizes it).
    The session row is locked, so two concurrent webhooks can't both charge.
    """
    try:
        session = active_session(db, plate, lock=True)
        if session is None or session.parking_cost is not None:
            db.commit()
            return None
        now = at or utcnow()
        charge = calculate_charge(session.entry_time, now, is_electric)
        session.exit_time = now
        session.parking_cost = Decimal(str(charge.parking_cost))
        session.charging_cost = Decimal(str(charge.charging_cost))
        session.status = SessionStatus.EXITING
        log_event(db, "CAR_CHARGED", car_plate=plate,
                  raw_data={"minutes": charge.minutes, "parking_cost": charge.parking_cost,
                            "charging_cost": charge.charging_cost})
        db.commit()
        return ChargeToRequest(charge.parking_cost, charge.charging_cost)
    except Exception:
        db.rollback()
        raise


@retry_on_deadlock
def record_payment(db: Session, plate: str, amount: Decimal, raw_data: dict | None = None) -> str | None:
    """Validate a payment the simulator says a driver made. Some payments are FAKE, so it is
    accepted only if it matches what WE charged this car, and only once.
    Returns None if accepted (session -> PAID), otherwise the reason it was rejected."""
    try:
        session = active_session(db, plate, lock=True)
        if session is None:
            reason = "no car with this plate is in the park"
        elif session.parking_cost is None:
            reason = "car has not been charged yet"
        elif session.payment_status == PaymentStatus.PAID:
            reason = "already paid"
        else:
            expected = session.parking_cost + (session.charging_cost or Decimal(0))
            reason = None if amount == expected else f"amount {amount} does not match charge {expected}"
        if reason is None:
            session.payment_status = PaymentStatus.PAID
            log_event(db, "PAYMENT_ACCEPTED", car_plate=plate, raw_data=raw_data)
        else:
            log_event(db, "PAYMENT_REJECTED", car_plate=plate, raw_data={"reason": reason, "payload": raw_data})
        db.commit()
        return reason
    except Exception:
        db.rollback()
        raise


@retry_on_deadlock
def complete_departure(db: Session, plate: str, raw_data: dict | None = None) -> None:
    """Car has left the car park. All in ONE transaction:
    session -> COMPLETED + PAID (exit_time/costs kept), spot -> FREE, departure event."""
    try:
        session = active_session(db, plate, lock=True)  # 1. find active session
        spot_name = None
        if session:
            session.exit_time = session.exit_time or utcnow()      # 2. exit time (set at charge)
            # 3./4. parking_cost / charging_cost were stored by record_charge()
            session.payment_status = PaymentStatus.PAID            # 5.
            session.status = SessionStatus.COMPLETED               # 6.
            if session.parking_spot_id:
                spot = db.get(ParkingSpot, session.parking_spot_id, with_for_update=True)
                if spot and spot.current_car == plate:             # 7./8. free spot (if not already)
                    spot.status, spot.current_car = SpotStatus.FREE, None
                    spot_name = spot.name
        log_event(db, "CAR_DEPARTED", car_plate=plate, parking_spot=spot_name, raw_data=raw_data)  # 9.
        db.commit()
    except Exception:
        db.rollback()
        raise
