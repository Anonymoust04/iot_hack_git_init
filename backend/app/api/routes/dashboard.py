"""Read-only park status for the dashboard (served from MySQL, never from the simulator)."""

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.models import Gate, ParkingSpot, SpotPurpose, SpotStatus
from app.schemas import DashboardOut, GateOut, SpotOut, ZoneSummary

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


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
    return DashboardOut(
        zones=zone_list,
        gates=[GateOut.model_validate(g) for g in gates],
        total_free=total_free,
        park_full=total_free == 0,
    )


@router.get("/spots", response_model=list[SpotOut])
def spots(db: DbSession, _: CurrentUser, zone: str | None = None):
    q = select(ParkingSpot).order_by(ParkingSpot.name)
    if zone:
        q = q.where(ParkingSpot.zone == zone)
    return db.scalars(q).all()


@router.get("/gates", response_model=list[GateOut])
def gates(db: DbSession, _: CurrentUser):
    return db.scalars(select(Gate).order_by(Gate.name)).all()
