# Database guide (MySQL 8 on Aiven)

| File | What it is |
| --- | --- |
| [`schema.sql`](schema.sql) | **The** table definitions. Single source of truth. |
| [`queries.sql`](queries.sql) | Ready-made dashboard and debugging queries. |
| [`../backend/app/models/`](../backend/app/models/) | SQLAlchemy classes, one per table. They must match `schema.sql` (a test checks this). |
| [`../backend/app/services/sync.py`](../backend/app/services/sync.py) | Level-start sync (simulator → MySQL upsert). |
| [`../backend/app/services/parking.py`](../backend/app/services/parking.py) | Allocation, parked, exit, charge, departure: one transaction each. |
| [`../docs/aiven-mysql-setup.md`](../docs/aiven-mysql-setup.md) | How to connect to Aiven. |

## Where data comes from

```
Simulator ──list-parking-spots / list-barriers (once per level)──▶ sync.py ──UPSERT──▶ parking_spots, gates
Simulator ──webhook──▶ /webhook ──▶ events (raw, always) ──▶ parking.py ──▶ parking_spots, parking_sessions, events
Dashboard ◀── SELECTs (queries.sql) ◀── MySQL
```

The simulator is the real world. MySQL is **our memory of it**, plus the history the simulator doesn't keep.

---

## The 5 tables in plain language

### `users`: who can log into our dashboard
| Column | Meaning |
| --- | --- |
| `username` | login name, unique |
| `password_hash` | bcrypt hash. The real password is never stored, and you can't read it back. |
| `role` | `ADMIN` (everything, including creating users) or `OPERATOR` (monitor + control the park) |

The first admin is created automatically from `BOOTSTRAP_ADMIN_*` in `.env`. Not related to other tables.

### `parking_spots`: the current state of every spot
One row per simulator spot, created and updated by the sync (never typed in by hand).

| Column | Meaning |
| --- | --- |
| `id` | our internal number. Sessions point to this. It never changes, even when re-synced. |
| `name` | simulator's name (`S150`). **Unique**, because simulator commands use it. |
| `zone` | `ZONE1`… (for the per-zone dashboard) |
| `purpose` | `Park` = a real bay. `EntrySpot` / `ExitSpot` = gate areas, never assigned to cars. |
| `car_type` | who the bay is for: `Electric`, `Accessible`, `Any` |
| `status` | **our** view: `FREE` → `RESERVED` (car driving there) → `OCCUPIED` (car detected) → `FREE`. Or `BROKEN` / `MAINTENANCE`. |
| `current_car` | plate of the car that has reserved or is parked in it, otherwise `NULL` |
| `broken`, `under_maintenance` | copied from the simulator |

Free spots are always **counted** from this table. There is no separate counter that could drift.

### `gates`: current barrier states
| Column | Meaning |
| --- | --- |
| `name` | simulator's name (`gate1`), unique |
| `state` | `Open`, `Closed`, `Opening`, `Closing` |
| `broken`, `under_maintenance` | from the simulator |

### `parking_sessions`: one row per car visit (the most important table)
A row is created when a car arrives, updated as it moves, and **never deleted**, because it is the history.

| Column | Meaning |
| --- | --- |
| `car_plate` | the car |
| `car_type` | `Electric` / `Accessible` / `Any`, or `NULL` if the simulator hasn't told us |
| `parking_spot_id` | → `parking_spots.id`, the spot it was given. `NULL` until assigned. |
| `entry_time` | arrived at the entrance. **Billing starts here.** |
| `parked_time` | detected in its spot |
| `exit_time` | charged at the exit. **Billing ends here.** |
| `parking_cost`, `charging_cost` | `DECIMAL`, so money is exact. `NULL` means not charged yet, which is also what stops double-charging. |
| `payment_status` | `PENDING` → `PAID` |
| `status` | `ENTERING` → `PARKED` → `EXITING` → `COMPLETED` |

A car's *active* session is the one whose status is not `COMPLETED`.

