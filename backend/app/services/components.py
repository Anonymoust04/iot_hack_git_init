"""Gate / spot state changes reported by webhooks (gate_action, component_broken, component_fixed).

Each function = ONE short transaction, never calls the simulator (same rule as parking.py).
"""

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from app.models import Gate, GateState, ParkingSpot, SpotStatus
from app.services.parking import log_event, retry_on_deadlock


@retry_on_deadlock
def set_gate_state(db: Session, name: str, state: GateState) -> None:
    # upsert: works even if the level-start sync hasn't created this gate yet
    stmt = insert(Gate.__table__).values(name=name, state=state.value)
    db.execute(stmt.on_duplicate_key_update(state=stmt.inserted.state))
    db.commit()


@retry_on_deadlock
def set_broken(db: Session, component_type: str, name: str, broken: bool, raw_data: dict | None = None) -> None:
    """component_broken (broken=True) / component_fixed (broken=False)."""
    try:
        if component_type == "BarrierGate":
            stmt = insert(Gate.__table__).values(name=name, broken=broken)
            db.execute(stmt.on_duplicate_key_update(broken=stmt.inserted.broken))
            log_event(db, "COMPONENT_BROKEN" if broken else "COMPONENT_FIXED", gate_name=name, raw_data=raw_data)
        else:
            # TODO(confirm): the docs only show Type "BarrierGate". If a spot breaks, the type name is
            # guessed to be "ParkingSpot"; check events.raw_data for the real one.
            spot = None
            if component_type == "ParkingSpot":
                spot = db.scalars(select(ParkingSpot).where(ParkingSpot.name == name).with_for_update()).first()
            if spot:
                spot.broken = broken
                if broken:
                    spot.status = SpotStatus.BROKEN
                elif spot.status == SpotStatus.BROKEN:
                    spot.status = SpotStatus.OCCUPIED if spot.current_car else SpotStatus.FREE
            log_event(db, "COMPONENT_BROKEN" if broken else "COMPONENT_FIXED",
                      parking_spot=name if spot else None, raw_data=raw_data)
        db.commit()
    except Exception:
        db.rollback()
        raise
