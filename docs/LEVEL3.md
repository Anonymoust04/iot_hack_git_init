# Level 3: resilience, integrity and reporting

Everything here lives in `backend/fastapi_project/main.py`. No database tables were
added: incidents are written to the existing `events` and `audit_logs` tables.

## The map is read from the simulator, not hard-coded

Level 3 has a different map from Level 2, so nothing about the layout is fixed in code.
On startup `main.py` calls `GET /list-parking-spots` and `GET /list-barriers` and builds
its zone registry from the answers (`discover_layout()` → `build_zone_defs()`):

- spots and barriers are grouped by their `zoneParent`;
- within a zone, gates are paired with that zone's `EntrySpot`s first and its `ExitSpot`s
  second, in name order — the Level 2 convention (`gate1`/`ENTRY1`, `gate2`/`EXIT1`);
- `parkingForCarType` gives the Electric / Accessible bays;
- gates with no zone of their own (Level 3's `gate7`, `gate19`) go to `OTHER_GATES` and
  take no part in car routing.

Everything downstream — capacity, routing, failover, the dashboard's zone cards — is
derived from that registry, so a new zone or a second entrance needs no code change.
If the simulator cannot be reached, the built-in Level 2 table is kept so the park still
runs, and a `ZONE_CAPACITY` incident records it.

`ENTRY_GATE` / `EXIT_GATE` in `.env` override the entrance/exit pairing for the gates
they name. They are ignored while they hold their Level 2 defaults, so those defaults
cannot mislabel a different level's gates.

**Through-gates.** `ALWAYS_OPEN_GATES` (default `gate7,gate19`, set in `.env`) are opened
at startup and never closed again: the auto-close timer skips them and they are marked as
manual overrides so the maintenance automation leaves them alone.

## What each feature does

| Requirement | Where | Behaviour |
| --- | --- | --- |
| Multiple zones | `ZONE_DEFS`, `discover_layout()` | Per-zone layout, capacity, entrances and exits, all discovered. `GET /zones` exposes them with live occupancy. |
| Maintenance mode | `set_maintenance_mode()` | A spot can be withdrawn from availability by an operator (`POST /maintenance/spots/{name}/enable`) or automatically. It stops being allocated at once. |
| Sensor abnormalities | `note_sensor_reading()` | Detects a second `CarIn` with no `CarOut`, occupancy with no plate, a `CarOut` from a spot never reported occupied, flapping (6 changes in 60 s), and "occupied by nobody" for 30 minutes. Two faults quarantine the spot and request a repair; `sensor_review_worker` returns it once it is stable. |
| Suspicious payments | `charge_with_verification()`, `review_payment_event()` | A visit is settled only when the simulator accepts **our** charge. A payment event for the wrong amount, for a car we never charged, or a repeat, voids the settlement and **asks for payment again** (up to `MAX_PAYMENT_ATTEMPTS`). A settled visit is never charged twice. |
| Gate failures | `note_gate_failure()`, `gate_presumed_failed()` | Three refused commands take a gate out of the rotation even with no `component_broken` webhook. Cars are re-routed to another gate of the same zone first, then any working gate. One command is let through every 2 minutes to probe recovery. |
| Traffic and load | `TTLCache`, `deque` histories | The heavy dashboard endpoints are computed once per TTL and shared by every tab (single flight, so concurrent callers wait on one computation rather than starting their own). Histories are bounded ring buffers. `GET /metrics` shows cache hit rate and queue depth. |
| Concurrent events | `dispatch()` | One FIFO queue **per plate**: a burst of arrivals is processed concurrently across cars but strictly in arrival order for each car, so a `CarOut` can never overtake its `CarIn`. |
| Invalid / duplicated / tampered requests | `check_request_integrity()` | See below. |
| Double parking | `note_vehicle_parked()` | Warns as soon as a plate claims a second spot — before the simulator's penalty — and also when one spot reports several vehicles. |
| Locating vehicles | `locate_vehicle()` | `GET /vehicles/locate/{plate}` answers where a car actually is, what it was assigned, and whether those differ. `GET /vehicles/locate?misparked_only=true` lists only the cars an operator has to go and find. |
| Reporting and audit | `record_incident()` | Every item above writes an `events` row and an `audit_logs` row. `GET /api/reports/operations` is a single operations report; `GET /api/reports/incidents` groups incidents by type, severity and zone. |

## Request integrity

Every webhook is checked before it reaches the car state machine.

**Rejected** (never processed, always counted and listed):

| Verdict | Meaning |
| --- | --- |
| `malformed` | Not JSON, not an object, or no `EventClass` |
| `tampered` | `Signature` does not match the payload's own fields |
| `duplicate` | An `EventId` already accepted — a retry or a replay |
| `flood` | One source above `INTEGRITY_RATE_LIMIT` calls per window |
| `invalid_field` | A `CarPlateNumber` that cannot be a plate (control characters, markup, absurd length) |
| `unsigned` | No `Signature`, **only** when `WEBHOOK_REQUIRE_SIGNATURE=true` |

**Flagged but processed** — dropping these would do more harm than good:

| Verdict | Why it is not rejected |
| --- | --- |
| `unsigned_accepted` | The shipped simulator sends `"Signature": null` on every webhook (`docs/LEVEL1.md`), so requiring one stops the park. A *wrong* signature is still rejected. |
| `gap`, `out_of_order` | A missed event cannot be fetched again, so the payload is still worth processing. |
| `clock_jump` | Reloading a level rewinds the simulator clock; replays are caught by the `EventId` and `SequenceId` checks instead. |
| `unknown_target` | The map is discovered at runtime, so an unrecognised name may simply be one we have not learned yet. |

Duplicates are grouped by `EventId` with a copy count, first/last seen and the sources
that sent them. Admins see all of this on **Admin → Integrity**
(`web-interface/src/pages/AdminIntegrity.jsx`).

## New endpoints

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `GET /zones` | – | Zone layout, capacity, occupancy, gate availability |
| `GET /maintenance/spots` | – | Spots withdrawn from availability, and why |
| `POST /maintenance/spots/{name}/enable` / `/disable` | `REPAIR` | Switch maintenance mode |
| `GET /sensor-health` | – | Per-spot sensor quality, worst first |
| `GET /api/integrity/summary` | Admin | Accepted vs rejected, counts per verdict |
| `GET /api/integrity/requests` | Admin | Rejected and suspect requests, newest first |
| `GET /api/integrity/duplicates` | Admin | Duplicated calls grouped by `EventId` |
| `GET /vehicles/locate/{plate}` | Login | Where one car actually is |
| `GET /vehicles/locate` | Login | Locate many; `misparked_only=true` for the exceptions |
| `GET /double-parking` | Login | Open double-parking warnings |
| `GET /api/incidents` | Login | The incident ledger, filterable |
| `GET /api/reports/incidents` | Login | Incidents by type, severity and zone |
| `GET /api/reports/operations` | Login | One-call operations report |
| `GET /metrics` | – | Cache hit rate, queue depth, throughput |

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `ALWAYS_OPEN_GATES` | `gate7,gate19` | Gates held open for the whole level |
| `WEBHOOK_REQUIRE_SIGNATURE` | `false` | Reject unsigned webhooks. Off because the simulator does not sign. |
| `WEBHOOK_VERIFY_SIGNATURE` | `true` | Reject *wrong* signatures. Leave on. |
| `ENTRY_GATE` / `EXIT_GATE` | Level 2 names | Override the discovered gate pairing. Ignored at their defaults. |

## Tests

- `tests/test_level3_layout.py` — map discovery, gate pairing, through-gates, fallback
- `tests/test_incidents.py` — integrity verdicts, sensor faults, maintenance mode,
  double parking, locating, payment retries, gate failover, ordered dispatch