### `events`: the diary
Every important thing that happens gets a row: `SPOT_ASSIGNED`, `CAR_REJECTED`, `CAR_PARKED`,
`CAR_EXITING`, `CAR_CHARGED`, `CAR_DEPARTED`, `SYNC`, `HANDLER_ERROR`…
Every simulator webhook is stored first as `WEBHOOK`, with the full original JSON in `raw_data`,
its `EventId` in `event_id` (**unique**, so a repeated webhook can't be processed twice) and its
`SequenceId` in `sequence_id`. Webhook problems get their own rows: `WEBHOOK_BAD_SIGNATURE`,
`SEQUENCE_GAP`, `SEQUENCE_OUT_OF_ORDER`, `PAYMENT_REJECTED`, `PENALTY`, `CO_ALERT`.
`parking_spot` stores the spot **name** (not id), so the log reads well on its own.

**Existing database from before `event_id` was added?** Either reset it, or run:
```sql
ALTER TABLE events
  ADD COLUMN event_id VARCHAR(64) NULL, ADD COLUMN sequence_id BIGINT UNSIGNED NULL,
  ADD UNIQUE KEY uq_events_event_id (event_id), ADD KEY ix_events_sequence_id (sequence_id);
```

### How they relate
```
parking_spots.id ◀──── parking_sessions.parking_spot_id   (many visits over time use one spot)
events: no foreign keys on purpose. A log must never fail to save because something else is missing.
users, gates: standalone
```

---

## How allocation stays safe (no two cars in one spot, no traffic jam)

```sql
START TRANSACTION;
SELECT * FROM parking_spots
 WHERE purpose='Park' AND status='FREE' AND car_type='Any' AND broken=0 AND under_maintenance=0
 ORDER BY name LIMIT 1
 FOR UPDATE SKIP LOCKED;          -- lock this row; skip rows other cars have locked
UPDATE parking_spots SET status='RESERVED', current_car='ABC123' WHERE id=…;
INSERT INTO parking_sessions (car_plate, parking_spot_id, entry_time, status) VALUES (…);
INSERT INTO events (…);
COMMIT;                            -- lock released; spot is now RESERVED for everyone
-- only NOW call the simulator: POST /car/ABC123/goto/S150
```

- **`FOR UPDATE`** locks the chosen row until `COMMIT`, so no other transaction can take it.
- **`SKIP LOCKED`** means a second car arriving at the same moment doesn't *wait* for the first.
  It simply takes the next free spot. No queue at the entrance.
- **The simulator is called after `COMMIT`:** a slow HTTP call never holds a lock.
- **If the park is full:** the car is logged as `CAR_REJECTED` and sent to `leavepark` straight away,
  so it doesn't block the entrance.
- **The spot is freed as soon as the car leaves it** (`EXITING`), not when it finally leaves the
  park, so the next car can use it sooner.
- **Stale reservations are released automatically:** if a car never reaches its reserved spot,
  the spot is freed after 5 minutes, when the park looks full.

Code: `allocate_spot()` in [`parking.py`](../backend/app/services/parking.py).
Proof: `test_concurrent_arrivals_never_share_a_spot`, where 5 cars arrive at once for 3 spots.

## Departure (one transaction)

`record_charge()` runs when the car is at the exit spot. It locks the session and computes the
cost once. A second call returns `None`, so we never charge twice, which the simulator
would penalize. Then `complete_departure()` runs, and in one `COMMIT` it:
sets `exit_time` (if missing) → sets `payment_status = PAID` → sets `status = COMPLETED` → frees the spot if it's still held → writes a `CAR_DEPARTED` event.

---

## Testing each step

All commands run from `backend/` with the venv active. **Use the phone hotspot:** the venue Wi-Fi blocks MySQL.

| Step | How to test |
| --- | --- |
| Connection (step 3) | `python -m app.db.init_db` should print `OK: MySQL 8.x, TLS: TLS_AES_...` |
| Schema (step 1) | same command should print all 5 tables. `pytest tests/test_schema_matches_models.py` checks the models match. |
| Sync (step 5) | `pytest tests/test_parking_flow.py -k sync`. With the simulator running: log in to `/docs` as admin, call `POST /api/control/sync`, then run the per-zone query from `queries.sql`. |
| Allocation (step 6) | `pytest tests/test_parking_flow.py -k "allocation or concurrent"` |
| Departure (step 7) | `pytest tests/test_parking_flow.py -k lifecycle` |
| Queries (step 8) | open `queries.sql` in Workbench/DBeaver and run them one by one. The last 3 must return 0 rows. |
| Everything | `pytest`. Uses database `carpark_test` (auto-created and wiped), never your real DB. |

**Resetting the real database** (dev only, deletes all data): `python -m app.db.init_db --reset`.
**Changing a table:** edit `schema.sql` **and** the matching model, then reset (or write the `ALTER TABLE`).

---

## Webhooks

Handled in [`webhook.py`](../backend/app/api/routes/webhook.py) (checks) and
[`webhook_handlers.py`](../backend/app/services/webhook_handlers.py) (one handler per `EventClass`).

1. **Signature:** MD5 of all values except `Signature`, sorted by field name, joined with `|`.
   Mismatch → stored as `WEBHOOK_BAD_SIGNATURE` and ignored. Set `WEBHOOK_VERIFY_SIGNATURE=false` only to debug.
2. **Dedup** on `EventId` (unique column).
3. **Order:** a gap or an older `SequenceId` is logged, and the event is still processed.

| `EventClass` | What we do |
| --- | --- |
| `car_spot_action` EntrySpot/CarIn | allocate a spot, `goto` it (or `leavepark` if full) |
| `car_spot_action` Park/CarIn → Park/CarOut | `PARKED` → `EXITING`, spot freed |
| `car_spot_action` ExitSpot/CarIn | charge once (billed on the simulator clock, `ServerDateTime`) |
| `payment_made` | accepted only if the car was charged, the amount equals our charge, and it isn't paid yet |
| `car_spot_action` ExitSpot/CarOut | session `COMPLETED` |
| `gate_action`, `component_broken`/`fixed` | update `gates` (and a spot, if a spot breaks) |
| `penalty`, `carbon_monoxide_event` | logged (`PENALTY`, `CO_ALERT`) |

Webhook `CarType` `Normal` is stored as `Any`.

## Still to confirm from the simulator

Raw payloads land in `events.raw_data` (`event_type = 'WEBHOOK'`). Check them there.

1. Which **gate** belongs to which entry/exit spot (to open the entry gate on arrival, and the exit gate after a valid payment).
2. The component `Type` for a broken parking spot (guessed: `ParkingSpot`).
3. How booleans/nulls are written in the signature string (none in the documented payloads).
4. Billing: is time counted from arrival or from parking, and how are partial minutes rounded (we round **up**)?
5. May a normal car use an Electric/Accessible bay when `Any` bays are full, or is that a penalty? (Currently: no.)
6. What one item in `detectedCars` looks like (assumed: a plate string).
