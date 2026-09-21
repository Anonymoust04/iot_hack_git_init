"""
Parking Simulator Backend - Level 2
====================================
Entrance gates : gate1 (Zone 1 / S-spots), gate3 (Zone 2 / bay-spots), gate5 (Zone 3 / P-spots)
Exit gates     : gate2, gate4, gate6
Zones          : Zone 1 -> S1-S30 | Zone 2 -> bay36-bay65 | Zone 3 -> P69-P98

Key behaviours
--------------
- Plate-based, queue-aware routing  cars across all three zones
- Vehicle-type-aware spot selection (Electric -> EV spots, Accessible -> Accessible spots)
- Re-entry guard: rerouted cars arriving at the correct gate are let through immediately
- Fee charged only after the car has confirmed physically parked (Park/CarIn event)
- CO risk Mid/High/Critical or high numeric CO -> working fans ON; Safe and low CO -> OFF
- Day/Night light control (daytime 06:00-18:00 -> lights OFF; night -> lights ON)
- Preventive maintenance: polls list-alarms every 5 s and repairs idle components
- Usage cycle tracking: polls every 30 s and logs component cycle counts
- Broken/unavailable component health tracked from webhook events; exposed on /component-health
"""

import asyncio
import hashlib
import json
import math
import re
import time
import uuid
import zlib
from collections import Counter, defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Annotated
from pydantic import BaseModel
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
import httpx

app = FastAPI(title="Parking Simulator Backend - Level 2")


def sim_now() -> datetime:
    """Naive UTC, matching how the database stores every timestamp."""
    return datetime.now(UTC).replace(tzinfo=None)


def _utc_from_timestamp(value: float) -> datetime:
    return datetime.fromtimestamp(value, UTC).replace(tzinfo=None)


import db_hook
import sys
from pathlib import Path

_backend_dir = str(Path(__file__).resolve().parents[1])
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
from app.api.deps import AdminUser, CurrentUser, require_permission
from app.config import Settings, get_settings
from app.core.permissions import Permission
from app.models import User
from app.services.device_overrides import has_manual_override, set_manual_override

GateControlDependency = Annotated[User, Depends(require_permission(Permission.GATE_CONTROL))]
LightControlDependency = Annotated[User, Depends(require_permission(Permission.LIGHT_CONTROL))]
FanControlDependency = Annotated[User, Depends(require_permission(Permission.FAN_CONTROL))]
RepairDependency = Annotated[User, Depends(require_permission(Permission.REPAIR))]
AdminDependency = AdminUser

db_hook.setup(app)

# -------------------------------------------------
# CONFIGURATION CONSTANTS
# -------------------------------------------------

# SIM_BASE_URL in .env (e.g. http://127.0.0.1:9898/api/v1); tests point it elsewhere so they never
# drive the real simulator
SIMULATOR_URL      = str(getattr(get_settings(), "sim_base_url", "http://127.0.0.1:9898/api/v1")).rstrip("/").removesuffix("/api/v1")
SIMULATOR_EMAIL    = "admin"
SIMULATOR_PASSWORD = "admin"

# -------------------------------------------------
# PARKING ZONE REGISTRY
# -------------------------------------------------
#
# Every zone declares its own layout, capacity, entrances and exits. Adding a
# fourth zone (or giving Zone 2 a second entrance) is a change to ZONE_DEFS
# only: spot ownership, gate routing, EntrySpot/ExitSpot names, per-zone
# capacity and the failover order are all derived from this table below.
#
#   spots        the parking spot names that belong to the zone, in layout order
#   entrances    entrance gates serving the zone, most preferred first
#   exits        exit gates serving the zone, most preferred first
#   entry_spots  gate -> the physical EntrySpot a car is sent to for that gate
#   exit_spots   gate -> the physical ExitSpot the simulator reports for that gate
#   car_types    spot name -> reserved vehicle type (spots not listed are "Any")

ZONE_DEFS: list[dict] = [
    {
        "id": "ZONE1",
        "label": "Zone 1",
        "spots": [f"S{i}" for i in range(1, 31)],
        "entrances": ["gate1"],
        "exits": ["gate2"],
        "entry_spots": {"gate1": "ENTRY1"},
        "exit_spots": {"gate2": "EXIT1"},
        "car_types": {
            "S5": "Electric", "S6": "Electric", "S10": "Electric", "S11": "Electric",
            "S20": "Electric", "S21": "Electric", "S25": "Electric", "S26": "Electric",
            "S7": "Accessible", "S8": "Accessible", "S9": "Accessible",
        },
    },
    {
        "id": "ZONE2",
        "label": "Zone 2",
        "spots": [f"bay{i}" for i in range(36, 66)],
        "entrances": ["gate3"],
        "exits": ["gate4"],
        "entry_spots": {"gate3": "ENTRY2"},
        "exit_spots": {"gate4": "EXIT2"},
        "car_types": {
            "bay42": "Electric", "bay43": "Electric", "bay47": "Electric", "bay48": "Electric",
            "bay56": "Electric", "bay57": "Electric", "bay61": "Electric", "bay62": "Electric",
            "bay58": "Accessible", "bay59": "Accessible", "bay60": "Accessible",
        },
    },
    {
        "id": "ZONE3",
        "label": "Zone 3",
        "spots": [f"P{i}" for i in range(69, 99)],
        "entrances": ["gate5"],
        "exits": ["gate6"],
        "entry_spots": {"gate5": "ENTRY3"},
        "exit_spots": {"gate6": "EXIT3"},
        "car_types": {
            "P74": "Electric", "P75": "Electric", "P76": "Electric", "P77": "Electric",
            "P78": "Electric",
            "P79": "Accessible", "P80": "Accessible", "P81": "Accessible",
        },
    },
]

ZONES: dict[str, dict] = {z["id"]: z for z in ZONE_DEFS}

# Derived lookups. Nothing below this line hard-codes a zone, a gate or a spot range.
VALID_PARKING_SPOTS: list[str] = [s for z in ZONE_DEFS for s in z["spots"]]
SPOT_ZONE: dict[str, str] = {s: z["id"] for z in ZONE_DEFS for s in z["spots"]}
ZONE_CAPACITY: dict[str, int] = {z["id"]: len(z["spots"]) for z in ZONE_DEFS}

ENTRANCE_GATES: list[str] = [g for z in ZONE_DEFS for g in z["entrances"]]
EXIT_GATES: list[str] = [g for z in ZONE_DEFS for g in z["exits"]]
ALL_GATES: list[str] = ENTRANCE_GATES + EXIT_GATES

GATE_ZONE: dict[str, str] = {g: z["id"] for z in ZONE_DEFS for g in z["entrances"] + z["exits"]}
GATE_ENTRY_SPOT: dict[str, str] = {g: name for z in ZONE_DEFS for g, name in z["entry_spots"].items()}
GATE_EXIT_SPOT: dict[str, str] = {g: name for z in ZONE_DEFS for g, name in z["exit_spots"].items()}
ENTRY_SPOT_GATE: dict[str, str] = {name: g for g, name in GATE_ENTRY_SPOT.items()}
EXIT_SPOT_GATE: dict[str, str] = {name: g for g, name in GATE_EXIT_SPOT.items()}
ENTRY_SPOTS: list[str] = list(ENTRY_SPOT_GATE)

SPOT_CAR_TYPES: dict[str, str] = {
    spot: kind for z in ZONE_DEFS for spot, kind in z["car_types"].items()
}


def get_spot_type(spot_name: str) -> str:
    return SPOT_CAR_TYPES.get(spot_name, "Any")


def zone_of_spot(spot_name: str) -> str | None:
    return SPOT_ZONE.get(spot_name)


def zone_of_gate(gate_name: str) -> str | None:
    return GATE_ZONE.get(gate_name)


def normalize_vehicle_type(raw_type: str) -> str:
    t = str(raw_type or "").strip().lower()
    if "elect" in t or "ev" in t:
        return "Electric"
    if "access" in t or "disab" in t or "handicap" in t:
        return "Accessible"
    return "Normal"


def spot_gate(spot_name: str) -> str:
    """Preferred entrance gate for a spot's zone (first declared entrance)."""
    zone = ZONES.get(SPOT_ZONE.get(spot_name, ""), None)
    if zone and zone["entrances"]:
        return zone["entrances"][0]
    return ENTRANCE_GATES[0]


def entrance_gates_for_spot(spot_name: str) -> list[str]:
    """Every entrance gate that can deliver a car to this spot's zone, preferred first."""
    zone = ZONES.get(SPOT_ZONE.get(spot_name, ""), None)
    return list(zone["entrances"]) if zone else []


def exit_gates_for_zone(zone_id: str | None) -> list[str]:
    zone = ZONES.get(zone_id or "", None)
    return list(zone["exits"]) if zone else []


def entry_spot_for_gate(gate: str) -> str:
    return GATE_ENTRY_SPOT.get(gate, ENTRY_SPOTS[0] if ENTRY_SPOTS else "ENTRY1")


def extract_spot_number(name: str) -> int:
    digits = "".join(ch for ch in name if ch.isdigit())
    return int(digits) if digits else 9999


def _normalise_spot_key(spot_name: str) -> str:
    return (spot_name or "").lower().replace(" ", "").replace("_", "").replace("-", "")


def gate_for_entry_spot(spot_name: str) -> str:
    """Which entrance gate an EntrySpot (or a gate name) belongs to."""
    norm = _normalise_spot_key(spot_name)
    for entry_name, gate in ENTRY_SPOT_GATE.items():
        if _normalise_spot_key(entry_name) == norm or _normalise_spot_key(gate) == norm:
            return gate
    for entry_name, gate in ENTRY_SPOT_GATE.items():
        if _normalise_spot_key(entry_name) in norm or _normalise_spot_key(gate) in norm:
            return gate
    return ENTRANCE_GATES[0]


def exit_gate_for_spot(spot_name: str) -> str:
    """Which exit gate an ExitSpot (or a gate name) belongs to."""
    norm = _normalise_spot_key(spot_name)
    for exit_name, gate in EXIT_SPOT_GATE.items():
        if _normalise_spot_key(exit_name) == norm or _normalise_spot_key(gate) == norm:
            return gate
    for exit_name, gate in EXIT_SPOT_GATE.items():
        if _normalise_spot_key(exit_name) in norm or _normalise_spot_key(gate) in norm:
            return gate
    return EXIT_GATES[0]


# -------------------------------------------------
# IN-MEMORY STATE
# -------------------------------------------------

SIMULATOR_TOKEN: str | None = None
_token_lock = asyncio.Lock()

parking_spots: dict[str, bool] = {s: True for s in VALID_PARKING_SPOTS}
spot_lock = asyncio.Lock()
gate_queues: dict[str, asyncio.Queue] = {g: asyncio.Queue() for g in ALL_GATES}
gate_inflight: dict[str, str] = {}  # a gate admits one plate until its EntrySpot CarOut
exit_inflight: dict[str, str] = {}  # exit gate releases the next car after ExitSpot CarOut

# Physical EntrySpot occupancy. The simulator rejects a second car if it is
# sent to an EntrySpot while another car is still sitting there.
entry_occupied: dict[str, bool] = {name: False for name in ENTRY_SPOTS}

# Which plate currently holds each EntrySpot, and since when (watchdog input).
entry_car: dict[str, str | None] = {name: None for name in ENTRY_SPOTS}
entry_occupied_since: dict[str, float] = {name: 0.0 for name in ENTRY_SPOTS}

# A car that never produces its EntrySpot CarOut blocks the whole zone; after this
# long the watchdog nudges it on and releases the spot for the queue behind it.
ENTRY_SPOT_TIMEOUT_SECONDS = 45.0

# Per-EntrySpot FIFO queue for cars waiting while the spot is occupied.
entry_queues: dict[str, asyncio.Queue] = {name: asyncio.Queue() for name in ENTRY_SPOTS}
entry_lock = asyncio.Lock()
active_cars:  dict[str, dict] = {}
charged_cars: set[str] = set()

# Bounded history. deque drops the oldest in O(1); the old list+pop(0) copied the
# whole list on every webhook, which is the hot path under event bursts.
webhook_events:      deque[dict] = deque(maxlen=400)
recent_car_arrivals: deque[dict] = deque(maxlen=50)

component_health: dict[str, dict] = {}
usage_cycles:     dict[str, dict] = {}
barrier_states:   dict[str, str] = {}
spot_details:     dict[str, dict] = {}
fan_details:      dict[str, dict] = {}
repair_attempts:  dict[str, float] = {}
REPAIR_RETRY_SECONDS = 60.0
MAINTENANCE_SCAN_SECONDS = 15.0
maintenance_wakeup = asyncio.Event()

_last_sim_hour: int = 12


# =================================================================
# INCIDENT LEDGER  (audit trail for everything below)
# =================================================================
#
# One place records every abnormal thing the backend notices or does:
# rejected webhooks, sensor faults, maintenance switches, double parking,
# suspicious payments, gate failovers. Each incident goes to three places:
#   1. an in-memory ring, so the admin pages stay fast and work without MySQL;
#   2. the `events` table, so it appears in reports and the daily summary;
#   3. `audit_logs`, so the audit page shows who or what caused it.

INCIDENT_RING_SIZE = 1000

SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}

# Incident type -> (events.event_type, audit action). Keeping the mapping in one
# table means a new incident type is a one-line change, and docs/EVENT_TYPES.md
# has a single list to mirror.
INCIDENT_TYPES: dict[str, tuple[str, str]] = {
    "integrity_rejected":  ("INTEGRITY_REJECTED",   "WEBHOOK_REJECTED"),
    "integrity_warning":   ("INTEGRITY_WARNING",    "WEBHOOK_SUSPECT"),
    "sensor_abnormal":     ("SENSOR_ABNORMAL",      "SENSOR_FAULT"),
    "spot_maintenance":    ("SPOT_MAINTENANCE",     "MAINTENANCE_MODE"),
    "double_parking":      ("DOUBLE_PARKING",       "DOUBLE_PARKING_WARNING"),
    "vehicle_misparked":   ("VEHICLE_MISPARKED",    "VEHICLE_MISPARKED"),
    "payment_suspicious":  ("PAYMENT_SUSPICIOUS",   "PAYMENT_SUSPECT"),
    "payment_retry":       ("PAYMENT_RETRY",        "PAYMENT_REQUESTED_AGAIN"),
    "payment_settled":     ("PAYMENT_SETTLED",      "PAYMENT_CONFIRMED"),
    "gate_failover":       ("GATE_FAILOVER",        "GATE_FAILOVER"),
    "capacity":            ("ZONE_CAPACITY",        "ZONE_CAPACITY"),
}

incidents: deque[dict] = deque(maxlen=INCIDENT_RING_SIZE)
incident_counts: Counter = Counter()
_incident_seq = 0


def _persist_incident(entry: dict) -> None:
    """events + audit_logs row for one incident. Runs in a worker thread."""
    event_type, action = INCIDENT_TYPES.get(entry["type"], ("INCIDENT", "INCIDENT"))
    try:
        from app.db.session import SessionLocal
        from app.services.audit import record_audit
        from app.services.parking import log_event
        with SessionLocal() as db:
            log_event(db, event_type, car_plate=entry.get("plate"),
                      parking_spot=entry.get("spot"), gate_name=entry.get("gate"),
                      raw_data={"severity": entry["severity"], "reason": entry["reason"],
                                "zone": entry.get("zone"), **(entry.get("details") or {})})
            db.commit()
            record_audit(db, action, actor=entry.get("actor"),
                         target_type=entry.get("target_type") or "system",
                         target_name=entry.get("spot") or entry.get("gate") or entry.get("plate"),
                         success=entry["severity"] == "info",
                         details={"reason": entry["reason"], "zone": entry.get("zone"),
                                  **(entry.get("details") or {})})
    except Exception as exc:  # never let bookkeeping break the car flow
        print(f"[INCIDENT] not persisted ({entry['type']}): {exc}")


def record_incident(kind: str, reason: str, *, severity: str = "warning", plate: str | None = None,
                    spot: str | None = None, gate: str | None = None, zone: str | None = None,
                    actor: str | None = None, target_type: str | None = None,
                    persist: bool = True, **details) -> dict:
    """Log one abnormal event. Safe to call from any async context; never raises."""
    global _incident_seq
    _incident_seq += 1
    if zone is None:
        zone = zone_of_spot(spot or "") or zone_of_gate(gate or "")
    entry = {
        "id": _incident_seq,
        "type": kind,
        "severity": severity if severity in SEVERITY_ORDER else "warning",
        "reason": reason,
        "plate": plate,
        "spot": spot,
        "gate": gate,
        "zone": zone,
        "actor": actor,
        "target_type": target_type,
        "details": details or None,
        "at": time.time(),
        "at_text": sim_now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    incidents.append(entry)
    incident_counts[kind] += 1
    print(f"[INCIDENT] {severity.upper()} {kind}: {reason}"
          + (f" (plate={plate})" if plate else "") + (f" (spot={spot})" if spot else ""))
    if persist:
        try:
            asyncio.get_running_loop()
            asyncio.create_task(asyncio.to_thread(_persist_incident, entry))
        except RuntimeError:
            pass  # no event loop (unit test / import time): the in-memory ring is enough
    return entry


# =================================================================
# REQUEST INTEGRITY GUARD
# =================================================================
#
# The parking network is not trusted. Every webhook is checked for:
#   malformed      not JSON, or not an object, or no EventClass
#   unsigned       no Signature while signatures are required
#   tampered       Signature does not match the payload's own fields
#   duplicate      an EventId we have already accepted (retry or replay attack)
#   replay         a SequenceId at or below one we already accepted
#   unknown_target names a spot, gate or zone that does not exist in this park
#   stale          ServerDateTime far behind the simulator clock we last saw
#   flood          one source sending far more than the simulator ever would
#
# "duplicate" and the other rejects are dropped but always counted and shown on
# the admin page. A sequence "gap" is only a warning: a missed event cannot be
# fetched again, so the payload is still processed.

INTEGRITY_SEEN_EVENTS = 8000          # EventIds remembered for duplicate detection
INTEGRITY_CLOCK_SKEW_SECONDS = 900.0  # 15 simulated minutes behind
INTEGRITY_RATE_WINDOW = 10.0          # seconds
INTEGRITY_RATE_LIMIT = 600            # webhooks per source per window (the sim sends far fewer)

_seen_event_ids: dict[str, float] = {}
_seen_event_order: deque[str] = deque()
_last_sequence_id: int | None = None
_source_hits: dict[str, deque] = defaultdict(deque)

integrity_stats: Counter = Counter()
# Every rejected or suspect request, newest last. Shown on the admin page.
integrity_log: deque[dict] = deque(maxlen=500)
# Duplicated calls specifically, grouped by EventId, with how many copies arrived.
duplicate_calls: dict[str, dict] = {}

# Deliberately permissive: plate formats differ between levels, so this only
# rejects values that cannot be a plate at all (control characters, markup,
# quotes, or an absurd length) rather than enforcing one country's format.
PLATE_RE = re.compile(r"^[^\x00-\x1f<>\"\'\\/;]{1,32}$")


def _normalise(value) -> str:
    return str(value or "").strip()


def _unknown_target_field(payload: dict) -> str | None:
    """Returns the offending field name if the payload names something we do not have."""
    spot = _normalise(payload.get("SpotName"))
    if spot:
        upper = spot.upper()
        known = (spot in parking_spots
                 or upper in {s.upper() for s in ENTRY_SPOTS}
                 or upper.startswith("ENTRY") or upper.startswith("EXIT")
                 or upper in ("LEAVEPARK", "EXIT"))
        if not known:
            return "SpotName"
    name = _normalise(payload.get("Name") or payload.get("ComponentName"))
    if name and payload.get("EventClass") == "gate_action" and name not in ALL_GATES:
        return "Name"
    zone = _normalise(payload.get("ZoneName"))
    if zone and str(payload.get("EventClass") or "").startswith("carbon") and zone.upper() not in ZONES:
        return "ZoneName"
    return None


def _remember_event_id(event_id: str) -> None:
    _seen_event_ids[event_id] = time.time()
    _seen_event_order.append(event_id)
    while len(_seen_event_order) > INTEGRITY_SEEN_EVENTS:
        _seen_event_ids.pop(_seen_event_order.popleft(), None)


def _rate_limited(source: str) -> bool:
    now = time.monotonic()
    hits = _source_hits[source]
    hits.append(now)
    cutoff = now - INTEGRITY_RATE_WINDOW
    while hits and hits[0] < cutoff:
        hits.popleft()
    return len(hits) > INTEGRITY_RATE_LIMIT


def _log_integrity(verdict: str, reason: str, payload: dict, source: str,
                   event_id: str | None, severity: str) -> dict:
    entry = {
        "id": integrity_stats["total"],
        "verdict": verdict,
        "reason": reason,
        "source": source,
        "event_id": event_id,
        "sequence_id": payload.get("SequenceId"),
        "event_class": payload.get("EventClass"),
        "plate": payload.get("CarPlateNumber") or payload.get("CarPlate"),
        "spot": payload.get("SpotName"),
        "server_time": payload.get("ServerDateTime"),
        "severity": severity,
        "at": time.time(),
        "at_text": sim_now().strftime("%Y-%m-%d %H:%M:%S"),
        "payload_digest": hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16],
    }
    integrity_log.append(entry)
    integrity_stats[verdict] += 1
    return entry


