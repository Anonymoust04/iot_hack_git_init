"""Sync, allocation (incl. concurrency), and the full car lifecycle against real MySQL."""

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import CarType, Event, ParkingSession, ParkingSpot, PaymentStatus, SessionStatus, SpotStatus
from app.services import parking
from app.services.sync import upsert_gates, upsert_parking_spots


def spot(name, car_type="Any", zone="ZONE1", purpose="Park", **kw):
    return {"name": name, "purpose": purpose, "parkingForCarType": car_type, "zoneParent": zone,
            "detectedCars": kw.get("cars", []), "broken": kw.get("broken", False),
            "isUnderMaintenance": kw.get("maint", False)}


def get_spot(db, name):
    db.expire_all()
    return db.scalars(select(ParkingSpot).where(ParkingSpot.name == name)).one()


# ---- Step 5: sync ---------------------------------------------------------

def test_sync_upsert_keeps_ids_and_maps_status(db):
    upsert_parking_spots(db, [spot("S1"), spot("S2", broken=True), spot("S3", cars=["ABC123"]),
                              spot("E1", purpose="EntrySpot")])
    upsert_gates(db, [{"name": "gate1", "zoneParent": "ZONE1", "broken": False,
                       "isUnderMaintenance": False, "state": "Closed"}])
    db.commit()
    first_id = get_spot(db, "S1").id
    assert get_spot(db, "S2").status == SpotStatus.BROKEN
    assert (get_spot(db, "S3").status, get_spot(db, "S3").current_car) == (SpotStatus.OCCUPIED, "ABC123")

    # second sync: same names -> UPDATE, not new rows
    upsert_parking_spots(db, [spot("S1", maint=True), spot("S2"), spot("S3"), spot("E1", purpose="EntrySpot")])
    upsert_gates(db, [{"name": "gate1", "zoneParent": "ZONE1", "broken": False,
                       "isUnderMaintenance": False, "state": "Open"}])
    db.commit()
    assert get_spot(db, "S1").id == first_id
    assert get_spot(db, "S1").status == SpotStatus.MAINTENANCE
    assert get_spot(db, "S3").current_car is None
    assert db.scalar(select(ParkingSpot.id).where(ParkingSpot.name == "S3")) is not None
    assert len(db.scalars(select(ParkingSpot)).all()) == 4


def test_sync_reads_car_counts_and_skips_leave_parking_spots(db):
    # the real simulator sends detectedCars as a count, and has LeaveParking spots (ESCAPE1...)
    upsert_parking_spots(db, [spot("S1", cars=0), spot("S2", cars=1), spot("ENTRY1", purpose="EntrySpot", cars=38),
                              spot("ESCAPE1", purpose="LeaveParking")])
    db.commit()
    assert get_spot(db, "S1").status == SpotStatus.FREE
    assert (get_spot(db, "S2").status, get_spot(db, "S2").current_car) == (SpotStatus.OCCUPIED, None)
    assert db.scalar(select(ParkingSpot.id).where(ParkingSpot.name == "ESCAPE1")) is None


def test_resync_keeps_plate_of_car_still_parked(db):
    upsert_parking_spots(db, [spot("S1")])
    db.commit()
    parking.mark_parked(db, "CAR1", "S1")                 # plate known from the Park/CarIn webhook
    upsert_parking_spots(db, [spot("S1", cars=1)])       # periodic re-sync: simulator gives a count only
    db.commit()
    s = get_spot(db, "S1")
    assert (s.status, s.current_car) == (SpotStatus.OCCUPIED, "CAR1")
    upsert_parking_spots(db, [spot("S1", cars=0)])       # car gone in the simulator
    db.commit()
    s = get_spot(db, "S1")
    assert (s.status, s.current_car) == (SpotStatus.FREE, None)


def test_stale_visits_are_closed(db):
    from datetime import timedelta
    from app.models import utcnow

    upsert_parking_spots(db, [spot("S1")])
    db.commit()
    parking.mark_parked(db, "GONE1", "S1")           # its departure webhook never arrived
    assert parking.expire_stale_sessions(db, timedelta(minutes=15)) == 0      # still fresh
    assert parking.expire_stale_sessions(db, timedelta(minutes=15), now=utcnow() + timedelta(minutes=16)) == 1
    db.expire_all()
    assert db.scalar(select(ParkingSession.status)) == SessionStatus.COMPLETED
    assert get_spot(db, "S1").status == SpotStatus.FREE


