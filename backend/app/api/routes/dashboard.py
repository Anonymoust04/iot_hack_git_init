"""Read-only park status for the dashboard (served from our DB, not the simulator)."""

from collections import defaultdict

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models import Gate, ParkingSpot
from app.schemas import DashboardOut, GateOut, SpotOut, ZoneSummary

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardOut)
def summary(db: DbSession, _: CurrentUser):
    spots = db.scalars(select(ParkingSpot).where(ParkingSpot.purpose == "Park")).all()
    by_zone: dict[str, ZoneSummary] = defaultdict(lambda: ZoneSummary(zone="", total=0, occupied=0, free=0, unavailable=0))
    for s in spots:
        z = by_zone[s.zone]
        z.zone = s.zone
        z.total += 1
        if s.broken or s.under_maintenance:
            z.unavailable += 1
        elif s.occupied_by or s.reserved_for:
            z.occupied += 1
        else:
            z.free += 1

    zones = sorted(by_zone.values(), key=lambda z: z.zone)
    total_free = sum(z.free for z in zones)
    gates = db.scalars(select(Gate).order_by(Gate.name)).all()
    return DashboardOut(
        zones=zones,
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