def check_request_integrity(body: bytes, source: str) -> tuple[dict, str | None, str | None]:
    """Returns (payload, verdict, reason).

    verdict is None when the payload may be processed; any other verdict means
    the request is rejected and must not touch the car state machine.
    """
    global _last_sequence_id, _last_sim_time
    integrity_stats["total"] += 1

    try:
        payload = json.loads(body)
        # Numbers keep their original JSON text: the simulator signs that text, so
        # 63.564693 must not change through a float round-trip.
        sig_fields = json.loads(body, parse_float=str, parse_int=str)
    except ValueError:
        payload = {"raw": body[:500].decode(errors="replace")}
        entry = _log_integrity("malformed", "body is not valid JSON", payload, source, None, "critical")
        record_incident("integrity_rejected", "Malformed webhook body (not JSON)",
                        severity="critical", source=source, digest=entry["payload_digest"])
        return payload, "malformed", "not valid JSON"

    if not isinstance(payload, dict):
        payload = {"raw": payload}
        _log_integrity("malformed", "payload is not a JSON object", payload, source, None, "critical")
        record_incident("integrity_rejected", "Webhook payload is not a JSON object",
                        severity="critical", source=source)
        return payload, "malformed", "not a JSON object"

    raw_event_id = payload.get("EventId")
    event_id = str(raw_event_id) if raw_event_id not in (None, "") else None

    if _rate_limited(source):
        _log_integrity("flood", f"more than {INTEGRITY_RATE_LIMIT} calls in "
                                f"{INTEGRITY_RATE_WINDOW:.0f}s", payload, source, event_id, "critical")
        record_incident("integrity_rejected", f"Source {source} is flooding the webhook endpoint",
                        severity="critical", source=source, limit=INTEGRITY_RATE_LIMIT)
        return payload, "flood", "rate limit exceeded"

    if not payload.get("EventClass"):
        _log_integrity("malformed", "no EventClass", payload, source, event_id, "warning")
        record_incident("integrity_rejected", "Webhook without EventClass", severity="warning",
                        source=source)
        return payload, "malformed", "no EventClass"

    # --- tamper check ---------------------------------------------------
    settings = get_settings()
    unsigned = sig_fields.get("Signature") in (None, "")
    if unsigned and not settings.webhook_require_signature:
        # Processed, but counted and visible on the admin integrity page: an
        # unsigned request cannot be proven genuine.
        _log_integrity("unsigned_accepted", "no Signature (accepted: signatures not required)",
                       payload, source, event_id, "warning")
    must_check = settings.webhook_verify_signature and (settings.webhook_require_signature or not unsigned)
    if must_check:
        from app.services.webhook_handlers import signature_is_valid
        if unsigned:
            # Only reached when WEBHOOK_REQUIRE_SIGNATURE is on. The shipped simulator
            # sends Signature: null on every webhook (see docs/LEVEL1.md), so the
            # default is off and unsigned traffic is flagged below instead of dropped.
            _log_integrity("unsigned", "no Signature field", payload, source, event_id, "critical")
            record_incident("integrity_rejected", "Unsigned webhook rejected", severity="critical",
                            plate=payload.get("CarPlateNumber"), source=source,
                            event_class=payload.get("EventClass"))
            return payload, "unsigned", "missing signature"
        if not signature_is_valid(sig_fields):
            _log_integrity("tampered", "Signature does not match the payload", payload, source,
                           event_id, "critical")
            record_incident("integrity_rejected", "Tampered webhook: signature mismatch",
                            severity="critical", plate=payload.get("CarPlateNumber"), source=source,
                            event_class=payload.get("EventClass"))
            return payload, "tampered", "bad signature"

    # --- duplicate / replay ---------------------------------------------
    if event_id is not None and event_id in _seen_event_ids:
        record = duplicate_calls.setdefault(event_id, {
            "event_id": event_id,
            "event_class": payload.get("EventClass"),
            "plate": payload.get("CarPlateNumber") or payload.get("CarPlate"),
            "spot": payload.get("SpotName"),
            "copies": 1,
            "first_seen_text": _utc_from_timestamp(
                _seen_event_ids[event_id]).strftime("%Y-%m-%d %H:%M:%S"),
            "sources": [],
        })
        record["copies"] += 1
        record["last_seen_text"] = sim_now().strftime("%Y-%m-%d %H:%M:%S")
        if source not in record["sources"]:
            record["sources"].append(source)
        _log_integrity("duplicate", f"EventId {event_id} already processed "
                                    f"({record['copies']} copies)", payload, source, event_id, "warning")
        record_incident("integrity_rejected",
                        f"Duplicated call: EventId {event_id} seen {record['copies']} times",
                        severity="warning", plate=record["plate"], spot=record["spot"],
                        source=source, copies=record["copies"], event_class=record["event_class"])
        return payload, "duplicate", "duplicate EventId"

    # --- replay of an old batch ------------------------------------------
    server_dt = parse_sim_time(payload.get("ServerDateTime"))
    if server_dt and _last_sim_time is not None:
        skew = (server_dt - _last_sim_time).total_seconds()
        if skew < -INTEGRITY_CLOCK_SKEW_SECONDS:
            # Reloading a level rewinds the simulator clock, so a big jump backwards
            # is normal and must not stop the park. Replays are caught by the EventId
            # and SequenceId checks instead; this is recorded, not rejected.
            _log_integrity("clock_jump", f"ServerDateTime is {abs(skew):.0f}s behind the "
                                         f"previous webhook", payload, source, event_id, "warning")
            record_incident("integrity_warning",
                            "Simulator clock jumped backwards (level reload?); re-baselining",
                            severity="warning", plate=payload.get("CarPlateNumber"),
                            source=source, skew_seconds=round(skew, 1))

    if server_dt:
        _last_sim_time = server_dt

    # --- unknown target ---------------------------------------------------
    # The map is discovered at runtime and can change between levels, so an
    # unrecognised name is reported but still processed: dropping it would blind
    # the backend to a spot it has simply not learned about yet.
    bad_field = _unknown_target_field(payload)
    if bad_field:
        _log_integrity("unknown_target",
                       f"{bad_field}={payload.get(bad_field)!r} is not part of the known map",
                       payload, source, event_id, "warning")
        record_incident("integrity_warning",
                        f"Webhook names an unknown {bad_field}: {payload.get(bad_field)!r}",
                        severity="warning", source=source, field=bad_field)

    plate = payload.get("CarPlateNumber") or payload.get("CarPlate")
    if plate is not None and str(plate) and not PLATE_RE.match(str(plate)):
        _log_integrity("invalid_field", f"CarPlateNumber {plate!r} is not a usable plate", payload,
                       source, event_id, "critical")
        record_incident("integrity_rejected", f"Webhook with an invalid plate {plate!r}",
                        severity="critical", source=source)
        return payload, "invalid_field", "invalid plate"

    # --- accepted: remember it, then check ordering ------------------------
    if event_id is not None:
        _remember_event_id(event_id)

    try:
        sequence_id = int(payload["SequenceId"])
    except (KeyError, TypeError, ValueError):
        sequence_id = None
    if sequence_id is not None:
        if _last_sequence_id is not None and sequence_id <= _last_sequence_id:
            _log_integrity("out_of_order", f"SequenceId {sequence_id} is not newer than "
                                           f"{_last_sequence_id}", payload, source, event_id, "warning")
            record_incident("integrity_warning",
                            f"Out-of-order SequenceId {sequence_id} (last {_last_sequence_id}); "
                            f"processed but flagged",
                            severity="warning", plate=plate, source=source)
        elif _last_sequence_id is not None and sequence_id > _last_sequence_id + 1:
            missed = sequence_id - _last_sequence_id - 1
            _log_integrity("gap", f"{missed} event(s) missed before SequenceId {sequence_id}",
                           payload, source, event_id, "warning")
            record_incident("integrity_warning", f"{missed} simulator event(s) were never delivered",
                            severity="warning", source=source, missed=missed,
                            last=_last_sequence_id, got=sequence_id)
        _last_sequence_id = max(sequence_id, _last_sequence_id or sequence_id)

    integrity_stats["accepted"] += 1
    return payload, None, None


# =================================================================
# ORDERED EVENT DISPATCHER
# =================================================================
#
# Simulator events arrive in bursts (everybody leaves at once after a CO alert).
# Handling them with a bare create_task loses ordering: a car's CarOut can be
# processed before its CarIn, which corrupts the spot state.
#
# dispatch() keeps ONE FIFO queue per key (the plate, or the spot for events
# with no plate). Work for the same key runs strictly in arrival order; work for
# different keys runs concurrently. Enqueueing is synchronous, so the order a
# webhook arrives in is the order it is queued in.

DISPATCH_MAX_PER_KEY = 250    # one car should never have this many pending events

_dispatch_queues: dict[str, deque] = {}
_dispatch_workers: dict[str, asyncio.Task] = {}
dispatch_stats: Counter = Counter()
_dispatch_peak_depth = 0


async def _dispatch_drain(key: str) -> None:
    queue = _dispatch_queues.get(key)
    try:
        while queue:
            factory, label = queue.popleft()
            try:
                await factory()
                dispatch_stats["completed"] += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                dispatch_stats["failed"] += 1
                print(f"[DISPATCH ERROR] {label} for {key}: {exc}")
                record_incident("integrity_warning", f"Event handler failed: {label}",
                                severity="warning", error=str(exc)[:200], key=key)
    finally:
        _dispatch_queues.pop(key, None)
        _dispatch_workers.pop(key, None)


def dispatch(key: str, factory, label: str = "event") -> bool:
    """Queue async work for `key`, preserving arrival order. False if it was dropped."""
    global _dispatch_peak_depth
    key = key or "global"
    queue = _dispatch_queues.setdefault(key, deque())
    if len(queue) >= DISPATCH_MAX_PER_KEY:
        dispatch_stats["dropped"] += 1
        record_incident("integrity_warning",
                        f"Event backlog for {key} exceeded {DISPATCH_MAX_PER_KEY}; dropped {label}",
                        severity="critical", key=key)
        return False
    queue.append((factory, label))
    dispatch_stats["queued"] += 1
    _dispatch_peak_depth = max(_dispatch_peak_depth, len(queue))
    if key not in _dispatch_workers:
        _dispatch_workers[key] = asyncio.create_task(_dispatch_drain(key))
    return True


def dispatch_depth() -> int:
    return sum(len(q) for q in _dispatch_queues.values())


# =================================================================
# RESPONSE CACHE  (dashboard under load)
# =================================================================
#
# The dashboard polls several endpoints every few seconds, from every open tab.
# Without this, 20 tabs = 20 simulator round-trips per endpoint per tick, and
# each list-* call has a simulated operating cost.
#
# Each entry is computed at most once per TTL, and concurrent callers wait on
# the SAME computation (single flight) instead of each starting their own.

