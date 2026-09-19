"""Core car-park logic: spot allocation, state sync, event handling."""

import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Gate, ParkingSpot, Zone
from app.services.simulator_client import SimulatorClient

log = logging.getLogger(__name__)


def sync_from_simulator(db: Session, sim: SimulatorClient) -> None:
    """Full resync of spots/gates/zones. Call on startup or after a crash ONLY (costly)."""
    spots = sim.list_parking_spots()
    gates = sim.list_barriers()
    zones = sim.list_zones()

    db.execute(delete(ParkingSpot))
    db.execute(delete(Gate))
    db.execute(delete(Zone))
    for s in spots:
        detected = s.get("detectedCars") or []
        db.add(ParkingSpot(
            name=s["name"],
            purpose=s["purpose"],
            car_type=s["parkingForCarType"],
            zone=s.get("zoneParent") or "",
            occupied_by=detected[0] if detected else None,
            broken=s.get("broken", False),
            under_maintenance=s.get("isUnderMaintenance", False),
        ))
    for g in gates:
        db.add(Gate(
            name=g["name"],
            zone=g.get("zoneParent") or "",
            state=g["state"],
            broken=g.get("broken", False),
            under_maintenance=g.get("isUnderMaintenance", False),
        ))
    for z in zones:
        db.add(Zone(name=z["name"], co_level=z.get("gasCarbonMonoxideLevel", 0), risk=z.get("risk", "Safe")))
    db.commit()
    log.info("Synced %d spots, %d gates, %d zones", len(spots), len(gates), len(zones))


def find_free_spot(db: Session, car_type: str) -> ParkingSpot | None:
    """Pick a free, working 'Park' spot suitable for this car type.

    TODO(team): refine the policy — e.g. Electric cars prefer Electric spots,
    Accessible cars prefer Accessible spots, regular cars avoid special spots
    unless nothing else is free. Confirm the simulator's penalty rules.
    """
    base = select(ParkingSpot).where(
        ParkingSpot.purpose == "Park",
        ParkingSpot.occupied_by.is_(None),
        ParkingSpot.reserved_for.is_(None),
        ParkingSpot.broken.is_(False),
        ParkingSpot.under_maintenance.is_(False),
    )
    preferred = db.scalars(base.where(ParkingSpot.car_type == car_type).limit(1)).first()
    return preferred or db.scalars(base.where(ParkingSpot.car_type == "Any").limit(1)).first()


# ---- Webhook event handlers ----------------------------------------------
# TODO(team): the webhook payload format isn't documented yet. Hit GET /test on the
# simulator, inspect the payloads stored in event_logs, then fill these in and
# register them in EVENT_HANDLERS by their real event type names.

def handle_car_arrived(db: Session, sim: SimulatorClient, payload: dict) -> None:
    """Car at entrance: create ParkingSession, pick a spot, open gate, send car.
    If there's no free spot -> sim.car_goto(plate, 'leavepark')."""
    raise NotImplementedError


def handle_car_parked(db: Session, sim: SimulatorClient, payload: dict) -> None:
    """Car detected in spot: mark spot occupied, session -> PARKED."""
    raise NotImplementedError


def handle_car_at_exit(db: Session, sim: SimulatorClient, payload: dict) -> None:
    """Car at exit spot: calculate charge (services.billing), sim.charge_car() ONCE,
    free the spot, open exit gate, session -> CHARGED."""
    raise NotImplementedError


def handle_gate_state(db: Session, sim: SimulatorClient, payload: dict) -> None:
    """Update Gate.state."""
    raise NotImplementedError


EVENT_HANDLERS = {
    # "CarArrived": handle_car_arrived,
    # "CarParked": handle_car_parked,
    # "CarAtExit": handle_car_at_exit,
    # "GateStateChanged": handle_gate_state,
}
