"""Step 5 — Level-start synchronization: simulator -> MySQL.

Call ONCE when a level starts (or after a crash). NOT in a loop: every list-* call
has a simulated cost. After this, webhooks keep the tables up to date.

    Simulator GET /list-parking-spots  ->  UPSERT parking_spots
    Simulator GET /list-barriers       ->  UPSERT gates

UPSERT = INSERT ... ON DUPLICATE KEY UPDATE (MySQL). The UNIQUE key on `name` decides
"new row" vs "update existing row". Spot ids never change, so parking_sessions
foreign keys stay valid. Rows are never deleted.
"""

import logging

from sqlalchemy import case, select, update
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from app.models import Event, Gate, ParkingSession, ParkingSpot, SessionStatus, SpotPurpose, SpotStatus
from app.services.simulator_client import SimulatorClient

log = logging.getLogger(__name__)


def _car_count(spot: dict) -> int:
    # The simulator sends detectedCars as a COUNT (e.g. 0, or 38 cars queued at ENTRY1), not plates
    cars = spot.get("detectedCars") or 0
    return len(cars) if isinstance(cars, list) else int(cars)


def _detected_plate(spot: dict) -> str | None:
    cars = spot.get("detectedCars")
    if isinstance(cars, list) and cars and isinstance(cars[0], str):
        return cars[0]
    return None  # a count only: the plate is filled in when the car's webhook arrives


def _spot_status(spot: dict, plate: str | None, old_status: SpotStatus | None) -> SpotStatus:
    if spot.get("broken"):
        return SpotStatus.BROKEN
    if spot.get("isUnderMaintenance"):
        return SpotStatus.MAINTENANCE
    if plate or _car_count(spot):
        return SpotStatus.OCCUPIED
    if old_status == SpotStatus.RESERVED:
        return SpotStatus.RESERVED  # a car is still driving there (resync mid-level)
    return SpotStatus.FREE


def upsert_parking_spots(db: Session, spots: list[dict]) -> int:
    old = dict(db.execute(select(ParkingSpot.name, ParkingSpot.status)).all())
    rows = []
    known_purposes = {p.value for p in SpotPurpose}
    for s in spots:
        if s.get("purpose") not in known_purposes:
            continue  # e.g. LeaveParking (ESCAPE1...): where 'leavepark' cars go, never assigned
        plate = _detected_plate(s)
        status = _spot_status(s, plate, old.get(s["name"]))
        rows.append({
            "name": s["name"],
            "zone": s.get("zoneParent") or "",
            "purpose": s["purpose"],
            "car_type": s.get("parkingForCarType") or "Any",
            "status": status.value,
            "current_car": plate,
            "broken": bool(s.get("broken")),
            "under_maintenance": bool(s.get("isUnderMaintenance")),
        })
    if not rows:
        return 0
    stmt = insert(ParkingSpot.__table__).values(rows)
    table = ParkingSpot.__table__
    stmt = stmt.on_duplicate_key_update(
        zone=stmt.inserted.zone,
        purpose=stmt.inserted.purpose,
        car_type=stmt.inserted.car_type,
        status=stmt.inserted.status,
        # RESERVED spots keep their current_car (the car on its way); so do spots still OCCUPIED
        # when the simulator gives only a car count (the plate came from the Park/CarIn webhook)
        current_car=case(
            (stmt.inserted.status == SpotStatus.RESERVED.value, table.c.current_car),
            (
                (stmt.inserted.status == SpotStatus.OCCUPIED.value) & stmt.inserted.current_car.is_(None),
                table.c.current_car,
            ),
            else_=stmt.inserted.current_car,
        ),
        broken=stmt.inserted.broken,
        under_maintenance=stmt.inserted.under_maintenance,
    )
    db.execute(stmt)
    return len(rows)


def upsert_gates(db: Session, gates: list[dict]) -> int:
    rows = [{
        "name": g["name"],
        "zone": g.get("zoneParent") or "",
        "state": g["state"],
        "broken": bool(g.get("broken")),
        "under_maintenance": bool(g.get("isUnderMaintenance")),
    } for g in gates]
    if not rows:
        return 0
    stmt = insert(Gate.__table__).values(rows)
    stmt = stmt.on_duplicate_key_update(
        zone=stmt.inserted.zone,
        state=stmt.inserted.state,
        broken=stmt.inserted.broken,
        under_maintenance=stmt.inserted.under_maintenance,
    )
    db.execute(stmt)
    return len(rows)


def has_spots(db: Session) -> bool:
    return db.scalar(select(ParkingSpot.id).where(ParkingSpot.purpose == SpotPurpose.PARK).limit(1)) is not None


def backfill_plates(db: Session) -> None:
    """The simulator reports only a car count per spot, so a re-sync can leave an occupied spot
    without a plate (e.g. it briefly reported the spot empty). Take the plate from the car's open
    visit, so the dashboard keeps showing who is parked where."""
    plate_of_open_visit = (
        select(ParkingSession.car_plate)
        .where(ParkingSession.parking_spot_id == ParkingSpot.id,
               ParkingSession.status.in_([SessionStatus.PARKED, SessionStatus.EXITING]))
        .order_by(ParkingSession.id.desc())
        .limit(1)
        .correlate(ParkingSpot)
        .scalar_subquery()
    )
    db.execute(
        update(ParkingSpot)
        .where(ParkingSpot.status == SpotStatus.OCCUPIED,
               ParkingSpot.current_car.is_(None),
               plate_of_open_visit.is_not(None))
        .values(current_car=plate_of_open_visit)
    )


def sync_from_simulator(db: Session, sim: SimulatorClient, log_event: bool = True) -> dict:
    """Fetch spots + gates from the simulator and upsert them in ONE transaction.
    log_event=False for the periodic re-sync, so the event log isn't flooded."""
    spots = sim.list_parking_spots()
    gates = sim.list_barriers()
    counts = {"spots": upsert_parking_spots(db, spots), "gates": upsert_gates(db, gates)}
    backfill_plates(db)
    if log_event:
        db.add(Event(event_type="SYNC", raw_data={"spots": spots, "gates": gates}))
    db.commit()
    log.info("Synced %(spots)d spots and %(gates)d gates", counts)
    return counts