class TTLCache:
    def __init__(self) -> None:
        self._values: dict[str, tuple[float, object]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.hits = 0
        self.misses = 0

    async def get(self, key: str, ttl: float, producer):
        hit = self._values.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            self.hits += 1
            return hit[1]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self._values.get(key)      # another caller may have filled it while we waited
            if hit and time.monotonic() - hit[0] < ttl:
                self.hits += 1
                return hit[1]
            self.misses += 1
            value = await producer()
            self._values[key] = (time.monotonic(), value)
            return value

    def invalidate(self, key: str) -> None:
        self._values.pop(key, None)

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {"hits": self.hits, "misses": self.misses, "entries": len(self._values),
                "hit_rate": round(self.hits / total, 3) if total else 0.0}


response_cache = TTLCache()

CACHE_TTL_SPOTS = 2.0     # /list-parking-spots, the heaviest dashboard call
CACHE_TTL_STATUS = 2.0    # /system-status
CACHE_TTL_SIM_LIST = 5.0  # simulator passthrough lists


def parse_sim_time(value) -> datetime | None:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


_last_sim_time: datetime | None = None


# =================================================================
# SPOT SENSOR HEALTH & MAINTENANCE MODE
# =================================================================
#
# A spot sensor that lies costs real capacity: a stuck "occupied" blocks a good
# spot forever, and a stuck "free" sends a second car into an occupied one
# (which the simulator penalises). We watch each spot's own event stream and
# quarantine it as soon as it stops making sense, rather than waiting for the
# simulator's own alarm.

SENSOR_FLAP_WINDOW = 60.0            # seconds
SENSOR_FLAP_LIMIT = 6                # state changes in the window before we distrust it
SENSOR_QUARANTINE_SECONDS = 300.0    # auto maintenance is reviewed after this long
SENSOR_STUCK_SECONDS = 1800.0        # "occupied" with no vehicle for this long is a stuck sensor

# name -> {"transitions": deque, "faults", "last_direction", "last_plate", "last_at", "last_fault"}
spot_sensor: dict[str, dict] = {}
# name -> {"reason", "by", "since", "auto"}  — spots withdrawn from allocation
maintenance_mode: dict[str, dict] = {}


def _sensor(name: str) -> dict:
    return spot_sensor.setdefault(name, {
        "transitions": deque(maxlen=SENSOR_FLAP_LIMIT * 2),
        "faults": 0, "last_direction": None, "last_plate": None,
        "last_at": 0.0, "last_fault": None,
    })


def spot_is_serviceable(name: str) -> bool:
    """A spot may be allocated only if it is healthy and not in maintenance."""
    if name in maintenance_mode:
        return False
    health = component_health.get(name, {})
    return not health.get("broken") and not health.get("under_maintenance")


async def set_maintenance_mode(name: str, on: bool, *, reason: str, actor: str | None = None,
                               auto: bool = False) -> dict:
    """Withdraw a spot from availability (or return it). Audited either way."""
    if on:
        maintenance_mode[name] = {
            "reason": reason,
            "by": actor or ("automatic" if auto else "operator"),
            "since": time.time(),
            "since_text": sim_now().strftime("%Y-%m-%d %H:%M:%S"),
            "auto": auto,
        }
        async with spot_lock:
            if name in parking_spots:
                parking_spots[name] = False
        record_incident("spot_maintenance", f"{name} taken out of service: {reason}",
                        severity="warning", spot=name, actor=actor, target_type="spot", auto=auto)
    else:
        previous = maintenance_mode.pop(name, None)
        health = component_health.get(name, {})
        if name in parking_spots and not health.get("broken") and not health.get("under_maintenance"):
            detail = spot_details.get(name, {})
            occupied = detail.get("isOccupied", False) or bool(detail.get("detectedCars"))
            async with spot_lock:
                assigned = any(c.get("assigned_spot") == name for c in active_cars.values())
                parking_spots[name] = not (occupied or assigned)
        _sensor(name)["faults"] = 0
        record_incident("spot_maintenance", f"{name} returned to service", severity="info",
                        spot=name, actor=actor, target_type="spot",
                        was=(previous or {}).get("reason"))
    response_cache.invalidate("spots")
    return {"spot": name, "maintenance": on, "reason": reason}


def note_sensor_reading(name: str, direction: str, plate: str | None, believed_free: bool) -> str | None:
    """Record one spot sensor event; returns a fault description, or None if it looks sane."""
    if name not in parking_spots:
        return None
    sensor = _sensor(name)
    now = time.time()
    fault = None

    if direction == "CarIn" and not plate:
        fault = "occupancy reported without a vehicle identity"
    elif direction == "CarIn" and sensor["last_direction"] == "CarIn" and sensor["last_plate"] != plate:
        fault = (f"second CarIn without a CarOut "
                 f"(had {sensor['last_plate'] or 'unknown'}, now {plate or 'unknown'})")
    elif direction == "CarOut" and believed_free and sensor["last_direction"] != "CarIn":
        fault = "CarOut from a spot the sensor never reported occupied"

    if direction in ("CarIn", "CarOut"):
        sensor["transitions"].append(now)
        window = [t for t in sensor["transitions"] if now - t <= SENSOR_FLAP_WINDOW]
        if len(window) >= SENSOR_FLAP_LIMIT:
            fault = fault or f"sensor flapping: {len(window)} changes in {SENSOR_FLAP_WINDOW:.0f}s"
        sensor["last_direction"] = direction
        sensor["last_plate"] = plate
        sensor["last_at"] = now

    if fault:
        sensor["faults"] += 1
        sensor["last_fault"] = fault
    return fault


async def handle_sensor_fault(name: str, fault: str) -> None:
    """One abnormal reading is noted; a repeat takes the spot out of service."""
    sensor = _sensor(name)
    record_incident("sensor_abnormal", f"{name}: {fault}", severity="warning", spot=name,
                    target_type="spot", faults=sensor["faults"])
    if sensor["faults"] >= 2 and name not in maintenance_mode:
        await set_maintenance_mode(name, True, reason=f"sensor abnormality ({fault})", auto=True)
        try:
            await call_simulator_api(f"parking-spots/{name}/repair", method="POST")
            await audit_system("AUTO_REPAIR", "spot", name, reason="sensor abnormality")
        except Exception as exc:
            print(f"[SENSOR] Repair request for {name} failed: {exc}")


async def sensor_review_worker() -> None:
    """Return auto-quarantined spots to service once the simulator says they are healthy,
    and quarantine spots reported occupied by nobody for far too long."""
    print("[SENSOR REVIEW] Spot sensor supervisor started.")
    while True:
        await asyncio.sleep(30.0)
        now = time.time()
        try:
            for name, entry in list(maintenance_mode.items()):
                if not entry.get("auto") or now - entry["since"] < SENSOR_QUARANTINE_SECONDS:
                    continue
                health = component_health.get(name, {})
                detail = spot_details.get(name, {})
                if health.get("broken") or health.get("under_maintenance") or detail.get("broken"):
                    continue
                if now - _sensor(name)["last_at"] < SENSOR_FLAP_WINDOW:
                    continue   # still moving; give it another cycle
                await set_maintenance_mode(name, False, reason="sensor stable again", auto=True)

            for name, detail in list(spot_details.items()):
                if name not in parking_spots or name in maintenance_mode:
                    continue
                if not (detail.get("isOccupied", False) or detail.get("detectedCars")):
                    continue
                known = any(c.get("parked_spot") == name or c.get("assigned_spot") == name
                            for c in active_cars.values())
                sensor = _sensor(name)
                if not known and sensor["last_at"] and now - sensor["last_at"] > SENSOR_STUCK_SECONDS:
                    sensor["faults"] += 1
                    await handle_sensor_fault(
                        name, f"occupied with no vehicle for {SENSOR_STUCK_SECONDS / 60:.0f} minutes")
        except Exception as exc:
            print(f"[SENSOR REVIEW ERROR] {exc}")


# =================================================================
# VEHICLE LOCATION & DOUBLE PARKING
# =================================================================
#
# vehicle_tracks answers "where is this car?", including cars that ignored their
# assignment. It is built from the sensor stream, so it also catches a plate
# holding two spots at once (double parking) as soon as the second CarIn
# arrives — before the simulator issues its penalty.

# plate -> {"spots": {name: since}, "last_seen", "last_event", "last_spot", "zone", "history"}
vehicle_tracks: dict[str, dict] = {}
double_parking: dict[str, dict] = {}   # plate (or "spot:<name>") -> open warning


def _track(plate: str) -> dict:
    return vehicle_tracks.setdefault(plate, {
        "spots": {}, "last_seen": 0.0, "last_event": None, "last_spot": None,
        "zone": None, "history": deque(maxlen=25),
    })


def note_vehicle_position(plate: str, spot: str | None, event: str, at_text: str = "") -> None:
    if not plate:
        return
    track = _track(plate)
    track["last_seen"] = time.time()
    track["last_event"] = event
    if spot:
        track["last_spot"] = spot
        track["zone"] = zone_of_spot(spot) or zone_of_gate(gate_for_entry_spot(spot)) or track["zone"]
    track["history"].append({
        "event": event, "spot": spot,
        "at": at_text or sim_now().strftime("%Y-%m-%d %H:%M:%S"),
    })


async def note_vehicle_parked(plate: str, spot: str) -> None:
    """Car detected in `spot`. Raises an early warning if it already holds another one."""
    if not plate or spot not in parking_spots:
        return
    track = _track(plate)
    others = [s for s in track["spots"] if s != spot]
    track["spots"][spot] = time.time()
    note_vehicle_position(plate, spot, "parked")

    assigned = (active_cars.get(plate) or {}).get("assigned_spot")
    if assigned and assigned != spot and assigned in parking_spots:
        record_incident("vehicle_misparked",
                        f"{plate} parked in {spot} but was assigned {assigned}",
                        severity="warning", plate=plate, spot=spot, assigned=assigned)
        async with spot_lock:
            still_held = any(c.get("parked_spot") == assigned for c in active_cars.values())
            if not still_held and assigned not in maintenance_mode:
                parking_spots[assigned] = True   # give the abandoned reservation back
        response_cache.invalidate("spots")

    if others:
        held = sorted([*others, spot])
        warning = {
            "plate": plate,
            "spots": held,
            "zones": sorted({zone_of_spot(s) or "?" for s in held}),
            "since": time.time(),
            "since_text": sim_now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        double_parking[plate] = warning
        record_incident("double_parking",
                        f"{plate} occupies {len(held)} spots at once: {', '.join(held)}",
                        severity="critical", plate=plate, spot=spot,
                        spots=held, zones=warning["zones"])


def note_vehicle_left_spot(plate: str, spot: str) -> None:
    if not plate:
        return
    track = _track(plate)
    track["spots"].pop(spot, None)
    note_vehicle_position(plate, spot, "left spot")
    if plate in double_parking and len(track["spots"]) <= 1:
        double_parking.pop(plate, None)
        record_incident("double_parking", f"{plate} is no longer double parked", severity="info",
                        plate=plate, spot=spot)


def note_double_parked_spot(name: str, detected: int) -> None:
    """The simulator reports more than one car in one spot."""
    key = f"spot:{name}"
    if key in double_parking:
        return
    double_parking[key] = {
        "spot": name, "cars": detected, "spots": [name],
        "zones": [zone_of_spot(name) or "?"], "since": time.time(),
        "since_text": sim_now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    record_incident("double_parking", f"{name} reports {detected} vehicles in one spot",
                    severity="critical", spot=name, detected=detected)


def _sensor_spots_for_plate(plate: str) -> list[str]:
    """Spots the simulator itself says hold this plate (ground truth)."""
    wanted = plate.upper()
    found = []
    for name, detail in spot_details.items():
        if str(detail.get("carPlateNumber") or "").upper() == wanted:
            found.append(name)
            continue
        cars = detail.get("detectedCars") or []
        if isinstance(cars, list) and any(str(c).upper() == wanted for c in cars):
            found.append(name)
    return sorted(found)


def locate_vehicle(plate: str) -> dict:
    """Best known position of one car, with the evidence behind it.

    Works for cars that never reached their assigned spot: the answer says what
    the assignment was and where the car actually is.
    """
    plate = (plate or "").strip()
    track = vehicle_tracks.get(plate) or next(
        (t for p, t in vehicle_tracks.items() if p.upper() == plate.upper()), None)
    car = active_cars.get(plate) or next(
        (c for p, c in active_cars.items() if p.upper() == plate.upper()), None)

    occupied = sorted((track or {}).get("spots", {}))
    for name in _sensor_spots_for_plate(plate):     # sensor truth beats our bookkeeping
        if name not in occupied:
            occupied.append(name)

    assigned = (car or {}).get("assigned_spot")
    parked = (car or {}).get("parked_spot")
    where = parked or (occupied[0] if occupied else None)

    if where:
        state = "parked"
        confidence = "confirmed" if parked or occupied else "sensor"
    elif car and car.get("charged"):
        state, confidence = "at exit", "tracked"
        where = (track or {}).get("last_spot")
    elif car:
        state, confidence = "driving to spot", "assigned"
        where = assigned
    elif track:
        state, confidence = "last seen", "historic"
        where = track.get("last_spot")
    else:
        return {"found": False, "plate": plate,
                "message": f"No live position for {plate}; try the vehicle history."}

    zone = zone_of_spot(where or "") or (track or {}).get("zone")
    history = list((track or {}).get("history", []))
    return {
        "found": True,
        "plate": plate,
        "state": state,
        "confidence": confidence,
        "location": where or "unknown",
        "zone": zone,
        "zone_label": (ZONES.get(zone or "") or {}).get("label"),
        "assigned_spot": assigned,
        "in_assigned_spot": bool(where and assigned and where == assigned),
        "occupies": occupied,
        "double_parked": len(occupied) > 1,
        "vehicle_type": (car or {}).get("car_type"),
        "charged": bool((car or {}).get("charged")),
        "entry_time": (car or {}).get("entry_time"),
        "last_seen_text": history[-1]["at"] if history else None,
        "history": history[-10:],
    }


# =================================================================
# PAYMENT VERIFICATION
# =================================================================
#
# The simulator can emit payment events we never asked for, for amounts we never
# charged. A visit is settled only when the simulator ACCEPTS our own /charge
# call for the amount we calculated. Anything else is suspicious: we void what we
# had, raise an incident, and ask for payment again.

MAX_PAYMENT_ATTEMPTS = 3
PAYMENT_RETRY_BACKOFF = 1.0
PAYMENT_AMOUNT_TOLERANCE = 0.01

# plate -> {"expected", "parking", "charging", "attempts", "confirmed", "suspicions": [...]}
payment_state: dict[str, dict] = {}


def _payment(plate: str) -> dict:
    return payment_state.setdefault(plate, {
        "expected": 0.0, "parking": 0.0, "charging": 0.0, "attempts": 0,
        "confirmed": False, "suspicions": [], "last_attempt": 0.0,
    })


def payment_is_settled(plate: str) -> bool:
    return bool(payment_state.get(plate, {}).get("confirmed")) or plate in charged_cars


async def charge_with_verification(plate: str, parking_cost: float, charging_cost: float,
                                   reason: str = "exit", minutes: float = 0.0,
                                   at: datetime | None = None) -> bool:
    """Charge a car and only treat it as paid once the simulator accepts the call.

    Retries a failed charge, and is a no-op if the visit is already settled (the
    simulator penalises a second charge for the same visit).
    """
    state = _payment(plate)
    if state["confirmed"]:
        print(f"[PAYMENT] {plate} already settled; not charging again.")
        return True

    expected = round(float(parking_cost) + float(charging_cost), 2)
    state.update(expected=expected, parking=float(parking_cost), charging=float(charging_cost))

    for attempt in range(1, MAX_PAYMENT_ATTEMPTS + 1):
        state["attempts"] += 1
        state["last_attempt"] = time.time()
        try:
            await api_charge_car(plate, parking_cost, charging_cost)
        except Exception as exc:
            print(f"[PAYMENT] Charge attempt {attempt}/{MAX_PAYMENT_ATTEMPTS} for {plate} failed: {exc}")
            if attempt == MAX_PAYMENT_ATTEMPTS:
                record_incident("payment_suspicious",
                                f"Charge for {plate} failed {MAX_PAYMENT_ATTEMPTS} times: {exc}",
                                severity="critical", plate=plate, expected=expected, cause=reason)
                return False
            record_incident("payment_retry",
                            f"Asking {plate} for payment again (attempt {attempt + 1}): {exc}",
                            severity="warning", plate=plate, expected=expected, attempt=attempt)
            await asyncio.sleep(PAYMENT_RETRY_BACKOFF * attempt)
            continue

        state["confirmed"] = True
        charged_cars.add(plate)
        if plate in active_cars:
            active_cars[plate]["charged"] = True
        record_incident("payment_settled", f"{plate} paid {expected:.2f} ({reason})",
                        severity="info", plate=plate, parking=float(parking_cost),
                        charging=float(charging_cost), attempts=state["attempts"])
        car_type = normalize_vehicle_type((active_cars.get(plate) or {}).get("car_type", "Normal"))
        asyncio.create_task(store_charge_async(plate, float(parking_cost), float(charging_cost),
                                               minutes, car_type, at))
        return True
    return False


async def review_payment_event(payload: dict) -> None:
    """A `payment_made` webhook arrived. Accept it only if it matches our own charge."""
    plate = payload.get("CarPlateNumber") or payload.get("CarPlate") or ""
    if not plate:
        record_incident("payment_suspicious", "Payment event without a plate", severity="warning")
        return
    state = payment_state.get(plate)
    try:
        amount = round(float(payload.get("Amount")), 2)
    except (TypeError, ValueError):
        amount = None

    if state is None or not state["attempts"]:
        record_incident("payment_suspicious",
                        f"Payment event for {plate}, which we never charged",
                        severity="critical", plate=plate, amount=amount)
        return
    if amount is None:
        suspicion = "payment event without a usable Amount"
    elif abs(amount - state["expected"]) > PAYMENT_AMOUNT_TOLERANCE:
        suspicion = f"paid {amount:.2f} but we charged {state['expected']:.2f}"
    elif state.get("settled_event"):
        suspicion = "second payment event for the same visit"
    else:
        state["settled_event"] = True
        record_incident("payment_settled", f"Payment event for {plate} matches our charge",
                        severity="info", plate=plate, amount=amount)
        return

    state["suspicions"].append(suspicion)
    state["confirmed"] = False
    charged_cars.discard(plate)
    if plate in active_cars:
        active_cars[plate]["charged"] = False
    record_incident("payment_suspicious", f"{plate}: {suspicion}", severity="critical",
                    plate=plate, amount=amount, expected=state["expected"])

    if state["attempts"] < MAX_PAYMENT_ATTEMPTS:
        record_incident("payment_retry",
                        f"Asking {plate} for payment again after a suspicious payment",
                        severity="warning", plate=plate)
        await charge_with_verification(plate, state["parking"], state["charging"],
                                       reason="suspicious payment")
    else:
        record_incident("payment_suspicious",
                        f"{plate} exhausted {MAX_PAYMENT_ATTEMPTS} payment attempts; held at exit",
                        severity="critical", plate=plate)


# =================================================================
# GATE FAILURE TRACKING
# =================================================================
#
# A gate that keeps refusing commands is treated as failed even when no
# component_broken webhook arrived, so its zone's traffic moves to another gate
# instead of piling up behind it. One command is let through periodically to
# probe whether it has recovered.

GATE_FAILURE_LIMIT = 3
GATE_PROBE_SECONDS = 120.0

gate_failures: dict[str, dict] = {}   # gate -> {"count", "since", "reason", "last"}


def note_gate_failure(gate: str, reason: str) -> None:
    entry = gate_failures.setdefault(gate, {"count": 0, "since": time.time(), "reason": reason})
    entry["count"] += 1
    entry["reason"] = reason
    entry["last"] = time.time()
    if entry["count"] == GATE_FAILURE_LIMIT:
        record_incident("gate_failover", f"{gate} failed {entry['count']} times: {reason}",
                        severity="critical", gate=gate, target_type="gate")


def note_gate_success(gate: str) -> None:
    if gate_failures.pop(gate, None):
        record_incident("gate_failover", f"{gate} is answering again", severity="info",
                        gate=gate, target_type="gate")


def gate_presumed_failed(gate: str) -> bool:
    entry = gate_failures.get(gate)
    if not entry or entry["count"] < GATE_FAILURE_LIMIT:
        return False
    if time.time() - entry.get("last", 0) > GATE_PROBE_SECONDS:
        entry["count"] = GATE_FAILURE_LIMIT - 1   # let one command through to probe it
        return False
    return True


# -------------------------------------------------
# RUNTIME LAYOUT DISCOVERY
# -------------------------------------------------
#
# ZONE_DEFS above is only the Level 2 fallback. Every level has a different map
# (Level 3 has more zones, more spots and more gates), so the real layout is read
# from the simulator at startup:
#
#   GET /list-parking-spots -> name, purpose (Park/EntrySpot/ExitSpot),
#                              parkingForCarType, zoneParent
#   GET /list-barriers      -> name, zoneParent, state
#
# Spots and gates are grouped by zoneParent. Within a zone, gates are paired with
# that zone's EntrySpots first and ExitSpots second, in name order — the same
# convention Level 2 uses (gate1/ENTRY1, gate2/EXIT1). ENTRY_GATE / EXIT_GATE in
# .env override the pairing for any gate they name, but only once they are set to
# something other than their Level 2 defaults (see build_zone_defs).
#
# Gates with no zone of their own (Level 3's gate7 and gate19) are not part of the
# car routing; they are listed in OTHER_GATES and handled by ALWAYS_OPEN_GATES.

# Gates held open from startup and never auto-closed. Comma-separated in .env as
# ALWAYS_OPEN_GATES; these are the Level 3 through-gates.
ALWAYS_OPEN_GATES: list[str] = [
    g.strip() for g in str(getattr(get_settings(), "always_open_gates", "gate7,gate19")).split(",")
    if g.strip()
]

OTHER_GATES: list[str] = []
layout_source: str = "static (Level 2 fallback)"
layout_discovered_at: str | None = None


def _numeric_key(name: str) -> tuple:
    """Sort gate7 before gate19 (and ENTRY2 before ENTRY10)."""
    digits = "".join(ch for ch in name if ch.isdigit())
    return (int(digits) if digits else 9999, name)


def _zone_label(zone_id: str, index: int) -> str:
    match = re.match(r"^zone\s*(\d+)$", zone_id.strip(), re.IGNORECASE)
    return f"Zone {match.group(1)}" if match else (zone_id.title() if zone_id else f"Zone {index}")


def build_zone_defs(sim_spots: list[dict], sim_barriers: list[dict]) -> list[dict]:
    """Turn the simulator's own spot and barrier lists into ZONE_DEFS."""
    settings = get_settings()
    # ENTRY_GATE / EXIT_GATE default to the Level 2 gate names. Applying those to a
    # different level would mis-label its gates, so they only override the pairing
    # when they were actually set (in .env or the environment).
    def _configured(field: str) -> set[str]:
        value = str(getattr(settings, field, "") or "")
        if value == Settings.model_fields[field].default:
            return set()
        return {g.strip() for g in value.split(",") if g.strip()}

    forced_entry = _configured("entry_gate")
    forced_exit = _configured("exit_gate")

    # dict, not list: the simulator may repeat a name, and a spot must be counted once.
    park: dict[str, dict[str, None]] = defaultdict(dict)
    entries: dict[str, dict[str, None]] = defaultdict(dict)
    exits: dict[str, dict[str, None]] = defaultdict(dict)
    car_types: dict[str, dict[str, str]] = defaultdict(dict)

    for item in sim_spots:
        name = (item or {}).get("name")
        if not name:
            continue
        zone = str(item.get("zoneParent") or "").upper() or "ZONE1"
        purpose = str(item.get("purpose") or "Park")
        if purpose == "EntrySpot":
            entries[zone][name] = None
        elif purpose == "ExitSpot":
            exits[zone][name] = None
        else:
            park[zone][name] = None
            kind = str(item.get("parkingForCarType") or "Any")
            if kind in ("Electric", "Accessible"):
                car_types[zone][name] = kind

    gates_by_zone: dict[str, dict[str, None]] = defaultdict(dict)
    zoneless: dict[str, None] = {}
    for item in sim_barriers:
        name = (item or {}).get("name")
        if not name:
            continue
        zone = str(item.get("zoneParent") or "").upper()
        if zone:
            gates_by_zone[zone][name] = None
        else:
            zoneless[name] = None

    zone_ids = sorted(set(park) | set(entries) | set(exits) | set(gates_by_zone), key=_numeric_key)
    built: list[dict] = []
    for index, zone_id in enumerate(zone_ids, start=1):
        spots = sorted(park.get(zone_id, {}), key=_numeric_key)
        if not spots:
            continue  # a zone with no parking spots is not a parking zone
        zone_entries = sorted(entries.get(zone_id, {}), key=_numeric_key)
        zone_exits = sorted(exits.get(zone_id, {}), key=_numeric_key)
        gates = sorted(gates_by_zone.get(zone_id, {}), key=_numeric_key)

        # .env may name a gate explicitly; otherwise the first gates serve the
        # EntrySpots and the rest serve the ExitSpots, both in name order.
        named_entry = [g for g in gates if g in forced_entry]
        named_exit = [g for g in gates if g in forced_exit]
        rest = [g for g in gates if g not in forced_entry and g not in forced_exit]
        wanted_entrances = max(len(zone_entries) - len(named_entry), 0)
        entrance_gates = named_entry + rest[:wanted_entrances]
        exit_gates = named_exit + rest[wanted_entrances:]

        built.append({
            "id": zone_id,
            "label": _zone_label(zone_id, index),
            "spots": spots,
            "entrances": entrance_gates,
            "exits": exit_gates,
            "entry_spots": {g: zone_entries[i] if i < len(zone_entries) else (zone_entries[-1] if zone_entries else "ENTRY1")
                            for i, g in enumerate(entrance_gates)},
            "exit_spots": {g: zone_exits[i] if i < len(zone_exits) else (zone_exits[-1] if zone_exits else "EXIT1")
                           for i, g in enumerate(exit_gates)},
            "car_types": car_types.get(zone_id, {}),
        })

    globals()["OTHER_GATES"] = sorted(zoneless, key=_numeric_key)
    return built


def apply_zone_defs(new_defs: list[dict], source: str) -> None:
    """Replace the zone registry and every table derived from it, in place.

    Called once at startup. Mutating the existing dicts (rather than rebinding
    them) keeps any module-level reference already handed out valid.
    """
    global ZONE_DEFS, ZONES, VALID_PARKING_SPOTS, SPOT_ZONE, ZONE_CAPACITY
    global ENTRANCE_GATES, EXIT_GATES, ALL_GATES, GATE_ZONE
    global GATE_ENTRY_SPOT, GATE_EXIT_SPOT, ENTRY_SPOT_GATE, EXIT_SPOT_GATE, ENTRY_SPOTS
    global SPOT_CAR_TYPES, layout_source, layout_discovered_at

    ZONE_DEFS = new_defs
    ZONES = {z["id"]: z for z in ZONE_DEFS}
    VALID_PARKING_SPOTS = [s for z in ZONE_DEFS for s in z["spots"]]
    SPOT_ZONE = {s: z["id"] for z in ZONE_DEFS for s in z["spots"]}
    ZONE_CAPACITY = {z["id"]: len(z["spots"]) for z in ZONE_DEFS}
    ENTRANCE_GATES = [g for z in ZONE_DEFS for g in z["entrances"]]
    EXIT_GATES = [g for z in ZONE_DEFS for g in z["exits"]]
    ALL_GATES = ENTRANCE_GATES + EXIT_GATES + [g for g in OTHER_GATES
                                               if g not in ENTRANCE_GATES and g not in EXIT_GATES]
    GATE_ZONE = {g: z["id"] for z in ZONE_DEFS for g in z["entrances"] + z["exits"]}
    GATE_ENTRY_SPOT = {g: n for z in ZONE_DEFS for g, n in z["entry_spots"].items()}
    GATE_EXIT_SPOT = {g: n for z in ZONE_DEFS for g, n in z["exit_spots"].items()}
    ENTRY_SPOT_GATE = {n: g for g, n in GATE_ENTRY_SPOT.items()}
    EXIT_SPOT_GATE = {n: g for g, n in GATE_EXIT_SPOT.items()}
    ENTRY_SPOTS = list(ENTRY_SPOT_GATE)
    SPOT_CAR_TYPES = {s: k for z in ZONE_DEFS for s, k in z["car_types"].items()}

    # Runtime state that is keyed by spot / gate / entry spot.
    for name in list(parking_spots):
        if name not in SPOT_ZONE:
            parking_spots.pop(name)
    for name in VALID_PARKING_SPOTS:
        parking_spots.setdefault(name, True)

    for gate in ALL_GATES:
        gate_queues.setdefault(gate, asyncio.Queue())
    for entry_name in ENTRY_SPOTS:
        entry_occupied.setdefault(entry_name, False)
        entry_car.setdefault(entry_name, None)
        entry_occupied_since.setdefault(entry_name, 0.0)
        entry_queues.setdefault(entry_name, asyncio.Queue())

    layout_source = source
    layout_discovered_at = sim_now().strftime("%Y-%m-%d %H:%M:%S")
    response_cache.invalidate("spots")


async def discover_layout() -> bool:
    """Read the current level's map from the simulator. False = keep the fallback."""
    try:
        sim_spots, sim_barriers = await asyncio.gather(
            call_simulator_api("list-parking-spots"),
            call_simulator_api("list-barriers"),
        )
    except Exception as exc:
        print(f"[LAYOUT] Could not read the map from the simulator: {exc}")
        print(f"[LAYOUT] Keeping the built-in fallback ({len(VALID_PARKING_SPOTS)} spots).")
        record_incident("capacity", "Simulator map unavailable; using the built-in fallback layout",
                        severity="warning", error=str(exc)[:200])
        return False

    if not isinstance(sim_spots, list) or not isinstance(sim_barriers, list):
        print("[LAYOUT] Simulator returned no usable spot/barrier list; keeping the fallback.")
        return False

    new_defs = build_zone_defs(sim_spots, sim_barriers)
    if not new_defs:
        print("[LAYOUT] Simulator reported no parking zones; keeping the fallback.")
        return False

    apply_zone_defs(new_defs, source=f"simulator ({len(sim_spots)} spots, {len(sim_barriers)} barriers)")
    print(f"[LAYOUT] Discovered {len(ZONE_DEFS)} zone(s) from the simulator:")
    for zone in ZONE_DEFS:
        print(f"[LAYOUT]   {zone['label']} ({zone['id']}): {len(zone['spots'])} spots "
              f"{zone['spots'][0]}..{zone['spots'][-1]} | entrances {zone['entrances']} "
              f"-> {list(zone['entry_spots'].values())} | exits {zone['exits']} "
              f"-> {list(zone['exit_spots'].values())}")
    if OTHER_GATES:
        print(f"[LAYOUT]   Gates outside the zones: {OTHER_GATES}")
    record_incident("capacity",
                    f"Level map loaded: {len(ZONE_DEFS)} zones, {len(VALID_PARKING_SPOTS)} spots, "
                    f"{len(ALL_GATES)} gates", severity="info",
                    zones=[z["id"] for z in ZONE_DEFS], spots=len(VALID_PARKING_SPOTS))
    return True


async def open_always_open_gates() -> None:
    """Hold the level's through-gates open from startup (Level 3: gate7, gate19).

    They are marked as manual overrides so the automation never closes them again.
    """
    for gate in ALWAYS_OPEN_GATES:
        try:
            set_manual_override(gate)
            await call_simulator_api(f"barrier-gates/{gate}/open", method="POST")
            print(f"[STARTUP] {gate} opened and held open.")
            await audit_system("GATE_HELD_OPEN", "gate", gate, reason="always-open gate")
        except Exception as exc:
            print(f"[STARTUP] Could not open {gate}: {exc}")
            await audit_system("GATE_HELD_OPEN", "gate", gate, success=False, error=str(exc)[:200])
            record_incident("gate_failover", f"{gate} could not be opened at startup: {exc}",
                            severity="warning", gate=gate, target_type="gate")



# -------------------------------------------------
# SIMULATOR AUTH & HTTP HELPERS
# -------------------------------------------------

class LoginRequest(BaseModel):
    email: str = "admin"
    password: str = "admin"


async def api_login(email: str | None = None, password: str | None = None) -> str:
    global SIMULATOR_TOKEN
    email    = email    or SIMULATOR_EMAIL
    password = password or SIMULATOR_PASSWORD
    url = f"{SIMULATOR_URL}/api/v1/auth/login"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json={"email": email, "password": password})
    if not resp.is_success:
        raise HTTPException(resp.status_code, f"Login failed: {resp.text}")
    token = resp.json().get("token")
    if not token:
        raise HTTPException(502, "Login response had no token")
    SIMULATOR_TOKEN = token
    print(f"[AUTH] Logged in as {email} ({token[:20]}...)")
    return token


async def get_token(force: bool = False) -> str:
    global SIMULATOR_TOKEN
    async with _token_lock:
        if force or not SIMULATOR_TOKEN:
            await api_login()
        return SIMULATOR_TOKEN


async def call_simulator_api(endpoint: str, method: str = "GET",
                              params: dict = None, json_data: dict = None):
    token = await get_token()
    url   = f"{SIMULATOR_URL}/api/v1/{endpoint.lstrip('/')}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=get_settings().sim_timeout_seconds) as client:
            response = await client.request(method=method, url=url, headers=headers,
                                            params=params, json=json_data)
            if response.status_code == 401:
                token   = await get_token(force=True)
                headers = {"Authorization": f"Bearer {token}"}
                response = await client.request(method=method, url=url, headers=headers,
                                                params=params, json=json_data)
            if response.is_success:
                try:
                    return response.json()
                except Exception:
                    return {"status": "success", "text": response.text}
            else:
                print(f"[API Error] {method} {endpoint} -> {response.status_code}")
                raise HTTPException(status_code=response.status_code,
                                    detail=f"Simulator API error: {response.text}")
    except httpx.RequestError as exc:
        print(f"[Network Error] {url}: {exc}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"Could not connect to Parking Simulator at {SIMULATOR_URL}.")


async def is_component_operable(name: str) -> bool:
    """A component is usable only if it is healthy, not in maintenance mode, and — for
    gates — not one we have repeatedly failed to command (see gate_presumed_failed)."""
    h = component_health.get(name, {})
    if h.get("broken", False) or h.get("under_maintenance", False):
        return False
    if name in maintenance_mode:
        return False
    if name in ALL_GATES and gate_presumed_failed(name):
        return False
    return True


async def _command_gate(gate_name: str, action: str):
    """Open/close one gate, remembering whether it answered. Repeated failures take the
    gate out of service so its zone's traffic moves to another gate (see gate_failures)."""
    if not await is_component_operable(gate_name):
        print(f"[SAFETY] Blocked {action} of {gate_name}: not operable.")
        return {"status": "blocked"}
    try:
        result = await call_simulator_api(f"barrier-gates/{gate_name}/{action}", method="POST")
    except Exception as exc:
        note_gate_failure(gate_name, f"{action} failed: {str(exc)[:120]}")
        raise
    note_gate_success(gate_name)
    return result


async def api_open_barrier_gate(gate_name: str):
    return await _command_gate(gate_name, "open")


async def api_close_barrier_gate(gate_name: str):
    return await _command_gate(gate_name, "close")


async def api_send_car_to_destination(car_name: str, destination: str):
    return await call_simulator_api(f"car/{car_name}/goto/{destination}", method="POST")


async def send_car_to_entry_or_queue(car_plate: str, entry_name: str, assigned_spot: str | None = None):
    """
    Send a car to an EntrySpot only when that EntrySpot is free.

    If the EntrySpot is occupied, place the car in the per-EntrySpot FIFO
    queue.  The next car is released when the current car produces an
    EntrySpot CarOut webhook (which calls process_waiting_entry).
    """

    entry_name = (entry_name or "ENTRY1").upper()
    if entry_name not in entry_occupied:
        entry_name = "ENTRY1"

    async with entry_lock:
        queue = entry_queues[entry_name]
        if entry_occupied[entry_name]:
            pending = list(getattr(queue, "_queue", []))
            if any(item.get("car_plate") == car_plate for item in pending):
                print(f"[ENTRY WAIT] {entry_name} already queued for {car_plate}; skipping duplicate.")
                return {"status": "queued", "entry": entry_name, "duplicate": True}
            print(f"[ENTRY WAIT] {entry_name} occupied -> queueing {car_plate}")
            queue.put_nowait({"car_plate": car_plate, "assigned_spot": assigned_spot})
            return {"status": "queued", "entry": entry_name}
        entry_occupied[entry_name] = True
        entry_car[entry_name] = car_plate
        entry_occupied_since[entry_name] = time.time()

    try:
        result = await api_send_car_to_destination(car_plate, entry_name)
        return result if isinstance(result, dict) else {"status": "success", "result": result}
    except HTTPException as exc:
        detail_str = str(getattr(exc, "detail", "") or "").lower()
        if "occupied" in detail_str or exc.status_code in (409, 422):
            # The EntrySpot is PHYSICALLY occupied by another car.
            # Keep our lock (entry_occupied stays True) and re-queue this car
            # so it is retried as soon as the spot clears.
            print(
                f"[ENTRY ROUTE] Simulator says {entry_name} occupied "
                f"-> requeueing {car_plate}."
            )
            entry_occupied_since[entry_name] = time.time()  # reset watchdog timer
            await entry_queues[entry_name].put(
                {"car_plate": car_plate, "assigned_spot": assigned_spot}
            )
            return {"status": "requeued", "entry": entry_name}
        else:
            # Some other API error — release the lock so the spot doesn't freeze.
            entry_occupied[entry_name] = False
            entry_occupied_since[entry_name] = 0.0
            entry_car[entry_name] = None
            raise
    except Exception:
        # Network / unexpected error — release the lock.
        entry_occupied[entry_name] = False
        entry_occupied_since[entry_name] = 0.0
        entry_car[entry_name] = None
        raise

async def api_charge_car(car_name: str, parking_cost: float, charging_cost: float):
    return await call_simulator_api(f"car/{car_name}/charge", method="POST",
                                    params={"parkingCost": parking_cost, "chargingCost": charging_cost})


# -------------------------------------------------
# GATE WORKERS
# -------------------------------------------------

async def auto_close_gate_after_delay(gate_name: str, delay: float = 1.0):
    if gate_name in ALWAYS_OPEN_GATES:
        return   # a through-gate stays open for the whole level
    if delay > 0:
        await asyncio.sleep(delay)
    try:
        q = gate_queues.get(gate_name)
        if (q is None or q.empty()) and gate_name not in gate_inflight and gate_name not in exit_inflight:
            if has_manual_override(gate_name):
                return
            await api_close_barrier_gate(gate_name)
    except Exception as e:
        print(f"[AUTOMATION ERROR] Close {gate_name}: {e}")


async def release_unparked_assignment(car_plate: str, destination: str):
    """Return a reserved spot when its entrance gate cannot be opened."""
    async with spot_lock:
        car = active_cars.get(car_plate)
        if car and not car.get("parked") and car.get("assigned_spot") == destination:
            parking_spots[destination] = True
            active_cars.pop(car_plate, None)

async def _redirect_entrance_car(car_plate: str, failed_gate: str, destination: str):
    """
    Called when `failed_gate` is blocked/broken.  Frees the already-reserved
    spot, then finds the next operable entrance gate with a free spot and
    re-queues the car there.  Falls back to leavepark if no alternative.
    """
    operable = {gate: await is_component_operable(gate) for gate in ENTRANCE_GATES if gate != failed_gate}
    record_incident("gate_failover", f"{failed_gate} unusable for {car_plate}; looking for another entrance",
                    severity="warning", gate=failed_gate, plate=car_plate, target_type="gate")
    alt_spot = alt_gate = None
    async with spot_lock:
        car = active_cars.get(car_plate)
        if car and not car.get("parked") and car.get("assigned_spot") == destination:
            parking_spots[destination] = True
            car["assigned_spot"] = None
            car_type = normalize_vehicle_type(car.get("car_type", "Normal"))
            eligible_types = {"Any", car_type} if car_type != "Normal" else {"Any"}
            # Any zone whose entrance still works will do, not just this car's original one.
            for gate in ENTRANCE_GATES:
                if not operable.get(gate):
                    continue
                choices = sorted(
                    (spot for spot, free in parking_spots.items()
                     if free and gate in entrance_gates_for_spot(spot)
                     and get_spot_type(spot) in eligible_types and spot_is_serviceable(spot)),
                    key=extract_spot_number,
                )
                if choices:
                    alt_spot, alt_gate = choices[0], gate
                    parking_spots[alt_spot] = False
                    car["assigned_spot"] = alt_spot
                    break
            if alt_spot is None:
                active_cars.pop(car_plate, None)

    if alt_spot:
        try:
            await send_car_to_entry_or_queue(car_plate, entry_spot_for_gate(alt_gate), alt_spot)
            return
        except Exception as exc:
            print(f"[{failed_gate.upper()} FALLBACK] Redirect failed for {car_plate}: {exc}")
            await release_unparked_assignment(car_plate, alt_spot)

    print(f"[{failed_gate.upper()} FALLBACK] No usable alternative for {car_plate} -> leavepark.")
    await api_send_car_to_destination(car_plate, "leavepark")


async def dedicated_entrance_gate_worker(gate_name: str, queue: asyncio.Queue):
    print(f"[{gate_name.upper()} WORKER] Entrance worker started.")
    while True:
        event       = await queue.get()
        car_plate   = event.get("car_plate", "")
        destination = event.get("destination", "leavepark")
        try:
            # A second goto while the previous car is still on this EntrySpot
            # causes simulator overlap. CarOut (or Park/CarIn recovery) releases it.
            while gate_inflight.get(gate_name):
                if gate_inflight[gate_name] == car_plate:
                    break  # duplicate arrival for the same car
                await asyncio.sleep(0.05)
            if gate_inflight.get(gate_name) == car_plate:
                continue
            gate_inflight[gate_name] = car_plate
            print(f"[{gate_name.upper()} WORKER] Opening for {car_plate} -> {destination}")
            open_result = await api_open_barrier_gate(gate_name)

            if isinstance(open_result, dict) and open_result.get("status") == "blocked":
                print(
                    f"[{gate_name.upper()} WORKER] Gate blocked for {car_plate}; "
                    f"trying next entrance gate."
                )
                gate_inflight.pop(gate_name, None)
                await _redirect_entrance_car(car_plate, gate_name, destination)
                continue

            await api_send_car_to_destination(car_plate, destination)
            if car_plate in active_cars:
                active_cars[car_plate]["route_stage"] = "dispatched"
            # The EntrySpot CarOut webhook closes this gate after the car passes.
        except Exception as e:
            if gate_inflight.get(gate_name) == car_plate:
                gate_inflight.pop(gate_name, None)
            print(f"[{gate_name.upper()} WORKER ERROR] {car_plate}: {e}")
            if destination != "leavepark":
                await release_unparked_assignment(car_plate, destination)
        finally:
            queue.task_done()


async def dedicated_exit_gate_worker(gate_name: str, queue: asyncio.Queue):
    print(f"[{gate_name.upper()} WORKER] Exit worker started.")
    while True:
        event     = await queue.get()
        car_plate = event.get("car_plate", "")
        while exit_inflight.get(gate_name):
            if exit_inflight[gate_name] == car_plate:
                break
            await asyncio.sleep(0.05)
        if exit_inflight.get(gate_name) == car_plate:
            queue.task_done()
            continue
        for i in range(12):
            if await is_component_operable(gate_name):
                break
            print(f"[{gate_name.upper()} SAFETY] Maintenance wait {i+1}/12 for {car_plate}...")
            await asyncio.sleep(1.0)

        # If still blocked after waiting, try the next operable exit gate.
        if not await is_component_operable(gate_name):
            # Prefer another exit of the same zone, then any working exit anywhere.
            same_zone = [g for g in exit_gates_for_zone(zone_of_gate(gate_name)) if g != gate_name]
            alt_exit = None
            for g in same_zone + [g for g in EXIT_GATES if g not in same_zone]:
                if g != gate_name and await is_component_operable(g):
                    alt_exit = g
                    break
            if alt_exit:
                record_incident("gate_failover",
                                f"{gate_name} is blocked; {car_plate} sent to {alt_exit}",
                                severity="warning", gate=gate_name, plate=car_plate,
                                target_type="gate", alternative=alt_exit)
                print(
                    f"[{gate_name.upper()} WORKER] Still blocked -> redirecting "
                    f"{car_plate} to {alt_exit}."
                )
                await gate_queues[alt_exit].put({"car_plate": car_plate, "gate_name": alt_exit})
            else:
                print(
                    f"[{gate_name.upper()} WORKER] All exit gates blocked -> "
                    f"sending {car_plate} leavepark directly."
                )
                try:
                    await api_send_car_to_destination(car_plate, "leavepark")
                except Exception as e:
                    print(f"[{gate_name.upper()} WORKER ERROR] leavepark fallback: {e}")
            queue.task_done()
            continue

        print(f"[{gate_name.upper()} WORKER] Opening exit for {car_plate}")
        try:
            exit_inflight[gate_name] = car_plate
            await api_open_barrier_gate(gate_name)
            await api_send_car_to_destination(car_plate, "leavepark")
            # ExitSpot CarOut closes the gate after the car has passed.
        except Exception as e:
            if exit_inflight.get(gate_name) == car_plate:
                exit_inflight.pop(gate_name, None)
            print(f"[{gate_name.upper()} WORKER ERROR] {car_plate}: {e}")
        finally:
            queue.task_done()


# -------------------------------------------------
# CO MONITORING & FAN AUTOMATION
# -------------------------------------------------

# The simulator rates each zone's CO itself: Safe < Mid < High < Critical (webhook "DangerLevel",
# list-zones "risk"). It only sends CO webhooks from Mid upward; ~50 ppm is Mid (hackathon docs).
# A stale Safe label with a high numeric reading still needs ventilation. Fans turn off when
# both the risk and the numeric reading are below Mid.
CO_RISK_ORDER      = {"Safe": 0, "Mid": 1, "High": 2, "Critical": 3}
CO_VENTILATE_FROM  = "Mid"
CO_MID_PPM         = 50.0   # only used if a reading arrives without a risk label
CO_CHECK_ACTIVE_S  = 20     # re-check list-zones this often while any zone is ventilating
CO_CHECK_IDLE_S    = 60     # ...and this often otherwise (list-* calls have a simulated cost)

zone_co: dict[str, dict] = {}   # zone -> {"ppm", "risk", "ventilating", "updated", "fans_on"}
_co_lock = asyncio.Lock()


def co_needs_ventilation(risk: str | None, ppm: float | None) -> bool:
    rated_high = CO_RISK_ORDER.get(str(risk).strip().title(), 0) >= CO_RISK_ORDER[CO_VENTILATE_FROM]
    measured_high = ppm is not None and ppm >= CO_MID_PPM
    return rated_high or measured_high


async def store_charge_async(plate: str, parking_cost: float, charging_cost: float,
                             minutes: float, car_type: str, charged_at: datetime | None):
    """Record an accepted charge in MySQL; retry transient database failures."""
    def run():
        from app.db.session import SessionLocal
        from app.models import CarType
        from app.services.parking import store_charge
        with SessionLocal() as db:
            vehicle_type = {
                "Electric": CarType.ELECTRIC,
                "Accessible": CarType.ACCESSIBLE,
            }.get(car_type, CarType.ANY)
            store_charge(db, plate, parking_cost, charging_cost, minutes=minutes,
                         at=charged_at, car_type=vehicle_type)
    for attempt in range(3):
        try:
            await asyncio.to_thread(run)
            return
        except Exception as e:
            print(f"[BILLING] charge for {plate} not saved (attempt {attempt + 1}/3): {e}")
            if attempt < 2:
                await asyncio.sleep(attempt + 1)


async def recover_manual_parking_async(plate: str, duration: float, raw_data: dict, car_type: str,
                                       at: datetime | None):
    """Persist a sensor-less visit before charging a car at a real exit."""
    def run():
        from app.db.session import SessionLocal
        from app.models import CarType
        from app.services.parking import recover_manual_parking
        vehicle_type = {
            "Electric": CarType.ELECTRIC,
            "Accessible": CarType.ACCESSIBLE,
        }.get(car_type, CarType.ANY)
        with SessionLocal() as db:
            return recover_manual_parking(db, plate, duration, car_type=vehicle_type,
                                          at=at, raw_data=raw_data)
    try:
        return await asyncio.to_thread(run)
    except Exception as e:
        print(f"[PARKING RECOVERY] visit for {plate} not saved: {e}")
        return False


async def audit_system(action: str, target_type: str, target_name: str, success: bool = True, **details):
    """Audit log entry for something the system did by itself (no operator): actor stays empty."""
    try:
        from app.services.audit import record_audit_async
        await record_audit_async(action, target_type=target_type, target_name=target_name,
                                 success=success, details=details or None)
    except Exception as e:
        print(f"[AUDIT] not written ({action} {target_name}): {e}")


async def _audit_fans(zone: str, action: str, names: list, risk, ppm, failed: dict):
    try:
        from app.services.audit import record_audit_async
        for name in names:
            error = failed.get(name)
            await record_audit_async(action, target_type="fan", target_name=name, success=error is None,
                                     details={"zone": zone, "co_ppm": ppm, "risk": risk, **({"error": error} if error else {})})
    except Exception as e:
        print(f"[CO VENTILATION] audit not written: {e}")


async def ventilate_zone(zone_name: str, on: bool, risk=None, ppm=None):
    """Switch every working fan of the zone ON (or OFF); skips fans already in that state."""
    try:
        fans = await call_simulator_api("list-exhaust-fans")
        if not isinstance(fans, list):
            return False
        target = [
            f for f in fans
            if str(f.get("zoneParent") or "").upper() == zone_name.upper()
            and bool(f.get("isOn", False)) != on
            and not f.get("broken", False)
            and not f.get("isUnderMaintenance", False)
            and f.get("name")
            and await is_component_operable(f["name"])
            and not has_manual_override(f["name"])
        ]
        zone_fans = [f.get("name") for f in fans if str(f.get("zoneParent") or "").upper() == zone_name.upper()]
        if not target:
            if on and not zone_fans:
                print(f"[CO VENTILATION] No fans in {zone_name}.")
            return True
        names = [f["name"] for f in target]
        print(f"[CO VENTILATION] {zone_name} ({risk}, {ppm} ppm): fans {'ON' if on else 'OFF'} {names}")
        results = await asyncio.gather(*[
            call_simulator_api(f"exhaust-fans/{n}/{'on' if on else 'off'}", method="POST") for n in names
        ], return_exceptions=True)
        failed = {n: str(r) for n, r in zip(names, results) if isinstance(r, Exception)}
        for n, err in failed.items():
            print(f"[CO VENTILATION ERROR] {n}: {err}")
        zone_co.setdefault(zone_name, {})["fans_on"] = [n for n in names if n not in failed] if on else []
        asyncio.create_task(_audit_fans(zone_name, "AUTO_FAN_ON" if on else "AUTO_FAN_OFF", names, risk, ppm, failed))
        return not failed
    except Exception as e:
        print(f"[CO VENTILATION ERROR] {zone_name}: {e}")
        return False


async def update_zone_co(zone_name: str, ppm, risk):
    """New CO reading (webhook or list-zones): switch fans when the zone crosses the Mid line."""
    if not zone_name:
        return
    try:
        ppm = round(float(ppm), 2) if ppm is not None else None
    except (TypeError, ValueError):
        ppm = None
    need = co_needs_ventilation(risk, ppm)
    async with _co_lock:
        state = zone_co.setdefault(zone_name, {"ventilating": False, "fans_on": []})
        was = state.get("ventilating", False)
        needs_safe_check = not state.get("safe_reconciled", False)
        state.update(ppm=ppm, risk=risk, updated=time.time(), ventilating=need)
        if need:
            state["safe_reconciled"] = False
        if need:
            # also re-run while ventilating: a fan repaired / turned off by maintenance goes back ON
            await ventilate_zone(zone_name, True, risk, ppm)
        elif was or needs_safe_check:
            # Reconcile once even after a restart: an already-running fan must turn off at Safe.
            if await ventilate_zone(zone_name, False, risk, ppm):
                state["safe_reconciled"] = True


async def co_monitor_worker():
    """Backup for webhooks: reads list-zones, catches missed alerts and turns fans off once Safe."""
    print("[CO MONITOR] Started (ventilate from Mid; off when Safe).")
    while True:
        try:
            zones = await call_simulator_api("list-zones")
            if isinstance(zones, list):
                for z in zones:
                    if isinstance(z, dict) and z.get("name"):
                        await update_zone_co(z["name"], z.get("gasCarbonMonoxideLevel"), z.get("risk"))
        except Exception as e:
            print(f"[CO MONITOR ERROR] {e}")
        active = any(v.get("ventilating") for v in zone_co.values())
        await asyncio.sleep(CO_CHECK_ACTIVE_S if active else CO_CHECK_IDLE_S)


# -------------------------------------------------
# DAY/NIGHT LIGHT CONTROLLER
# -------------------------------------------------

async def auto_light_controller_worker():
    print("[LIGHT CONTROLLER] Day/Night light controller started.")
    while True:
        try:
            hour     = _last_sim_hour
            is_night = not (6 <= hour < 18)
            lights   = await call_simulator_api("list-lights")
            if isinstance(lights, list):
                for light in lights:
                    name = light.get("name")
                    if not name or light.get("broken") or light.get("isUnderMaintenance") or has_manual_override(name):
                        continue
                    currently_on = light.get("isOn", False)
                    if is_night and not currently_on:
                        print(f"[LIGHT] Night -> ON: {name}")
                        await call_simulator_api(f"lights/{name}/on", method="POST")
                    elif not is_night and currently_on:
                        print(f"[LIGHT] Day -> OFF: {name}")
                        await call_simulator_api(f"lights/{name}/off", method="POST")
        except Exception as e:
            print(f"[LIGHT CONTROLLER ERROR] {e}")
        await asyncio.sleep(60.0)


# -------------------------------------------------
# USAGE CYCLE TRACKER
# -------------------------------------------------

async def usage_cycle_tracker_worker():
    print("[USAGE CYCLES] Usage cycle tracker started.")
    while True:
        # Gates, fans, and spots are collected by the maintenance scan.
        for endpoint, c_type in [("list-lights", "light")]:
            try:
                items = await call_simulator_api(endpoint)
                if isinstance(items, list):
                    for item in items:
                        n = item.get("name")
                        if n:
                            usage_cycles[n] = {
                                "cycles":       item.get("usageCycles") or item.get("cycleCount") or 0,
                                "last_updated": time.time(),
                                "type":         c_type,
                            }
            except Exception:
                pass
        await asyncio.sleep(30.0)


# -------------------------------------------------
# PREVENTIVE MAINTENANCE WORKER
# -------------------------------------------------

async def _maintain_spot(name: str, reason: str = "maintenance alarm") -> bool:
    async with spot_lock:
        is_free     = parking_spots.get(name, True)
        is_assigned = any(info.get("assigned_spot") == name for info in active_cars.values())
    detail = spot_details.get(name, {})
    occupied = detail.get("isOccupied", False) or bool(detail.get("detectedCars"))
    broken = component_health.get(name, {}).get("broken", False)
    if occupied or is_assigned or (not is_free and not broken) or has_manual_override(name):
        return False
    print(f"[PREEMPTIVE MAINTENANCE] Spot {name} IDLE - repairing...")
    previous_health = component_health.get(name, {}).copy()
    async with spot_lock:
        parking_spots[name] = False
    component_health[name] = {"broken": False, "under_maintenance": True}
    try:
        await call_simulator_api(f"parking-spots/{name}/repair", method="POST")
        await audit_system("AUTO_REPAIR", "spot", name, reason=reason)
    except Exception as err:
        print(f"[PREEMPTIVE MAINTENANCE ERROR] Spot {name}: {err}")
        await audit_system("AUTO_REPAIR", "spot", name, success=False, error=str(err)[:200])
        component_health[name] = previous_health
        async with spot_lock:
            parking_spots[name] = is_free
    return True

async def _maintain_gate(name: str, reason: str = "maintenance alarm") -> bool:
    q = gate_queues.get(name)
    broken = component_health.get(name, {}).get("broken", False)
    if (q is None or has_manual_override(name)
            or (not broken and (not q.empty()
                or (name in ENTRANCE_GATES and entry_occupied[entry_spot_for_gate(name)])))):
        return False
    print(f"[PREEMPTIVE MAINTENANCE] Gate {name} IDLE - repairing...")
    previous_health = component_health.get(name, {}).copy()
    component_health[name] = {"broken": False, "under_maintenance": True}
    try:
        await call_simulator_api(f"barrier-gates/{name}/repair", method="POST")
        component_health[name] = {"broken": False, "under_maintenance": False}
        await audit_system("AUTO_REPAIR", "gate", name, reason=reason)
    except Exception as err:
        print(f"[PREEMPTIVE MAINTENANCE ERROR] Gate {name}: {err}")
        await audit_system("AUTO_REPAIR", "gate", name, success=False, error=str(err)[:200])
        component_health[name] = previous_health
    return True


async def _maintain_fan(name: str, reason: str = "maintenance alarm") -> bool:
    if has_manual_override(name):
        return False
    detail = fan_details.get(name, {})
    zone = str(detail.get("zoneParent") or "").upper()
    co = zone_co.get(zone, {})
    was_broken = component_health.get(name, {}).get("broken", False)
    unavailable = was_broken or component_health.get(name, {}).get("under_maintenance", False)
    if not unavailable and (co_needs_ventilation(co.get("risk"), co.get("ppm"))
                           or (detail.get("isOn") and not co)):
        return False
    print(f"[PREEMPTIVE MAINTENANCE] Fan {name} - turning off and repairing...")
    previous_health = component_health.get(name, {}).copy()
    component_health[name] = {"broken": False, "under_maintenance": True}
    try:
        if not unavailable and detail.get("isOn"):
            await call_simulator_api(f"exhaust-fans/{name}/off", method="POST")
        await call_simulator_api(f"exhaust-fans/{name}/repair", method="POST")
        component_health[name] = {"broken": False, "under_maintenance": False}
        detail.update(broken=False, isUnderMaintenance=False)
        await audit_system("AUTO_REPAIR", "fan", name, reason=reason)
        if co_needs_ventilation(co.get("risk"), co.get("ppm")):
            await ventilate_zone(zone, True, co.get("risk"), co.get("ppm"))
    except Exception as err:
        print(f"[PREEMPTIVE MAINTENANCE ERROR] Fan {name}: {err}")
        await audit_system("AUTO_REPAIR", "fan", name, success=False, error=str(err)[:200])
        component_health[name] = previous_health
    return True


async def auto_preemptive_maintenance_worker():
    print("[MAINTENANCE SCHEDULER] Automatic repair worker started.")
    while True:
        try:
            await maintenance_scan_once()
        except Exception as exc:
            print(f"[MAINTENANCE SCHEDULER ERROR] {exc}")
        try:
            await asyncio.wait_for(maintenance_wakeup.wait(), timeout=MAINTENANCE_SCAN_SECONDS)
        except asyncio.TimeoutError:
            pass
        maintenance_wakeup.clear()


async def maintenance_scan_once():
    """Use simulator health and alarms to repair idle gates, spots, and fans once per cooldown."""
    fresh: set[str] = set()
    for endpoint, details in (
        ("list-barriers", barrier_states),
        ("list-parking-spots", spot_details),
        ("list-exhaust-fans", fan_details),
    ):
        try:
            items = await call_simulator_api(endpoint)
        except Exception as exc:
            print(f"[MAINTENANCE] Could not read {endpoint}: {exc}")
            continue
        if not isinstance(items, list):
            continue
        fresh.add(endpoint)
        for item in items:
            name = item.get("name") if isinstance(item, dict) else None
            if not name:
                continue
            if endpoint == "list-barriers":
                details[name] = item.get("state", "")
            else:
                details[name] = item
            if endpoint in ("list-barriers", "list-exhaust-fans") or (endpoint == "list-parking-spots" and name in parking_spots):
                usage_cycles[name] = {
                    "cycles": item.get("usageCycles", item.get("cycleCount", 0)),
                    "last_updated": time.time(),
                    "type": {"list-barriers": "gate", "list-exhaust-fans": "fan", "list-parking-spots": "spot"}[endpoint],
                }
            if item.get("broken") or item.get("isUnderMaintenance") or name in component_health:
                component_health[name] = {
                    "broken": bool(item.get("broken")),
                    "under_maintenance": bool(item.get("isUnderMaintenance")),
                }
            if (endpoint == "list-parking-spots" and name in parking_spots
                    and name in component_health):
                occupied = item.get("isOccupied", False) or bool(item.get("detectedCars"))
                async with spot_lock:
                    assigned = any(car.get("assigned_spot") == name for car in active_cars.values())
                    parking_spots[name] = not (occupied or assigned or item.get("broken") or item.get("isUnderMaintenance"))

    candidates = {name: "component failure" for name, health in component_health.items() if health.get("broken")}
    candidates.update({name: "fan unavailable" for name, health in component_health.items()
                       if name in fan_details and health.get("under_maintenance") and not health.get("broken")})
    try:
        alarms = await call_simulator_api("list-alarms")
    except Exception as exc:
        print(f"[MAINTENANCE] Could not read alarms: {exc}")
        alarms = []
    if isinstance(alarms, list):
        for alarm in alarms:
            if not isinstance(alarm, dict):
                continue
            name = alarm.get("name") or alarm.get("Name")
            problem = str(alarm.get("problem") or alarm.get("Problem") or "")
            if name and any(word in problem.lower() for word in ("maintenance", "broken", "failure", "repair", "usage", "cycle")):
                candidates[name] = problem

    for name, reason in sorted(candidates.items()):
        health = component_health.get(name, {})
        if (health.get("under_maintenance") and not health.get("broken") and name not in fan_details) or has_manual_override(name):
            continue
        if time.monotonic() - repair_attempts.get(name, float("-inf")) < REPAIR_RETRY_SECONDS:
            continue
        attempted = False
        if name in parking_spots and "list-parking-spots" in fresh:
            attempted = await _maintain_spot(name, reason)
        elif name in ALL_GATES and "list-barriers" in fresh:
            attempted = await _maintain_gate(name, reason)
        elif (name in fan_details or name.lower().startswith(("fan", "f_"))) and "list-exhaust-fans" in fresh:
            attempted = await _maintain_fan(name, reason)
        # The simulator has no lights/{name}/repair endpoint.
        if attempted:
            repair_attempts[name] = time.monotonic()


# -------------------------------------------------
# ENTRY QUEUE DRAIN
# -------------------------------------------------

async def process_waiting_entry(entry_name: str, departed_plate: str | None = None):
    """
    Release one queued car after an EntrySpot becomes free.
    """
    q = entry_queues.get(entry_name)
    if q is None:
        return
    async with entry_lock:
        if departed_plate and entry_car.get(entry_name) not in (None, departed_plate):
            return  # another car has already claimed the physical EntrySpot
        entry_occupied[entry_name] = False
        entry_car[entry_name] = None
        if q.empty():
            return
        item = q.get_nowait()
        entry_occupied[entry_name] = True
        entry_car[entry_name] = item["car_plate"]
        entry_occupied_since[entry_name] = time.time()
    car_plate     = item["car_plate"]
    assigned_spot = item.get("assigned_spot")
    print(f"[ENTRY QUEUE] Releasing queued {car_plate} -> {entry_name}" +
          (f" for {assigned_spot}" if assigned_spot else ""))
    try:
        await api_send_car_to_destination(car_plate, entry_name)
    except Exception as exc:
        print(f"[ENTRY QUEUE ERROR] {car_plate} -> {entry_name}: {exc}")
        if assigned_spot:
            await release_unparked_assignment(car_plate, assigned_spot)
        try:
            await api_send_car_to_destination(car_plate, "leavepark")
        except Exception as leave_exc:
            print(f"[ENTRY QUEUE ERROR] Could not release {car_plate}: {leave_exc}")
        asyncio.create_task(process_waiting_entry(entry_name))
    finally:
        q.task_done()


# -------------------------------------------------
# BUSINESS LOGIC - CAR ENTRY
# -------------------------------------------------

async def process_car_entry(data: dict):
    global _last_sim_hour
    car_plate   = data.get("CarPlateNumber", "")
    car_type    = data.get("CarType", "Normal")
    spot_name   = data.get("SpotName", "")
    planned_dur = float(data.get("PlannedParkingDurationInMinutes", 1) or 1)
    server_time = data.get("ServerDateTime", "")

    try:
        _last_sim_hour = int(str(server_time).split(" ")[1].split(":")[0])
    except Exception:
        pass


    recent_car_arrivals.append({
        "car_plate": car_plate, "car_type": car_type, "spot_name": spot_name,
        "parking_duration": planned_dur, "event_time": server_time, "raw_event": data,
    })
    print(f"[ENTRY] {car_plate} ({car_type}) at {spot_name}.")

    # RE-ENTRY GUARD: rerouted car arriving at correct gate
    existing = active_cars.get(car_plate)
    if existing and existing.get("assigned_spot"):
        assigned_spot = existing["assigned_spot"]
        current_gate  = gate_for_entry_spot(spot_name)
        target_gate = spot_gate(assigned_spot)
        if existing.get("parked") or existing.get("route_stage") in ("at_gate", "dispatched"):
            return
        if current_gate != target_gate:
            if existing.get("route_stage") != "rerouting":
                existing["route_stage"] = "rerouting"
                await send_car_to_entry_or_queue(car_plate, entry_spot_for_gate(target_gate), assigned_spot)
            return
        existing["route_stage"] = "at_gate"
        await gate_queues[target_gate].put({
            "car_plate": car_plate, "gate_name": target_gate, "destination": assigned_spot,
        })
        return

    try:
        g1_ok, g3_ok, g5_ok = await asyncio.gather(
            is_component_operable("gate1"),
            is_component_operable("gate3"),
            is_component_operable("gate5"),
        )
        gate_operable = {"gate1": g1_ok, "gate3": g3_ok, "gate5": g5_ok}

        if not any(gate_operable.values()):
            print(f"[ENTRY] ALL entrance gates inoperable -> leavepark {car_plate}.")
            await api_send_car_to_destination(car_plate, "leavepark")
            return

        norm_type = normalize_vehicle_type(car_type)
        assigned_spot = target_gate = target_entry = None
        current_gate = gate_for_entry_spot(spot_name)
        preferred_gate = ENTRANCE_GATES[zlib.crc32(car_plate.strip().upper().encode()) % len(ENTRANCE_GATES)]

        # Reserve a spot atomically: concurrent entrance webhooks must not choose the same one.
        async with spot_lock:
            # A spot in maintenance mode (operator switch or sensor quarantine) is
            # never offered, even when our own occupancy map still says it is free.
            all_free = [s for s, free in parking_spots.items() if free and spot_is_serviceable(s)]
            eligible = []
            if norm_type == "Electric":
                eligible += [s for s in all_free if get_spot_type(s) == "Electric"]
            elif norm_type == "Accessible":
                eligible += [s for s in all_free if get_spot_type(s) == "Accessible"]
            def reachable(spot: str) -> bool:
                return any(gate_operable.get(g) for g in entrance_gates_for_spot(spot))

            operable = [s for s in eligible if reachable(s)]
            if not operable:
                operable = [s for s in all_free if get_spot_type(s) == "Any" and reachable(s)]
            choices = {gate: sorted((s for s in operable if gate in entrance_gates_for_spot(s)),
                                    key=extract_spot_number) for gate in ENTRANCE_GATES}
            usable_gates = [gate for gate, spots in choices.items() if spots]
            if usable_gates:
                # A plate picks a stable preferred zone. Queue and EntrySpot load
                # override that preference when rerouting would cause a pile-up.
                target_gate = min(usable_gates, key=lambda gate: (
                    gate_queues[gate].qsize()
                    + (2 if gate != current_gate and entry_occupied[entry_spot_for_gate(gate)] else 0)
                    + (0 if gate == current_gate else 1)
                    - (2 if gate == preferred_gate else 0),
                    ENTRANCE_GATES.index(gate),
                ))
                assigned_spot = choices[target_gate][0]
                target_entry = entry_spot_for_gate(target_gate)

            if assigned_spot:
                parking_spots[assigned_spot] = False
                active_cars[car_plate] = {
                    "entry_time": server_time, "car_type": car_type,
                    "assigned_spot": assigned_spot, "assigned_ts": time.time(),
                    "planned_duration": planned_dur, "charged": False, "parked": False,
                    "route_stage": "allocated",
                }

        if assigned_spot:
            print(
                f"[ROUTING] {car_plate}: current={spot_name}/{current_gate}, "
                f"assigned={assigned_spot}, target={target_entry}/{target_gate}"
            )

            if current_gate == target_gate:
                active_cars[car_plate]["route_stage"] = "at_gate"
                print(f"[ENTRY] {car_plate} at correct {current_gate} -> {assigned_spot}.")
                await gate_queues[current_gate].put({
                    "car_plate": car_plate,
                    "gate_name": current_gate,
                    "destination": assigned_spot,
                })
            else:
                active_cars[car_plate]["route_stage"] = "rerouting"
                print(
                    f"[ENTRY REROUTE] {car_plate} at {current_gate}, "
                    f"needs {target_gate} -> {target_entry}."
                )

                # The car is leaving this gate without entering: close it behind the
                # car so the next arrival is not admitted into the wrong zone.
                if current_gate != target_gate:
                    try:
                        await api_close_barrier_gate(current_gate)
                    except Exception as exc:
                        print(f"[ENTRY REROUTE] Could not close {current_gate}: {exc}")
                await send_car_to_entry_or_queue(car_plate, target_entry, assigned_spot)
        else:
            print(f"[ENTRY] No spot for {car_plate} ({norm_type}) -> leavepark.")
            open_gate    = next((g for g in ENTRANCE_GATES if gate_operable.get(g)), None)
            current_gate = gate_for_entry_spot(spot_name)
            if open_gate and current_gate == open_gate:
                await gate_queues[open_gate].put({
                    "car_plate": car_plate, "gate_name": open_gate, "destination": "leavepark",
                })
            else:
                await api_send_car_to_destination(car_plate, "leavepark")

    except Exception as e:
        print(f"[ENTRY ERROR] {car_plate}: {e}")
        car = active_cars.get(car_plate)
        if car and not car.get("parked") and car.get("assigned_spot"):
            await release_unparked_assignment(car_plate, car["assigned_spot"])


# -------------------------------------------------
# BUSINESS LOGIC - CAR EXIT
# -------------------------------------------------

async def _precharge_car_on_departure(car_plate: str, spot_name: str):
    """
    Pre-charge a car as soon as it leaves its parking spot (Park CarOut).
    By the time the car reaches the ExitSpot, payment is already confirmed
    and the gate opens without any extra delay.
    """
    car_info  = active_cars.get(car_plate, {})
    raw_type  = car_info.get("car_type", "Normal")
    norm_type = normalize_vehicle_type(raw_type)
    is_ev     = (norm_type == "Electric")
    duration  = float(car_info.get("planned_duration", 1) or 1)
    parking_cost  = float(max(1, round(duration)))
    charging_cost = float(parking_cost * 2.0) if is_ev else 0.0
    print(f"[PRE-CHARGE] {car_plate} departed {spot_name}: parking=${parking_cost}, EV=${charging_cost}")
    # charge_with_verification retries, records the payment, and refuses to charge a
    # visit twice. A failure here is not fatal: the exit path asks again.
    if await charge_with_verification(car_plate, parking_cost, charging_cost,
                                      reason="pre-charge on departure", minutes=duration):
        print(f"[PRE-CHARGE] {car_plate} charged successfully.")
    else:
        print(f"[PRE-CHARGE] {car_plate} not settled; the exit gate will ask again.")


async def process_car_exit(data: dict):
    global _last_sim_hour
    car_plate   = data.get("CarPlateNumber","") or data.get("CarPlate") or data.get("car_plate") or ""
    spot_name   = data.get("SpotName","") or "EXIT1"
    server_time = data.get("ServerDateTime","")

    if not car_plate:
        return

    car_info  = active_cars.get(car_plate, {})
    raw_type  = data.get("CarType") or car_info.get("car_type","Normal")
    norm_type = normalize_vehicle_type(raw_type)
    is_ev     = (norm_type == "Electric")

    try:
        _last_sim_hour = int(str(server_time).split(" ")[1].split(":")[0])
    except Exception:
        pass

    print(f"[EXIT] {car_plate} ({norm_type}) at {spot_name}.")

    charge_at = None
    try:
        charge_at = datetime.strptime(str(server_time), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        pass

    # Bill elapsed simulator time when both timestamps exist. Planned duration
    # is only a fallback when the entry webhook was missed.
    duration = 0.0
    entry_t = car_info.get("entry_time")
    if entry_t and charge_at:
        try:
            entry_at = datetime.strptime(str(entry_t), "%Y-%m-%d %H:%M:%S")
            duration = max(1, math.ceil((charge_at - entry_at).total_seconds() / 60))
        except (TypeError, ValueError):
            pass
    if duration <= 0:
        duration = float(car_info.get("planned_duration", 0) or 0)
    if duration <= 0:
        for evt in reversed(webhook_events):
            p = evt.get("CarPlateNumber") or evt.get("CarPlate") or evt.get("car_plate")
            if p and p.strip().upper() == car_plate.strip().upper():
                d = float(evt.get("PlannedParkingDurationInMinutes") or 0)
                if d > 0:
                    duration = d; break
    if duration <= 0:
        duration = 1.0
    car_info = active_cars.get(car_plate, {})
    if not car_info:
        await recover_manual_parking_async(car_plate, duration, data, raw_type, charge_at)
        car_info = {"car_type": raw_type, "planned_duration": duration}

    exit_gate  = exit_gate_for_spot(spot_name)
    if car_plate in active_cars:
        active_cars[car_plate]["billed_minutes"] = duration
    payment_ok = payment_is_settled(car_plate)

    if not payment_ok:
        parking_cost  = float(max(1, math.ceil(duration)))
        charging_cost = parking_cost if is_ev else 0.0
        print(f"[EXIT] Charging {car_plate}: parking=${parking_cost}, EV=${charging_cost}")
        payment_ok = await charge_with_verification(car_plate, parking_cost, charging_cost,
                                                    reason="exit", minutes=duration, at=charge_at)
    else:
        print(f"[EXIT] {car_plate} already settled.")

    if payment_ok:
        freed = car_info.get("assigned_spot") or car_info.get("parked_spot")
        if freed and freed in parking_spots:
            async with spot_lock:
                parking_spots[freed] = True
            print(f"[EXIT] Spot {freed} freed.")
        if car_plate in active_cars:
            active_cars[car_plate]["assigned_spot"] = None
        print(f"[EXIT] Queuing {car_plate} for exit via {exit_gate}.")
        await gate_queues[exit_gate].put({"car_plate": car_plate, "gate_name": exit_gate})
    else:
        # All charge attempts failed.  Rescue the car so it isn't trapped at
        # the ExitSpot forever (simulator: 'No valid escape spot found').
        print(f"[EXIT] All charge attempts failed for {car_plate} — rescuing.")
        record_incident("payment_suspicious",
                        f"{car_plate} left without a settled payment after "
                        f"{MAX_PAYMENT_ATTEMPTS} attempts",
                        severity="critical", plate=car_plate, gate=exit_gate)
        freed = car_info.get("assigned_spot") or car_info.get("parked_spot")
        if freed and freed in parking_spots:
            parking_spots[freed] = True
        if car_plate in active_cars:
            active_cars[car_plate]["assigned_spot"] = None
        asyncio.create_task(rescue_stuck_car(
            car_plate, f"charge failed after {MAX_PAYMENT_ATTEMPTS} attempts"))


# -------------------------------------------------
# ENTRY SPOT WATCHDOG
# -------------------------------------------------

async def rescue_stuck_car(car_plate: str, reason: str):
    """
    Last-resort escape: find any operable exit gate and route `car_plate`
    through it to leavepark.  Used when a car is stuck with no valid
    parking destination (e.g. park is full, all spots broken).
    """
    print(f"[RESCUE] {car_plate} - {reason}. Looking for an exit gate...")
    for g in EXIT_GATES:
        if await is_component_operable(g):
            print(f"[RESCUE] {car_plate} -> leavepark via {g}.")
            await gate_queues[g].put({"car_plate": car_plate, "gate_name": g})
            return
    # All exit gates inoperable — try direct goto as absolute fallback.
    print(f"[RESCUE] All exit gates blocked for {car_plate} -> direct leavepark.")
    try:
        await api_send_car_to_destination(car_plate, "leavepark")
    except Exception as exc:
        print(f"[RESCUE ERROR] {car_plate}: {exc}")

async def entry_spot_watchdog_worker():
    """
    Polls every 5 seconds.  If any EntrySpot has been occupied for longer than
    ENTRY_SPOT_TIMEOUT_SECONDS:
      1. Try to nudge the stuck car toward its assigned parking spot via the
         gate queue so it stops blocking the entry.
      2. Force-release the entry_occupied flag.
      3. Drain the per-entry queue so the next waiting car can proceed.
    """
    print("[ENTRY WATCHDOG] Entry spot watchdog started.")
    while True:
        await asyncio.sleep(5.0)
        now = time.time()
        for entry_name, occupied in list(entry_occupied.items()):
            if not occupied:
                continue
            elapsed = now - entry_occupied_since.get(entry_name, now)
            if elapsed < ENTRY_SPOT_TIMEOUT_SECONDS:
                continue

            print(
                f"[ENTRY WATCHDOG] {entry_name} occupied for {elapsed:.0f}s "
                f"(>{ENTRY_SPOT_TIMEOUT_SECONDS}s) — nudging & force-releasing."
            )

            # Try to nudge the stuck car toward its parking spot.
            # If no valid spot exists, rescue it out through an exit gate.
            stuck_plate = entry_car.get(entry_name)
            if stuck_plate:
                car_info   = active_cars.get(stuck_plate, {})
                stuck_spot = car_info.get("assigned_spot")
                already_parked = car_info.get("parked", False)

                if stuck_spot and not already_parked and spot_gate(stuck_spot) in ENTRANCE_GATES:
                    # Car has a valid unparked destination — nudge through the gate.
                    target_gate = spot_gate(stuck_spot)
                    print(
                        f"[ENTRY WATCHDOG] Nudging {stuck_plate} "
                        f"-> {target_gate} for {stuck_spot}."
                    )
                    await gate_queues[target_gate].put({
                        "car_plate":   stuck_plate,
                        "gate_name":   target_gate,
                        "destination": stuck_spot,
                    })
                else:
                    # No valid spot (full park, spot cleared, or already parked).
                    # Send the car out so it doesn't block the entry indefinitely.
                    asyncio.create_task(
                        rescue_stuck_car(
                            stuck_plate,
                            f"stuck at {entry_name} with no valid destination"
                        )
                    )
                    # Free any reserved spot so the pool is correct.
                    if stuck_spot and stuck_spot in parking_spots:
                        parking_spots[stuck_spot] = True
                    if stuck_plate in active_cars:
                        active_cars[stuck_plate]["assigned_spot"] = None

            # Force-release so queued cars can proceed.
            entry_occupied[entry_name] = False
            entry_occupied_since[entry_name] = 0.0
            entry_car[entry_name] = None
            asyncio.create_task(process_waiting_entry(entry_name))


# -------------------------------------------------
# STARTUP
# -------------------------------------------------

@app.on_event("startup")
async def startup_event():
    # The map differs per level, so read it from the simulator BEFORE starting the
    # gate workers: the worker set and the spot table both come from it.
    await discover_layout()

    for g in ENTRANCE_GATES:
        asyncio.create_task(dedicated_entrance_gate_worker(g, gate_queues[g]))
    for g in EXIT_GATES:
        asyncio.create_task(dedicated_exit_gate_worker(g, gate_queues[g]))
    asyncio.create_task(auto_preemptive_maintenance_worker())
    asyncio.create_task(auto_light_controller_worker())
    asyncio.create_task(co_monitor_worker())
    asyncio.create_task(usage_cycle_tracker_worker())
    asyncio.create_task(entry_spot_watchdog_worker())
    asyncio.create_task(sensor_review_worker())
    routed_gates = [g for g in ALL_GATES if g not in ALWAYS_OPEN_GATES]
    print(f"[STARTUP] Closing {len(routed_gates)} routed gate(s)...")
    try:
        await asyncio.gather(*[
            call_simulator_api(f"barrier-gates/{g}/close", method="POST")
            for g in routed_gates
        ], return_exceptions=True)
    except Exception as e:
        print(f"[STARTUP NOTE] {e}")

    # ...then hold the level's through-gates open (Level 3: gate7, gate19).
    await open_always_open_gates()
    for zone in ZONE_DEFS:
        print(f"[STARTUP] {zone['label']}: {len(zone['spots'])} spots, "
              f"entrances {zone['entrances']}, exits {zone['exits']}")
    print(f"[STARTUP] Ready. Map from {layout_source}: {len(ZONE_DEFS)} zones | "
          f"Entrance: {ENTRANCE_GATES} | Exit: {EXIT_GATES} | "
          f"Held open: {ALWAYS_OPEN_GATES} | Spots: {len(VALID_PARKING_SPOTS)}")


# -------------------------------------------------
# WEBHOOK
# -------------------------------------------------

@app.get("/webhook")
@app.get("/webhook.php")
def webhook_info():
    return {"message": "Webhook endpoint is active."}


@app.post("/webhook")
@app.post("/webhook.php")
async def webhook(request: Request):
    """Single entry point for the parking network.

    Order of work, and why:
      1. integrity guard  — an invalid, duplicated or tampered request never
         reaches the car state machine, but is always counted and logged;
      2. raw copy to MySQL — even a rejected request keeps its audit trail;
      3. synchronous state update — occupancy and gate bookkeeping, cheap;
      4. ordered dispatch  — the slow work (simulator calls) is queued per plate,
         so a burst of arrivals is processed concurrently but never out of order.

    The response is returned without waiting for step 4, so the simulator is
    never blocked by our own processing.
    """
    global _last_sim_hour

    body = await request.body()
    source = request.client.host if request.client else "unknown"
    data, verdict, reason = check_request_integrity(body, source)

    # Keep the raw copy either way: db_hook runs its own checks and stores the
    # payload on its worker thread, so this never waits on MySQL.
    await db_hook.record(request)

    if verdict is not None:
        print(f"[WEBHOOK REJECTED] {verdict}: {reason} from {source}")
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"status": "rejected", "verdict": verdict, "reason": reason},
        )

    print(f"[WEBHOOK] {data}")
    webhook_events.append(data)

    server_time = data.get("ServerDateTime", "")
    parsed_time = parse_sim_time(server_time)
    if parsed_time:
        _last_sim_hour = parsed_time.hour   # _last_sim_time is kept by the integrity guard

    event_class = data.get("EventClass", "")
    spot_type   = data.get("SpotType", "")
    spot_name   = data.get("SpotName", "")
    direction   = data.get("Direction", "")
    car_plate   = data.get("CarPlateNumber") or data.get("CarPlate") or data.get("car_plate") or ""

    is_entry_spot = spot_type == "EntrySpot" or (spot_name and spot_name.upper().startswith("ENTRY"))
    is_exit_spot  = spot_type == "ExitSpot" or (spot_name and spot_name.upper().startswith("EXIT"))
    is_park_spot  = spot_type == "Park" or (spot_name and spot_name in parking_spots)

    # ---------------- component health ----------------
    if event_class in ("component_broken", "component_failure"):
        comp = data.get("Name") or data.get("ComponentName")
        if comp:
            component_health[comp] = {"broken": True, "under_maintenance": False}
            maintenance_wakeup.set()
            response_cache.invalidate("spots")
            print(f"[COMPONENT] {comp} BROKEN.")
            if comp in ALL_GATES:
                note_gate_failure(comp, "simulator reported it broken")
            record_incident("sensor_abnormal" if comp in parking_spots else "gate_failover",
                            f"{comp} reported broken by the simulator", severity="warning",
                            spot=comp if comp in parking_spots else None,
                            gate=comp if comp in ALL_GATES else None,
                            target_type="spot" if comp in parking_spots else "gate")

    elif event_class in ("component_fixed", "component_repaired"):
        comp = data.get("Name") or data.get("ComponentName")
        if comp:
            component_health[comp] = {"broken": False, "under_maintenance": False}
            if comp in ALL_GATES:
                note_gate_success(comp)
            if comp in parking_spots and comp not in maintenance_mode:
                async with spot_lock:
                    parking_spots[comp] = True
            response_cache.invalidate("spots")
            print(f"[COMPONENT] {comp} REPAIRED.")

    elif event_class in ("component_maintenance", "component_under_maintenance"):
        comp = data.get("Name") or data.get("ComponentName")
        if comp:
            component_health[comp] = {"broken": False, "under_maintenance": True}
            maintenance_wakeup.set()
            response_cache.invalidate("spots")

    elif event_class in ("payment_made", "payment_received"):
        # The simulator can emit fake payment events. Only our own accepted /charge
        # call settles a visit; anything else is reviewed and may be re-charged.
        dispatch(car_plate or "payments", lambda d=data: review_payment_event(d), "payment review")

    elif event_class in ("carbon_monoxide_event", "carbon_monoxide_level_change"):
        zone_name = data.get("ZoneName", "ZONE1")
        co_level  = data.get("CarbonMonoxideLevel")
        danger    = data.get("DangerLevel")   # Safe / Mid / High / Critical
        print(f"[CO] Zone {zone_name}: {co_level} ppm, {danger}")
        dispatch(f"co:{zone_name}", lambda: update_zone_co(zone_name, co_level, danger), "CO reading")

    elif event_class == "penalty":
        print(f"[PENALTY] {data.get('CarPlateNumber')}: {data.get('Reason')} - ${data.get('FineAmount')}")
        record_incident("integrity_warning",
                        f"Simulator penalty: {data.get('Reason')} (${data.get('FineAmount')})",
                        severity="warning", plate=data.get("CarPlateNumber"),
                        spot=data.get("ComponentName") if data.get("ComponentName") in parking_spots else None,
                        fine=data.get("FineAmount"), penalty_reason=data.get("Reason"))

    # ---------------- ground-truth parking sync ----------------
    if is_park_spot and spot_name:
        async with spot_lock:
            believed_free = parking_spots.get(spot_name, True)
        fault = note_sensor_reading(spot_name, direction, car_plate or None, believed_free)

        detected = data.get("DetectedCars")
        if isinstance(detected, list) and len(detected) > 1:
            note_double_parked_spot(spot_name, len(detected))
        elif isinstance(detected, int) and detected > 1:
            note_double_parked_spot(spot_name, detected)

        async with spot_lock:
            if direction == "CarIn":
                parking_spots[spot_name] = False
                if car_plate:
                    if car_plate not in active_cars:
                        active_cars[car_plate] = {
                            "entry_time":       server_time,
                            "car_type":         data.get("CarType", "Normal"),
                            "assigned_spot":    spot_name,
                            "parked_spot":      spot_name,
                            "planned_duration": float(data.get("PlannedParkingDurationInMinutes", 1) or 1),
                            "charged": False, "parked": True,
                        }
                    else:
                        active_cars[car_plate]["parked_spot"] = spot_name
                        active_cars[car_plate]["parked"]      = True
                print(f"[SENSOR] {spot_name} OCCUPIED by {car_plate or 'unknown'}.")
            elif direction == "CarOut":
                if spot_name not in maintenance_mode:
                    parking_spots[spot_name] = True
                for p, info in list(active_cars.items()):
                    if info.get("assigned_spot") == spot_name:
                        info["assigned_spot"] = None
                print(f"[SENSOR] {spot_name} FREE.")
        response_cache.invalidate("spots")

        if direction == "CarIn" and car_plate:
            # Runs after the lock is released: it may free an abandoned reservation
            # and raise the double-parking warning.
            dispatch(car_plate, lambda p=car_plate, s=spot_name: note_vehicle_parked(p, s),
                     "park bookkeeping")
        elif direction == "CarOut":
            note_vehicle_left_spot(car_plate, spot_name)

        if fault:
            dispatch(f"sensor:{spot_name}", lambda s=spot_name, f=fault: handle_sensor_fault(s, f),
                     "sensor fault")

    # ---------------- entry spot occupancy ----------------
    if is_entry_spot:
        entry_name = (spot_name or ENTRY_SPOTS[0]).upper()
        if entry_name in entry_occupied:
            if direction == "CarIn":
                async with entry_lock:
                    entry_occupied[entry_name] = True
                    entry_car[entry_name] = car_plate or entry_car.get(entry_name)
                    entry_occupied_since[entry_name] = time.time()
                note_vehicle_position(car_plate, entry_name, "at entrance", server_time)
            elif direction == "CarOut":
                asyncio.create_task(auto_close_gate_after_delay(gate_for_entry_spot(entry_name), 0.3))
                asyncio.create_task(process_waiting_entry(entry_name, car_plate or None))
                note_vehicle_position(car_plate, entry_name, "entered the park", server_time)

    # ---------------- exit spot ----------------
    if is_exit_spot and direction == "CarOut":
        gate = exit_gate_for_spot(spot_name)
        if not car_plate or exit_inflight.get(gate) == car_plate:
            exit_inflight.pop(gate, None)
        if car_plate:
            note_vehicle_position(car_plate, spot_name, "left the park", server_time)
            active_cars.pop(car_plate, None)
            charged_cars.discard(car_plate)
            vehicle_tracks.pop(car_plate, None)
            double_parking.pop(car_plate, None)
            payment_state.pop(car_plate, None)
        asyncio.create_task(auto_close_gate_after_delay(gate, 0.3))

    if not car_plate and spot_name and direction == "CarOut":
        car_plate = next(
            (p for p, info in active_cars.items()
             if info.get("assigned_spot") == spot_name or info.get("parked_spot") == spot_name), "")

    # ---------------- slow work, strictly ordered per car ----------------
    if is_entry_spot and direction == "CarIn":
        dispatch(car_plate or f"entry:{spot_name}", lambda d=data: process_car_entry(d), "car entry")
    elif is_exit_spot and direction == "CarIn":
        dispatch(car_plate or f"exit:{spot_name}", lambda d=data: process_car_exit(d), "car exit")

    return {
        "status":           "dispatched",
        "gate_queue_sizes": {g: q.qsize() for g, q in gate_queues.items()},
        "free_spots":       sum(1 for v in parking_spots.values() if v),
        "pending_events":   dispatch_depth(),
    }


# -------------------------------------------------
# AUTH ROUTE
# -------------------------------------------------

@app.post("/auth/login")
async def test_login(body: LoginRequest):
    token = await api_login(body.email, body.password)
    return {"status": "ok", "email": body.email,
            "token_preview": f"{token[:25]}...", "token_length": len(token)}


# -------------------------------------------------
# DASHBOARD DATA ROUTES
# -------------------------------------------------

@app.get("/recent-arrivals")
def get_recent_arrivals(limit: int = 10):
    return {"count": len(recent_car_arrivals), "recent_arrivals": recent_car_arrivals[-limit:]}


@app.get("/all-events")
def get_all_events(limit: int = 20):
    return {"count": len(webhook_events), "events": webhook_events[-limit:]}


@app.get("/recent-activity")
def get_recent_activity(limit: int = 25):
    items = []
    for evt in reversed(list(webhook_events)[-120:]):
        event_class = evt.get("EventClass","")
        server_dt   = evt.get("ServerDateTime") or evt.get("Timestamp") or "Recent"
        time_str    = server_dt.split(" ")[-1] if " " in str(server_dt) else str(server_dt)
        evt_id      = evt.get("EventId") or str(len(items)+1)
        seq_id      = evt.get("SequenceId")

        if event_class == "car_spot_action":
            plate     = evt.get("CarPlateNumber") or evt.get("CarPlate") or "Unknown"
            s_type    = evt.get("SpotType","")
            s_name    = evt.get("SpotName","")
            direction = evt.get("Direction","")
            if s_type == "EntrySpot" or "ENTRY" in s_name.upper():
                event_text  = "Arrived at Entrance" if direction=="CarIn" else "Passed Entrance Gate"
                status_text = "Queued" if direction=="CarIn" else "In Transit"
            elif s_type == "Park":
                event_text  = f"Parked in {s_name}" if direction=="CarIn" else f"Left spot {s_name}"
                status_text = "Parked" if direction=="CarIn" else "Departing"
            elif s_type == "ExitSpot" or "EXIT" in s_name.upper():
                event_text  = "Arrived at Exit Gate" if direction=="CarIn" else "Exited Car Park"
                status_text = "Processing" if direction=="CarIn" else "Departed"
            else:
                event_text  = f"{s_type} {direction}"
                status_text = "Active"
            items.append({"id":evt_id,"sequenceId":seq_id,"category":"car","plate":plate,
                          "event":event_text,"spot":s_name or "—","time":time_str,"status":status_text})
        elif event_class == "gate_action":
            gate_name = evt.get("Name","Gate")
            action    = evt.get("Action","Operated")
            items.append({"id":evt_id,"sequenceId":seq_id,"category":"gate","plate":f"[{gate_name}]",
                          "event":f"Gate {gate_name} {action}","spot":gate_name,"time":time_str,
                          "status":"Open" if action.lower() in ("open","opened") else "Closed"})
        elif event_class == "penalty":
            plate  = evt.get("CarPlateNumber") or evt.get("ComponentName") or "Unknown"
            reason = evt.get("Reason","Violation")
            fine   = evt.get("FineAmount","0")
            items.append({"id":evt_id,"sequenceId":seq_id,"category":"alert","plate":plate,
                          "event":f"Penalty: {reason} (${fine})","spot":evt.get("ComponentName","—"),
                          "time":time_str,"status":"Penalty"})
        elif event_class in ("carbon_monoxide_event","carbon_monoxide_level_change"):
            zone  = evt.get("ZoneName","")
            level = evt.get("CarbonMonoxideLevel",0)
            items.append({"id":evt_id,"sequenceId":seq_id,"category":"alert","plate":"[CO SENSOR]",
                          "event":f"CO {level} ppm in {zone}","spot":zone,"time":time_str,"status":"Alert"})
        elif event_class:
            items.append({"id":evt_id,"sequenceId":seq_id,"category":"system",
                          "plate":evt.get("CarPlateNumber") or "[SYSTEM]",
                          "event":event_class.replace("_"," ").title(),
                          "spot":evt.get("Name") or evt.get("ComponentName") or "—",
                          "time":time_str,"status":"Alert"})
        if len(items) >= limit:
            break
    return items


@app.get("/co-status")
async def co_status():
    """Latest CO reading per zone as the controller sees it (no simulator call)."""
    return [
        {"name": zone, "gasCarbonMonoxideLevel": v.get("ppm"), "risk": v.get("risk"),
         "ventilating": v.get("ventilating", False), "fansOn": v.get("fans_on", []),
         "updated": v.get("updated"), "ventilateFrom": CO_VENTILATE_FROM}
        for zone, v in sorted(zone_co.items())
    ]


async def _build_system_status() -> dict:
    sim_online = False
    try:
        await call_simulator_api("list-parking-spots")
        sim_online = True
    except Exception:
        pass

    async with spot_lock:
        snapshot = dict(parking_spots)
    total    = len(snapshot)
    free     = sum(1 for v in snapshot.values() if v)
    zone_free = {zone_id: sum(1 for s in zone["spots"] if snapshot.get(s))
                 for zone_id, zone in ZONES.items()}

    return {
        "backend": "online", "simulator": "online" if sim_online else "offline",
        "total_spots": total, "available_spots": free, "occupied_spots": total - free,
        "cars_inside": len(active_cars), "park_full": free == 0,
        "broken_components": sum(1 for h in component_health.values() if h.get("broken")),
        "spots_in_maintenance": len(maintenance_mode),
        "open_incidents": len(double_parking) + len(maintenance_mode),
        "pending_events": dispatch_depth(),
        # Legacy keys the dashboard already reads ("zone1"), plus the zone ids.
        "zone_free": {**{f"zone{i}": zone_free.get(z["id"], 0) for i, z in enumerate(ZONE_DEFS, 1)},
                      **zone_free},
    }


@app.get("/system-status")
async def get_system_status():
    # Polled by every open dashboard tab: computed at most once per CACHE_TTL_STATUS.
    return await response_cache.get("system-status", CACHE_TTL_STATUS, _build_system_status)


@app.get("/active-cars")
def get_active_cars():
    cars = []
    for plate, info in active_cars.items():
        entry_raw    = info.get("entry_time","")
        display_time = "Recently"
        elapsed_str  = "Active"
        if isinstance(entry_raw, str) and entry_raw:
            display_time = entry_raw.split(" ")[-1] if " " in entry_raw else entry_raw
            try:
                dt          = datetime.strptime(entry_raw, "%Y-%m-%d %H:%M:%S")
                elapsed_str = f"{max(0, int((datetime.now()-dt).total_seconds()/60))}m"
            except Exception:
                pass
        cars.append({
            "plateNumber": plate, "vehicleType": info.get("car_type","Standard"),
            "brand": "Standard", "model": "Car", "colour": "Silver",
            "assignedSpot": info.get("assigned_spot","—"),
            "parkingSpot":  info.get("parked_spot") or info.get("assigned_spot","—"),
            "entryTime": display_time, "duration": elapsed_str,
            "plannedDuration": info.get("planned_duration",0),
            "charged": info.get("charged",False), "parked": info.get("parked",False),
            "status": "Exiting" if info.get("charged") else ("Parked" if info.get("parked") else "Arriving"),
        })
    return {"count": len(cars), "cars": cars}


# -------------------------------------------------
# VEHICLE LOCATION
# -------------------------------------------------

@app.get("/vehicles/locate/{plate}")
async def locate_one_vehicle(plate: str, _: CurrentUser):
    """Where is this car right now, including cars that ignored their assignment."""
    return locate_vehicle(plate)


@app.get("/vehicles/locate")
async def locate_vehicles(_: CurrentUser, q: str = "", misparked_only: bool = False,
                          limit: int = Query(100, ge=1, le=500)):
    """Locate every tracked car, or the ones whose plate contains `q`.

    `misparked_only` narrows this to the cars that are NOT in the spot they were
    assigned — the ones an operator actually has to go and find.
    """
    plates = set(active_cars) | set(vehicle_tracks)
    if q:
        needle = q.strip().upper()
        plates = {p for p in plates if needle in p.upper()}
    found = []
    for plate in sorted(plates):
        located = locate_vehicle(plate)
        if not located.get("found"):
            continue
        if misparked_only and located.get("in_assigned_spot"):
            continue
        if misparked_only and not located.get("assigned_spot") and not located.get("double_parked"):
            continue
        found.append(located)
        if len(found) >= limit:
            break
    return {"count": len(found), "vehicles": found}


@app.get("/double-parking")
async def get_double_parking(_: CurrentUser):
    """Open double-parking warnings, raised as soon as a second spot is claimed."""
    rows = sorted(double_parking.values(), key=lambda w: w["since"], reverse=True)
    return {"count": len(rows), "warnings": rows}


@app.get("/vehicles/{plate}")
def get_vehicle_details(plate: str):
    info = active_cars.get(plate)
    if info:
        entry_raw    = info.get("entry_time","")
        display_time = str(entry_raw)
        elapsed_str  = "Active"
        if isinstance(entry_raw, str) and entry_raw:
            try:
                dt           = datetime.strptime(entry_raw, "%Y-%m-%d %H:%M:%S")
                display_time = dt.strftime("%I:%M %p")
                elapsed_str  = f"{max(0, int((datetime.now()-dt).total_seconds()/60))}m"
            except Exception:
                display_time = entry_raw
        return {
            "found": True, "plateNumber": plate,
            "vehicleType": info.get("car_type","Sedan"),
            "brand":"Standard","model":"Car","colour":"Silver",
            "parkingSpot": info.get("parked_spot") or info.get("assigned_spot","—"),
            "status":"Exiting" if info.get("charged") else ("Parked" if info.get("parked") else "Arriving"),
            "entryTime":display_time,"exitTime":"—","duration":elapsed_str,
            "charged":info.get("charged",False),
        }
    for arr in reversed(recent_car_arrivals):
        if arr.get("car_plate","").lower() == plate.lower():
            return {
                "found":True,"plateNumber":arr.get("car_plate"),
                "vehicleType":arr.get("car_type","Sedan"),
                "brand":"Standard","model":"Car","colour":"White",
                "parkingSpot":arr.get("spot_name","—"),"status":"Arrived",
                "entryTime":str(arr.get("event_time","Recent")).split(" ")[-1],
                "exitTime":"—","duration":"Recently entered","charged":False,
            }
    for evt in reversed(webhook_events):
        p = evt.get("CarPlateNumber") or evt.get("CarPlate") or evt.get("car_plate")
        if p and p.lower() == plate.lower():
            return {
                "found":True,"plateNumber":p,"vehicleType":evt.get("CarType","Sedan"),
                "brand":"Vehicle","model":"Standard","colour":"Grey",
                "parkingSpot":evt.get("SpotName","—"),
                "status":"Exited" if evt.get("SpotType")=="ExitSpot" else "Active",
                "entryTime":evt.get("ServerDateTime","Earlier"),
                "exitTime":"—","duration":"Completed","charged":True,
            }
    try:
        from app.db.session import SessionLocal
        from app.models import ParkingSession, ParkingSpot
        from sqlalchemy import select
        with SessionLocal() as db:
            row = db.execute(
                select(ParkingSession, ParkingSpot.name)
                .outerjoin(ParkingSpot, ParkingSession.parking_spot_id == ParkingSpot.id)
                .where(ParkingSession.car_plate.like(f"{plate}%"))
                .order_by(ParkingSession.id.desc())
            ).first()
            if row:
                sess, s_name = row
                return {
                    "found":True,"plateNumber":sess.car_plate,
                    "vehicleType":getattr(sess,"car_type","Sedan") or "Sedan",
                    "brand":"Standard","model":"Car","colour":"Silver",
                    "parkingSpot":s_name or "—",
                    "status":"Exited" if sess.status=="completed" else "Active",
                    "entryTime":sess.entry_time.strftime("%I:%M %p") if sess.entry_time else "Earlier",
                    "exitTime":sess.exit_time.strftime("%I:%M %p") if sess.exit_time else "—",
                    "duration":f"Fee: ${(sess.fee_cents or 0)/100:.2f}" if sess.fee_cents else "Completed",
                    "charged":sess.status=="completed" or bool(sess.fee_cents),
                }
    except Exception:
        pass
    return {"found":False,"plateNumber":plate,"message":f"No records for {plate}"}


# -------------------------------------------------
# COMPONENT HEALTH & USAGE CYCLES (NEW ROUTES)
# -------------------------------------------------

@app.get("/component-health")
def get_component_health():
    broken      = {n:h for n,h in component_health.items() if h.get("broken")}
    maintenance = {n:h for n,h in component_health.items() if h.get("under_maintenance")}
    return {
        "broken": broken, "under_maintenance": maintenance,
        "broken_count": len(broken), "maintenance_count": len(maintenance),
        "all": component_health,
    }


@app.get("/usage-cycles")
def get_usage_cycles():
    return {"cycles": usage_cycles, "total_components": len(usage_cycles)}


# -------------------------------------------------
# SIMULATOR PASSTHROUGH GET ROUTES
# -------------------------------------------------

async def _build_spot_list() -> list[dict]:
    broken_spots = set()
    maint_spots  = set()
    try:
        sim_spots = await call_simulator_api("list-parking-spots")
        if isinstance(sim_spots, list):
            for s in sim_spots:
                n = s.get("name")
                if n:
                    if s.get("broken"):             broken_spots.add(n)
                    if s.get("isUnderMaintenance"): maint_spots.add(n)
    except Exception as e:
        print(f"[WARN] list-parking-spots: {e}")

    async with spot_lock:
        local_snap = dict(parking_spots)

    spots = []
    i_global = 0
    for zone in ZONE_DEFS:
        zone_label = zone["label"]
        for name in zone["spots"]:
            i_global   += 1
            is_free     = local_snap.get(name, True)
            is_broken   = name in broken_spots or component_health.get(name,{}).get("broken",False)
            is_maint    = name in maint_spots  or component_health.get(name,{}).get("under_maintenance",False)
            is_occupied = not is_free
            assigned_plate = None
            if is_occupied:
                assigned_plate = next(
                    (p for p,info in active_cars.items()
                     if info.get("assigned_spot")==name or info.get("parked_spot")==name), None)
                if not assigned_plate:
                    for evt in reversed(webhook_events):
                        if evt.get("SpotName") == name:
                            assigned_plate = evt.get("CarPlate") or evt.get("car_plate") or evt.get("CarPlateNumber")
                            if assigned_plate: break
            entry = maintenance_mode.get(name)
            in_maintenance = is_broken or is_maint or entry is not None
            status = "maintenance" if in_maintenance else ("occupied" if is_occupied else "free")
            spots.append({
                "name": name, "zone": zone_label, "zoneId": zone["id"], "purpose": "Park",
                "number": i_global, "spotType": get_spot_type(name),
                "isOccupied": is_occupied, "detectedCars": 1 if is_occupied else 0,
                "broken": is_broken, "isUnderMaintenance": is_maint or entry is not None,
                "isBroken": is_broken, "status": status,
                "maintenanceReason": (entry or {}).get("reason"),
                "sensorFault": _sensor(name)["last_fault"] if name in spot_sensor else None,
                "carPlateNumber": assigned_plate if is_occupied else None,
            })
    return spots


@app.get("/list-parking-spots")
async def list_parking_spots():
    # The heaviest dashboard call (one simulator round-trip). Shared between tabs.
    return await response_cache.get("spots", CACHE_TTL_SPOTS, _build_spot_list)


@app.get("/list-barriers")
async def list_barriers():
    try:
        return await call_simulator_api("list-barriers")
    except Exception:
        return [
            {"name": g, "isOpen": False,
             "isBroken": component_health.get(g,{}).get("broken",False),
             "isUnderMaintenance": component_health.get(g,{}).get("under_maintenance",False),
             "type": "Entrance" if g in ENTRANCE_GATES else "Exit",
             "zoneParent": zone_of_gate(g)}
            for g in ALL_GATES
        ]


# Each list-* call has a simulated operating cost, and every dashboard tab polls
# them. One call per CACHE_TTL_SIM_LIST serves all of them.
@app.get("/list-lights")
async def list_lights():
    return await response_cache.get("list-lights", CACHE_TTL_SIM_LIST,
                                    lambda: call_simulator_api("list-lights"))

@app.get("/list-exhaust-fans")
async def list_exhaust_fans():
    return await response_cache.get("list-exhaust-fans", CACHE_TTL_SIM_LIST,
                                    lambda: call_simulator_api("list-exhaust-fans"))

@app.get("/list-alarms")
async def list_alarms():
    return await response_cache.get("list-alarms", CACHE_TTL_SIM_LIST,
                                    lambda: call_simulator_api("list-alarms"))

@app.get("/list-zones")
async def list_zones():
    return await response_cache.get("list-zones", CACHE_TTL_SIM_LIST,
                                    lambda: call_simulator_api("list-zones"))

@app.get("/test")
async def test():
    return await call_simulator_api("test")


# -------------------------------------------------
# SIMULATOR CONTROL POST ROUTES
# -------------------------------------------------

@app.post("/barrier-gates/{name}/open")
async def open_barrier_gate(name: str, _: GateControlDependency):
    set_manual_override(name)
    return await api_open_barrier_gate(name)


@app.post("/barrier-gates/{name}/close")
async def close_barrier_gate(name: str, _: GateControlDependency):
    set_manual_override(name)
    return await api_close_barrier_gate(name)


@app.post("/barrier-gates/{name}/repair")
async def repair_barrier_gate(name: str, _: RepairDependency):
    set_manual_override(name)
    return await call_simulator_api(f"barrier-gates/{name}/repair", method="POST")


@app.post("/lights/{name}/on")
async def turn_on_light(name: str, _: LightControlDependency):
    set_manual_override(name)
    return await call_simulator_api(f"lights/{name}/on", method="POST")


@app.post("/lights/{name}/off")
async def turn_off_light(name: str, _: LightControlDependency):
    set_manual_override(name)
    return await call_simulator_api(f"lights/{name}/off", method="POST")


@app.post("/lights/group/{name}/on")
async def turn_on_light_group(name: str, _: LightControlDependency):
    set_manual_override(f"group:{name}")
    return await call_simulator_api(f"lights/group/{name}/on", method="POST")


@app.post("/lights/group/{name}/off")
async def turn_off_light_group(name: str, _: LightControlDependency):
    set_manual_override(f"group:{name}")
    return await call_simulator_api(f"lights/group/{name}/off", method="POST")


@app.post("/exhaust-fans/{name}/repair")
async def repair_exhaust_fan(name: str, _: RepairDependency):
    set_manual_override(name)
    return await call_simulator_api(f"exhaust-fans/{name}/repair", method="POST")


@app.post("/exhaust-fans/{name}/on")
async def turn_on_exhaust_fan(name: str, _: FanControlDependency):
    set_manual_override(name)
    return await call_simulator_api(f"exhaust-fans/{name}/on", method="POST")


@app.post("/exhaust-fans/{name}/off")
async def turn_off_exhaust_fan(name: str, _: FanControlDependency):
    set_manual_override(name)
    return await call_simulator_api(f"exhaust-fans/{name}/off", method="POST")


@app.post("/parking-spots/{name}/repair")
async def repair_parking_spot(name: str, _: RepairDependency):
    set_manual_override(name)
    return await call_simulator_api(f"parking-spots/{name}/repair", method="POST")


@app.post("/car/{name}/goto/{destination}")
async def car_goto(name: str, _: CurrentUser, destination: str):
    return await api_send_car_to_destination(name, destination)


@app.post("/car/{name}/charge")
async def car_charge(name: str, user: CurrentUser, parking_cost: float = 0.0, charging_cost: float = 0.0):
    """Charge a car by hand. Goes through the same verified path as the exit gate, so it
    is recorded, audited, and refused if the visit has already been settled (the
    simulator penalises charging one visit twice)."""
    if payment_is_settled(name):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{name} has already been charged for this visit")
    record_incident("payment_retry", f"{user.username} charged {name} by hand",
                    severity="info", plate=name, actor=user.username, target_type="payment",
                    parking=parking_cost, charging=charging_cost)
    settled = await charge_with_verification(name, parking_cost, charging_cost,
                                             reason=f"manual charge by {user.username}")
    if not settled:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"The simulator did not accept the charge for {name}")
    return {"status": "charged", "plate": name,
            "total": round(parking_cost + charging_cost, 2)}



# -------------------------------------------------
# ZONES
# -------------------------------------------------

@app.get("/zones")
async def get_zones():
    """Layout, capacity and gates of every zone, with live occupancy.

    The dashboard builds its zone cards from this, so adding a zone to
    ZONE_DEFS makes it appear without any frontend change.
    """
    async with spot_lock:
        snapshot = dict(parking_spots)
    out = []
    for zone in ZONE_DEFS:
        spots = zone["spots"]
        free = [s for s in spots if snapshot.get(s) and spot_is_serviceable(s)]
        maintenance = [s for s in spots
                       if s in maintenance_mode
                       or component_health.get(s, {}).get("broken")
                       or component_health.get(s, {}).get("under_maintenance")]
        entrances = [{"name": g, "entry_spot": zone["entry_spots"].get(g),
                      "operable": await is_component_operable(g),
                      "queue": gate_queues[g].qsize() if g in gate_queues else 0}
                     for g in zone["entrances"]]
        exits = [{"name": g, "exit_spot": zone["exit_spots"].get(g),
                  "operable": await is_component_operable(g),
                  "queue": gate_queues[g].qsize() if g in gate_queues else 0}
                 for g in zone["exits"]]
        out.append({
            "id": zone["id"],
            "label": zone["label"],
            "capacity": len(spots),
            "free": len(free),
            "occupied": len(spots) - len(free) - len(maintenance),
            "maintenance": len(maintenance),
            "utilisation": round(1 - len(free) / len(spots), 3) if spots else 0.0,
            "first_spot": spots[0] if spots else None,
            "last_spot": spots[-1] if spots else None,
            "reserved_types": sorted({get_spot_type(s) for s in spots} - {"Any"}),
            "entrances": entrances,
            "exits": exits,
            "usable_entrances": sum(1 for g in entrances if g["operable"]),
            "usable_exits": sum(1 for g in exits if g["operable"]),
            "cars_inside": sum(1 for c in active_cars.values()
                               if zone_of_spot(c.get("parked_spot") or c.get("assigned_spot") or "") == zone["id"]),
        })
    return out


# -------------------------------------------------
# MAINTENANCE MODE
# -------------------------------------------------

@app.get("/maintenance/spots")
async def list_maintenance_spots():
    """Every spot currently withdrawn from availability, and why."""
    rows = []
    for name, entry in sorted(maintenance_mode.items()):
        sensor = spot_sensor.get(name, {})
        rows.append({
            "spot": name,
            "zone": zone_of_spot(name),
            "reason": entry["reason"],
            "by": entry["by"],
            "auto": entry["auto"],
            "since": entry["since_text"],
            "sensor_faults": sensor.get("faults", 0),
            "last_fault": sensor.get("last_fault"),
        })
    return {"count": len(rows), "spots": rows,
            "capacity_withdrawn": len(rows),
            "total_spots": len(parking_spots)}


class MaintenanceRequest(BaseModel):
    reason: str = "manual maintenance"


@app.post("/maintenance/spots/{name}/enable")
async def enable_spot_maintenance(name: str, user: RepairDependency,
                                  body: MaintenanceRequest | None = None):
    """Take a spot out of service. It stops being allocated immediately."""
    if name not in parking_spots:
        raise HTTPException(404, f"{name} is not a parking spot in this park")
    reason = (body.reason if body else None) or "manual maintenance"
    return await set_maintenance_mode(name, True, reason=reason, actor=user.username)


@app.post("/maintenance/spots/{name}/disable")
async def disable_spot_maintenance(name: str, user: RepairDependency):
    """Return a spot to service."""
    if name not in maintenance_mode:
        raise HTTPException(404, f"{name} is not in maintenance mode")
    return await set_maintenance_mode(name, False, reason="returned to service",
                                      actor=user.username)


@app.get("/sensor-health")
async def get_sensor_health():
    """Per-spot sensor quality, worst first. Feeds the maintenance view."""
    rows = [{
        "spot": name,
        "zone": zone_of_spot(name),
        "faults": s["faults"],
        "last_fault": s["last_fault"],
        "last_direction": s["last_direction"],
        "last_plate": s["last_plate"],
        "last_seen": _utc_from_timestamp(s["last_at"]).strftime("%Y-%m-%d %H:%M:%S")
        if s["last_at"] else None,
        "in_maintenance": name in maintenance_mode,
    } for name, s in spot_sensor.items() if s["faults"] or name in maintenance_mode]
    rows.sort(key=lambda r: (-r["faults"], r["spot"]))
    return {"count": len(rows), "spots": rows,
            "healthy": len(parking_spots) - len(rows)}


# -------------------------------------------------
# REQUEST INTEGRITY (ADMIN)
# -------------------------------------------------

@app.get("/api/integrity/summary")
async def integrity_summary(_: AdminDependency):
    """Headline numbers for the admin integrity page."""
    by_verdict = {k: v for k, v in integrity_stats.items() if k not in ("total", "accepted")}
    duplicate_copies = sum(d["copies"] for d in duplicate_calls.values())
    return {
        "total_requests": integrity_stats["total"],
        "accepted": integrity_stats["accepted"],
        "rejected": sum(by_verdict.values()),
        "by_verdict": dict(sorted(by_verdict.items())),
        "duplicate_event_ids": len(duplicate_calls),
        "duplicate_copies": duplicate_copies,
        "last_sequence_id": _last_sequence_id,
        "remembered_event_ids": len(_seen_event_ids),
        "sources": len(_source_hits),
        "rate_limit": {"window_seconds": INTEGRITY_RATE_WINDOW, "max_calls": INTEGRITY_RATE_LIMIT},
    }


@app.get("/api/integrity/requests")
async def integrity_requests(_: AdminDependency, limit: int = Query(200, ge=1, le=500),
                             verdict: str | None = None):
    """Rejected and suspect requests, newest first."""
    rows = [r for r in reversed(integrity_log) if verdict in (None, r["verdict"])]
    return {"count": len(rows), "requests": rows[:limit]}


@app.get("/api/integrity/duplicates")
async def integrity_duplicates(_: AdminDependency, limit: int = Query(200, ge=1, le=500)):
    """Duplicated calls, grouped by EventId, most repeated first.

    A duplicate is never processed twice; this is the log of what was dropped.
    """
    rows = sorted(duplicate_calls.values(), key=lambda d: (-d["copies"], d["event_id"]))
    return {"count": len(rows),
            "total_copies": sum(d["copies"] for d in rows),
            "duplicates": rows[:limit]}


# -------------------------------------------------
# INCIDENTS, REPORTS & METRICS
# -------------------------------------------------

@app.get("/api/incidents")
async def get_incidents(_: CurrentUser, limit: int = Query(200, ge=1, le=1000),
                        kind: str | None = None, severity: str | None = None,
                        zone: str | None = None, since_minutes: float | None = None):
    """The incident ledger: everything abnormal the backend noticed or did."""
    cutoff = time.time() - since_minutes * 60 if since_minutes else None
    rows = [
        i for i in reversed(incidents)
        if (kind is None or i["type"] == kind)
        and (severity is None or i["severity"] == severity)
        and (zone is None or i["zone"] == zone)
        and (cutoff is None or i["at"] >= cutoff)
    ]
    return {"count": len(rows), "incidents": rows[:limit],
            "types": dict(sorted(incident_counts.items()))}


@app.get("/api/reports/incidents")
async def incident_report(_: CurrentUser, since_minutes: float = Query(1440, ge=1)):
    """Incident totals by type, severity and zone for the last `since_minutes`."""
    cutoff = time.time() - since_minutes * 60
    window = [i for i in incidents if i["at"] >= cutoff]
    by_zone: dict[str, Counter] = defaultdict(Counter)
    for entry in window:
        by_zone[entry["zone"] or "unassigned"][entry["type"]] += 1
    return {
        "window_minutes": since_minutes,
        "total": len(window),
        "by_severity": dict(Counter(i["severity"] for i in window)),
        "by_type": dict(sorted(Counter(i["type"] for i in window).items())),
        "by_zone": {z: dict(sorted(c.items())) for z, c in sorted(by_zone.items())},
        "critical": [i for i in reversed(window) if i["severity"] == "critical"][:25],
        "lifetime_totals": dict(sorted(incident_counts.items())),
    }


@app.get("/api/reports/operations")
async def operations_report(_: CurrentUser):
    """One call with everything an operations report needs: capacity per zone,
    gate availability, open incidents, payment health and event throughput."""
    zones = await get_zones()
    suspicious = [p for p, s in payment_state.items() if s["suspicions"]]
    unsettled = [p for p, s in payment_state.items() if not s["confirmed"] and s["attempts"]]
    return {
        "generated_at": sim_now().strftime("%Y-%m-%d %H:%M:%S"),
        "simulator_time": _last_sim_time.strftime("%Y-%m-%d %H:%M:%S") if _last_sim_time else None,
        "zones": zones,
        "capacity": {
            "total": len(parking_spots),
            "free": sum(1 for s, free in parking_spots.items() if free and spot_is_serviceable(s)),
            "in_maintenance": len(maintenance_mode),
        },
        "gates": {
            "entrances": {g: {"operable": await is_component_operable(g),
                              "failures": gate_failures.get(g, {}).get("count", 0),
                              "queue": gate_queues[g].qsize()} for g in ENTRANCE_GATES},
            "exits": {g: {"operable": await is_component_operable(g),
                          "failures": gate_failures.get(g, {}).get("count", 0),
                          "queue": gate_queues[g].qsize()} for g in EXIT_GATES},
        },
        "payments": {
            "settled": sum(1 for s in payment_state.values() if s["confirmed"]),
            "unsettled": len(unsettled),
            "suspicious": len(suspicious),
            "suspicious_plates": suspicious[:25],
        },
        "integrity": {
            "total_requests": integrity_stats["total"],
            "accepted": integrity_stats["accepted"],
            "rejected": sum(v for k, v in integrity_stats.items()
                            if k not in ("total", "accepted")),
            "duplicate_event_ids": len(duplicate_calls),
        },
        "incidents": {
            "open_double_parking": len(double_parking),
            "lifetime": dict(sorted(incident_counts.items())),
            "recent": list(reversed(incidents))[:20],
        },
        "throughput": {
            "queued": dispatch_stats["queued"],
            "completed": dispatch_stats["completed"],
            "failed": dispatch_stats["failed"],
            "dropped": dispatch_stats["dropped"],
            "pending": dispatch_depth(),
            "peak_queue_depth": _dispatch_peak_depth,
        },
    }


@app.get("/metrics")
async def get_metrics():
    """Backend health under load. No authentication: it exposes no park data."""
    return {
        "cache": response_cache.stats(),
        "dispatch": {**dispatch_stats, "pending": dispatch_depth(),
                     "peak_queue_depth": _dispatch_peak_depth,
                     "active_keys": len(_dispatch_workers)},
        "webhooks": {"total": integrity_stats["total"], "accepted": integrity_stats["accepted"]},
        "cars_inside": len(active_cars),
        "incidents": sum(incident_counts.values()),
    }
