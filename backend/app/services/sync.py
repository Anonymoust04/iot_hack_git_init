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

from sqlalchemy import case, select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from app.models import Event, Gate, ParkingSpot, SpotStatus
from app.services.simulator_client import SimulatorClient

log = logging.getLogger(__name__)


def _detected_plate(spot: dict) -> str | None:
    cars = spot.get("detectedCars") or []
    if not cars:
        return None
    car = cars[0]
    # TODO(confirm): docs show detectedCars as a list but not what one item looks like.
    # Assuming a plate string; if it is an object, the raw text is kept (truncated) so the
    # spot is still treated as OCCUPIED rather than wrongly FREE.
    return car if isinstance(car, str) else str(car)[:32]


def _spot_status(spot: dict, plate: str | None, old_status: SpotStatus | None) -> SpotStatus:
    if spot.get("broken"):
        return SpotStatus.BROKEN
    if spot.get("isUnderMaintenance"):
        return SpotStatus.MAINTENANCE
    if plate:
        return SpotStatus.OCCUPIED
    if old_status == SpotStatus.RESERVED:
        return SpotStatus.RESERVED  # a car is still driving there (resync mid-level)
    return SpotStatus.FREE


def upsert_parking_spots(db: Session, spots: list[dict]) -> int:
    old = dict(db.execute(select(ParkingSpot.name, ParkingSpot.status)).all())
    rows = []
    for s in spots:
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
        # RESERVED spots keep their current_car (the car on its way)
        current_car=case(
            (stmt.inserted.status == SpotStatus.RESERVED.value, table.c.current_car),
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


def sync_from_simulator(db: Session, sim: SimulatorClient) -> dict:
    """Fetch spots + gates from the simulator and upsert them in ONE transaction."""
    spots = sim.list_parking_spots()
    gates = sim.list_barriers()
    counts = {"spots": upsert_parking_spots(db, spots), "gates": upsert_gates(db, gates)}
    db.add(Event(event_type="SYNC", raw_data={"spots": spots, "gates": gates}))
    db.commit()
    log.info("Synced %(spots)d spots and %(gates)d gates", counts)
    return counts