def test_car_that_entered_but_never_parked_is_closed_sooner(db):
    from datetime import timedelta
    from app.models import utcnow

    parking.record_arrival(db, "AWAY2")                 # sent to leavepark: no more events
    parking.mark_parked(db, "PARKED2", "S9")            # a parked car must stay
    later = utcnow() + timedelta(minutes=4)
    assert parking.expire_stale_sessions(db, timedelta(minutes=15), now=later,
                                         entering_older_than=timedelta(minutes=3)) == 1
    db.expire_all()
    status = dict(db.execute(select(ParkingSession.car_plate, ParkingSession.status)).all())
    assert status == {"AWAY2": SessionStatus.COMPLETED, "PARKED2": SessionStatus.PARKED}


def test_resync_keeps_reservation(db):
    upsert_parking_spots(db, [spot("S1")])
    db.commit()
    assert parking.allocate_spot(db, "CAR1") == "S1"
    upsert_parking_spots(db, [spot("S1")])  # car still driving: simulator sees nothing there
    db.commit()
    s = get_spot(db, "S1")
    assert (s.status, s.current_car) == (SpotStatus.RESERVED, "CAR1")


# ---- Step 6: allocation ---------------------------------------------------

def test_allocation_prefers_matching_type_and_rejects_when_full(db):
    upsert_parking_spots(db, [spot("A1"), spot("EV1", "Electric")])
    db.commit()
    assert parking.allocate_spot(db, "EV-CAR", CarType.ELECTRIC) == "EV1"
    assert parking.allocate_spot(db, "NORMAL", None) == "A1"
    assert parking.allocate_spot(db, "LATE", None) is None  # full
    assert db.scalar(select(Event.event_type).where(Event.car_plate == "LATE")) == "CAR_REJECTED"


def test_duplicate_arrival_returns_same_spot(db):
    upsert_parking_spots(db, [spot("A1"), spot("A2")])
    db.commit()
    assert parking.allocate_spot(db, "CAR1") == parking.allocate_spot(db, "CAR1")
    assert len(db.scalars(select(ParkingSession)).all()) == 1


def test_concurrent_arrivals_never_share_a_spot(db):
    upsert_parking_spots(db, [spot(f"S{i}") for i in range(3)])
    db.commit()

    def arrive(i):
        with SessionLocal() as s:  # each car = its own connection/transaction
            return parking.allocate_spot(s, f"CAR{i}")

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(arrive, range(5)))

    given = [r for r in results if r]
    assert len(given) == 3 and len(set(given)) == 3  # 3 spots, 3 different cars
    assert results.count(None) == 2                  # the rest were turned away, not queued


# ---- Step 7: full lifecycle ------------------------------------------------

def test_full_lifecycle_and_charge_only_once(db):
    upsert_parking_spots(db, [spot("S1", "Electric")])
    db.commit()

    assert parking.allocate_spot(db, "EV1", CarType.ELECTRIC) == "S1"
    assert get_spot(db, "S1").status == SpotStatus.RESERVED

    parking.mark_parked(db, "EV1", "S1")
    assert get_spot(db, "S1").status == SpotStatus.OCCUPIED

    parking.mark_leaving_spot(db, "EV1")
    s1 = get_spot(db, "S1")
    assert (s1.status, s1.current_car) == (SpotStatus.FREE, None)  # freed early for the next car

    charge = parking.record_charge(db, "EV1", is_electric=True)
    assert charge is not None and charge.charging_cost == charge.parking_cost
    assert parking.record_charge(db, "EV1", is_electric=True) is None  # never charge twice

    parking.complete_departure(db, "EV1")
    db.expire_all()
    session = db.scalars(select(ParkingSession).where(ParkingSession.car_plate == "EV1")).one()
    assert session.status == SessionStatus.COMPLETED
    assert session.payment_status == PaymentStatus.PENDING  # quoted, but no accepted payment
    assert session.exit_time is not None and session.parking_cost is not None  # history kept
    types = db.scalars(select(Event.event_type).where(Event.car_plate == "EV1").order_by(Event.id)).all()
    assert types == ["SPOT_ASSIGNED", "CAR_PARKED", "CAR_EXITING", "CAR_CHARGED", "CAR_DEPARTED"]


def test_wrong_spot_frees_the_reserved_one(db):
    upsert_parking_spots(db, [spot("S1"), spot("S2")])
    db.commit()
    assert parking.allocate_spot(db, "CAR1") == "S1"
    parking.mark_parked(db, "CAR1", "S2")  # driver ignored us
    assert get_spot(db, "S1").status == SpotStatus.FREE
    assert get_spot(db, "S2").current_car == "CAR1"
