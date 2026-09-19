"""Read-only park status for the dashboard (served from MySQL, never from the simulator)."""

from fastapi import APIRouter
from sqlalchemy import and_, exists, func, not_, select

from app.api.deps import CurrentUser, DbSession
from app.config import get_settings
from app.models import Event, Gate, ParkingSession, ParkingSpot, SessionStatus, SpotPurpose, SpotStatus
from app.schemas import DashboardOut, GateOut, SpotOut, ZoneSummary

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def gate_out(gate: Gate) -> GateOut:
    s = get_settings()
    roles = {name.strip().lower(): "entrance" for name in s.entry_gate.split(",") if name.strip()}
    roles |= {name.strip().lower(): "exit" for name in s.exit_gate.split(",") if name.strip()}
    return GateOut.model_validate(gate).model_copy(update={"role": roles.get(gate.name.lower())})


@router.get("", response_model=DashboardOut)
def summary(db: DbSession, _: CurrentUser):
    # One GROUP BY query: count of each status per zone (see database/queries.sql)
    rows = db.execute(
        select(ParkingSpot.zone, ParkingSpot.status, func.count())
        .where(ParkingSpot.purpose == SpotPurpose.PARK)
        .group_by(ParkingSpot.zone, ParkingSpot.status)
    ).all()
    zones: dict[str, ZoneSummary] = {}
    for zone, status, n in rows:
        z = zones.setdefault(zone, ZoneSummary(zone=zone, total=0, free=0, reserved=0, occupied=0, unavailable=0))
        z.total += n
        if status == SpotStatus.FREE:
            z.free += n
        elif status == SpotStatus.RESERVED:
            z.reserved += n
        elif status == SpotStatus.OCCUPIED:
            z.occupied += n
        else:
            z.unavailable += n

    zone_list = sorted(zones.values(), key=lambda z: z.zone)
    total_free = sum(z.free for z in zone_list)
    gates = db.scalars(select(Gate).order_by(Gate.name)).all()
    # Inside = past the entrance: not completed, and not still queuing outside (ENTERING without CAR_ENTERED)
    entered = exists().where(
        Event.car_plate == ParkingSession.car_plate,
        Event.event_type == "CAR_ENTERED",
        Event.event_time >= ParkingSession.created_at,
    )
    queuing = and_(ParkingSession.status == SessionStatus.ENTERING, not_(entered))
    cars_inside = db.scalar(
        select(func.count()).select_from(ParkingSession)
        .where(ParkingSession.status != SessionStatus.COMPLETED, not_(queuing))
    )
    return DashboardOut(
        zones=zone_list,
        gates=[gate_out(g) for g in gates],
        total_free=total_free,
        park_full=total_free == 0,
        cars_inside=cars_inside,
    )


@router.get("/spots", response_model=list[SpotOut])
def spots(db: DbSession, _: CurrentUser, zone: str | None = None):
    q = select(ParkingSpot).order_by(ParkingSpot.name)
    if zone:
        q = q.where(ParkingSpot.zone == zone)
    return db.scalars(q).all()


@router.get("/gates", response_model=list[GateOut])
def gates(db: DbSession, _: CurrentUser):
    return [gate_out(g) for g in db.scalars(select(Gate).order_by(Gate.name)).all()]
