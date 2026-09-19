# Jiaying: Level 2 plan (database / logs)

My area: operational event logging, login attempts, audit logs, penalties, and the DB/API support for them.
FastAPI wiring (`main.py`, `db_hook.py`, `auth.py`) is **Zhi Hong's**: I only send him handoff snippets.
Team rules: [LEVEL2_TASKS.md](LEVEL2_TASKS.md).

## Current verification checklist — 2026-09-20

This checklist supersedes the older handoff assumptions below. It records what was checked against the
running browser app, FastAPI instance, and simulator on 2026-09-20. A checked item means the surface was
observed or exercised; it does not mean every failure-mode requirement is complete.

### Verified live

- [x] `http://127.0.0.1:8000/health` responds `200` with `{"status":"ok"}`.
- [x] FastAPI Swagger loads and exposes login attempts, dashboard, control, audit, penalties, and logs routes.
- [x] Browser login succeeds with the configured admin account.
- [x] Browser login response/UI shows the latest three login attempts.
- [x] Frontend routes render: dashboard, vehicles, penalties, audit, reports, and admin users.
- [x] Simulator login succeeds with `admin` / `admin`.
- [x] Simulator returns parking spots, barriers, lights, exhaust fans, zones, and alarms.
- [x] Simulator reports three zones and live CO readings; it currently reports maintenance alarms for `gate3` and `gate5`.
- [x] `npm run build` succeeds for the frontend.

### Partially working or currently failing live

- [ ] Dashboard live integration is not verified: the browser currently displays `System Offline`, unavailable
  component data, and unavailable CO data despite the backend health endpoint and simulator being reachable.
- [ ] Admin user directory is not verified: the page remains on `REFRESHING...` / loading instead of showing users.
- [ ] The simulator returns a high numeric CO reading for Zone 1 while its risk label is `Safe`; threshold/risk
  consistency needs checking.
- [ ] Current database-focused test rerun was blocked by MySQL error `2013 Lost connection to MySQL server during query`
  while dropping `user_permissions`; this is an environment failure, not a passing test result.
- [ ] Earlier integration tests still need repair: gate role inference, webhook response `queued` versus `dispatched`,
  and the gate-operability fixture/safety path.

### Still not complete

- [ ] Strict signed-only webhook enforcement: `webhook_require_signature` still defaults to `False`, so missing
  signatures are accepted by default.
- [ ] Audit call-site integration for gate/control/repair actions, user changes, fan/light actions, and automation.
- [ ] Financial report backend and frontend integration: `getFinancialReport()` still returns `null`.
- [ ] Usage-cycle monitoring for parking spots, durable cycle history, and threshold-based maintenance scheduling.
- [ ] Complete failure recovery for every component type, including durable maintenance state and repair outcomes.
- [ ] Complete RBAC protection for simulator routes in `main.py`, especially direct movement, charging, and component routes.
- [ ] Frontend permission-based hiding/enforcement using effective per-user permissions rather than only local role checks.
- [ ] Complete manual-parking recovery: identify/register a car that entered without gate/spot events, estimate its
  duration, charge it once, free the occupied spot, record the events, and route it safely through an exit.
- [ ] Complete dynamic financial and operational reporting validation with live database data.

### Recommended next checks

- [ ] Restore a stable MySQL connection, then run `pytest -q` from `backend/` without treating skipped or setup-failed
  database tests as passing.
- [ ] In the browser, confirm `GET /api/dashboard`, `GET /api/auth/users`, `/api/penalties`, `/api/audit`, and
  `/api/logs/daily-summary` after login, recording the HTTP status and response body for each.
- [ ] Trigger one simulator component failure, repair it, and verify the dashboard state, database event, audit row,
  and returned component state.
- [ ] Send one missing-signature and one invalid-signature webhook, confirming both are rejected and logged.
- [ ] Run the manual-parked-car scenario end to end and verify no duplicate charge or stale occupied spot remains.

## Progress checklist

Check items off as they're actually done (files exist, tests really passed on MySQL — not `skipped`,
and it's committed). This is the one place to see what's left before the PR.

### Step 0 — Login attempts
- [x] Service + route + model created (`services/login_attempts.py`, `routes/login_attempts.py`, `models/login_attempt.py`)
- [x] `login_attempts` table added to `database/schema.sql`
- [x] Tests written (`tests/test_login_attempts.py`)
- [x] Tests passed offline (SQLite substitute, MySQL blocked at the time)
- [x] Tests passed on real MySQL (Aiven): 8 passed on `carpark_test_login_attempts`
- [x] Committed (`bf9fc72`)
- [ ] Wired into `auth.py` / `db_hook.py` by Zhi Hong (handoff below, not my job)

### Task A — Audit logs (agent 1)
- [x] Files created: `models/audit_log.py`, `services/audit.py`, `routes/audit.py`, `tests/test_audit.py`
- [x] `audit_logs` table appended to `database/schema.sql` (nothing else in that file touched: +24 lines)
- [x] Tests pass on `carpark_test_audit` (12 passed, real Aiven MySQL)
- [x] `tests/test_schema_matches_models.py` still passes (3 passed with the new table)
- [x] Reviewed: `git status` shows only this task's files (+ Task B's, which belong to agent 2)
- [x] Committed (`5df1eae`, with Task B)
- [x] Handoff table written (see "Handoff: audit call sites" under Task A)

### Task B — Penalties page data (agent 2)
- [x] Files created: `services/penalties.py`, `routes/penalties.py`, `tests/test_penalties.py`
- [x] No new table added (reads `events` where `event_type = 'PENALTY'`)
- [x] Tests pass on `carpark_test_penalty` (6 passed)
- [x] Reviewed: `git status` is clean; Task B files are committed in `5df1eae`
- [x] Committed (`5df1eae`, with Task A)

### Task C — Event log search + daily summary (agent 3)
- [x] Files created: `services/event_log.py`, `routes/event_log.py`, `tests/test_event_log.py`, `docs/EVENT_TYPES.md`
- [x] No new table added (reads existing `events`)
- [x] Tests pass on `carpark_test_events` (5 passed on MySQL)
- [x] Reviewed: `git status` is clean; Task C files are committed in `97c976f`
- [x] Committed (`97c976f`, with Task A follow-up)

### Task D — User management + authorities (RBAC data)
> **Update (combined branch):** Jackson's branch built user management + permissions too (`auth.py`,
> `core/permissions.py`, `deps.py`), and the frontend uses it. To keep **one** permission system, my Task D API
> (`services/user_admin.py`, `routes/admin_users.py`, `tests/test_admin_users.py`) was removed; `schema.sql` now has
> one `user_permissions` table matching the merged model, and `init_db.upgrade_schema()` adds any missing columns to
> an existing table on startup. The items below are kept as history (code in commit `44833b0`).
- [x] Files created: `models/user_permission.py`, `services/user_admin.py`, `routes/admin_users.py`, `tests/test_admin_users.py`
- [x] `user_permissions` table appended to `database/schema.sql` (no change to `users`: no reset needed)
- [x] `init_db.py`: `user_permissions` added to the drop list before `users` (needed by the foreign key)
- [x] Tests pass on `carpark_test_users` (14 passed, real Aiven MySQL)
- [x] Full suite passes on `carpark_test_full` (83 tests; also fixed a leftover-admin bug in `test_audit.py`)
- [x] Committed (`44833b0`)
- [ ] Wired in by Zhi Hong (router + permission checks on routes, see "Task D" below)
- [ ] Admin page built by Jackson (API below)

### Step 4 — Handoff & PR
- [ ] Full suite passes together: `pytest` in `backend/` (real MySQL run, not skipped) — latest isolated MySQL run: 52 passed, 6 failed, 14 errors; the test admin was not seeded after another Admin was created first
- [ ] One handoff message sent to Zhi Hong: login route change, router registrations, audit call sites,
      endpoint list for Christen/Jackson
- [x] Pushed: local `lvl2-db` and `origin/lvl2-db` match at `7e66722`
- [ ] PR opened `lvl2-db → main` (merged by Zhi Hong, not by me)

---

## 1. How to test what's done (login attempts)

Always on the **phone hotspot** (the venue Wi-Fi blocks MySQL). In `backend/`:

```powershell
.\fastapi-env\Scripts\Activate.ps1
pytest tests/test_login_attempts.py -v
```

Pass = `8 passed`. **`skipped` means MySQL wasn't reachable and nothing was tested.**

| Test | Proves |
| --- | --- |
| `test_1_successful_login_adds_one_success_row` | Correct password → one `success = true` row, and the login response lists it |
| `test_2_wrong_password_adds_one_failed_row_and_no_secret` | Wrong password → one `success = false` row, password not stored |
| `test_3_latest_three_newest_first` | 5 attempts → only the newest 3 come back, newest first |
| `test_4_only_one_previous_attempt` | One attempt → one result, no error |
| `test_5_unknown_username_is_logged_safely` | Unknown/odd username → still logged as failed, nothing breaks |
| `test_simultaneous_attempts_are_all_recorded` | 20 logins at once → 20 rows |
| `test_very_long_username_and_ip_are_cut_not_rejected` | Huge input is cut to fit, not an error |
| `test_endpoint_shows_only_my_attempts` | `GET /api/auth/login-attempts` needs login and shows only your own |

Then the whole suite, so nothing old broke: `pytest` → all `passed`.

**After Zhi Hong wires it in**, check it by hand:
1. Restart the backend, open http://127.0.0.1:8000/docs.
2. `POST /api/auth/login` with a wrong password (401), then the right one: the response has `login_attempts`
   with 2 entries, newest first (`true`, then `false`).
3. **Authorize**, then `GET /api/auth/login-attempts` → the same list.
4. In MySQL: `SELECT * FROM login_attempts ORDER BY id DESC LIMIT 10;`: no password anywhere.

**Reminder:** the manual login check is still pending until Zhi Hong applies the `auth.py` and `db_hook.py`
handoff. Before that wiring, the login-attempt service and tests can pass, but the running `/api/auth/login`
response will not yet contain `login_attempts`, and `/api/auth/login-attempts` will not be registered in the
real app.

### Manual penalty verification

The penalty service and focused tests are already complete. To verify the endpoint manually after Zhi Hong
registers the penalties router, use this order:

1. Start MySQL and the backend. The backend must be running because the SQL insert only creates test data;
  it does not call the API.
2. In MySQL Workbench, DBeaver, or the MySQL client, select the configured `DB_NAME` database and insert one
  test penalty. `DB_NAME` is the real development database; do not use the pytest database unless you are
  deliberately testing there.

  ```sql
  INSERT INTO events
  (event_type, event_time, raw_data)
  VALUES
  ('PENALTY', UTC_TIMESTAMP(),
   '{"Reason":"Occupied broken spot","FineAmount":"50.00","Type":"ParkingSpot","ComponentName":"S12","CarPlateNumber":"CAR-001","EventId":"manual-penalty-1"}');
  ```

3. Open http://127.0.0.1:8000/docs, call `POST /api/auth/login`, and copy the returned `access_token`.
4. Click **Authorize** in Swagger and enter `Bearer <access_token>`.
5. Call `GET /api/penalties`. The response should contain the inserted row with reason `Occupied broken
  spot`, fine `50.00`, type `ParkingSpot`, component `S12`, and plate `CAR-001`.
6. Call `GET /api/penalties/summary`. It should include the row in `count`, `total_fine`, and the
  `ParkingSpot` entry under `by_type`.
7. Call `GET /api/logs/events?types=PENALTY`. The same event should appear. Call
  `GET /api/logs/daily-summary` and confirm the current UTC day's `penalties` and `penalty_total` include it.
8. Delete the manual row afterwards if this is a shared development database:

  ```sql
   DELETE FROM events
   WHERE event_type = 'PENALTY'
     AND JSON_UNQUOTE(JSON_EXTRACT(raw_data, '$.EventId')) = 'manual-penalty-1';
  ```

**What must be wired first:** the SQL insert and service tests do not need `main.py`, but the manual endpoint
checks require the running FastAPI app to register `penalties.router` and `event_log.router` in `db_hook.py`.
The login step also requires the login handoff described above. You do not need to wait for the frontend page
to verify these backend endpoints in Swagger.

---

## 2. Order of work

```
Step 0  (me, 5 min)          commit login attempts
Step 1  (3 agents, parallel) Task A audit  |  Task B penalties  |  Task C event log + daily summary
Step 2  (me)                 review each agent's files, run the full test suite
Step 3  (me)                 commit per task
Step 4  (me)                 one handoff message to Zhi Hong, push, open PR
```

**Why A, B and C can run at the same time:** each creates only its own new files (different names), and only
Task A touches a shared file (`database/schema.sql`, one table appended at the end). B and C need no new table:
penalties and operational events already live in the `events` table.

### Step 0: commit what's done (before starting any agent)

```powershell
cd C:\Users\User\myProject\IoT_hack\iot_hack_git_init
git status                     # should list only my login-attempt files + docs
git add database/schema.sql backend/app/models/login_attempt.py backend/app/services/login_attempts.py backend/app/api/routes/login_attempts.py backend/tests/test_login_attempts.py docs/LEVEL2_TASKS.md docs/JIAYING_LEVEL2_PLAN.md
git commit -m "Level 2: login attempt logging"
```

### Rules for all three agents (paste with every prompt)

> - Read `docs/LEVEL2_TASKS.md` and `docs/JIAYING_LEVEL2_PLAN.md` first.
> - Create **only** the files listed for your task. Do not edit any other file. If you think another file needs a
>   change, stop and tell me.
> - **Do not run any git command that changes anything** (no add, commit, stash, checkout, switch, reset, pull,
>   merge). Other agents are working in the same folder right now. `git status` / `git diff` are fine.
> - Follow the existing style: models in `backend/app/models/<name>.py` (import it directly, don't edit
>   `models/__init__.py`), services take `db: Session` as the first argument and commit, response models live inside
>   your route file (don't edit `app/schemas/__init__.py`), use `CurrentUser` / `AdminUser` from `app/api/deps.py`.
> - Run tests with **your own test database** so you don't wipe the other agents' data:
>   `$env:TEST_DB_NAME = "<given below>"; pytest tests/<your test file> -v` (phone hotspot needed; `skipped` = not tested).
> - At the end: list every file you created, show `git status`, and give me the one line Zhi Hong must add to
>   `db_hook.py` to register your router. Don't add it yourself.

---

### Task A: Audit logs (agent 1) · test DB `carpark_test_audit`

**Goal (Level 2):** "Maintain audit logs for important actions, changes, repairs, and system events." Only Admin reads them.

**Files to create:**
- `backend/app/models/audit_log.py`
- `backend/app/services/audit.py`
- `backend/app/api/routes/audit.py`
- `backend/tests/test_audit.py`

**The only shared-file edit:** append this table at the **end** of `database/schema.sql` (keep CRLF line endings,
don't touch anything above it):

| Column | Type | Notes |
| --- | --- | --- |
| `id` | BIGINT UNSIGNED, PK, auto | |
| `actor` | VARCHAR(64) NULL | username who did it; NULL = the system (automation) |
| `action` | VARCHAR(64) NOT NULL | UPPER_SNAKE, e.g. `GATE_OPEN`, `SPOT_REPAIR`, `FAN_ON`, `USER_CREATED`, `USER_ROLE_CHANGED`, `AUTO_REPAIR` |
| `target_type` | VARCHAR(32) NULL | `gate`, `spot`, `light`, `fan`, `user`, `system` |
| `target_name` | VARCHAR(64) NULL | e.g. `gateA`, `S12`, `op1` |
| `success` | BOOLEAN NOT NULL DEFAULT TRUE | a failed repair / refused command is still audited |
| `details` | JSON NULL | extra data (old/new role, error text). **Never passwords or tokens.** |
| `ip_address` | VARCHAR(45) NULL | |
| `created_at` | DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP | |

Indexes: `(created_at)`, `(actor, created_at)`, `(action, created_at)`, `(target_type, target_name)`.

**Service** (`services/audit.py`):
- `record_audit(db, action, *, actor=None, target_type=None, target_name=None, success=True, details=None, ip_address=None)`:
  one short transaction; cut strings to the column sizes; must never raise into the caller's real work (log and move on).
- `list_audit(db, *, actor=None, action=None, target_type=None, target_name=None, since=None, until=None, limit=50, offset=0)`:
  newest first.

**Route** (`routes/audit.py`, prefix `/api/audit`, **AdminUser only**): `GET /api/audit` with the filters above
(limit max 500). Response model defined in the file.

**Tests:** record + read back; newest first; each filter; Operator gets 403, no token gets 401; `details` with a
dict round-trips; 20 records at once from threads all saved; a table/model column check passes
(`tests/test_schema_matches_models.py` must still pass).

**Handoff to include in the answer:** the `db_hook.py` line, plus a table "where Zhi Hong should call
`record_audit`": gate open/close/repair in `routes/control.py`, repairs and fan/light switching in his automation,
user create/edit/delete in the admin routes, with the exact call for each.

#### Handoff: audit call sites (Task A → Zhi Hong)

Three helpers in `app/services/audit.py`. None of them ever raises because of the audit itself, and none commits
or rolls back the caller's own work (they write in a separate short transaction):

| Helper | Use it in |
| --- | --- |
| `with audited(db, ACTION, actor=..., target_type=..., target_name=...):` | sync routes with a `db` session: records success, or `success=False` + the error text if the block raises (the error is re-raised unchanged) |
| `record_audit(db, ACTION, actor=..., ...)` | sync code, one-off events |
| `await record_audit_async(ACTION, ...)` | async code without a session: `main.py` routes and automation loops |

**Register the router** in `db_hook.py` (top: `from app.api.routes import audit  # noqa: E402`; in `setup()` after the
existing loop): `app.include_router(audit.router)`  → `GET /api/audit` (Admin only).

| Where | Change (exact) |
| --- | --- |
| `app/api/routes/control.py` · `gate_action` | rename the `_: OperatorUser` parameter to `user: OperatorUser`, then wrap the simulator call:<br>`with audited(db, f"GATE_{action.upper()}", actor=user.username, target_type="gate", target_name=name):`<br>`    _simulator_call(...)`  (the existing line, indented) |
| `app/api/routes/control.py` · `repair_spot` | add `db: DbSession`, rename `_` to `user`, wrap the simulator call in `with audited(db, "SPOT_REPAIR", actor=user.username, target_type="spot", target_name=name):` |
| `app/api/routes/control.py` · `car_goto` | add `db: DbSession`, rename `_` to `user`, wrap in `with audited(db, "CAR_MOVED", actor=user.username, target_type="car", target_name=plate, details={"destination": destination}):` |
| `app/api/routes/control.py` · `resync` | rename `_` to `user`, wrap in `with audited(db, "SIMULATOR_RESYNC", actor=user.username, target_type="system"):` |
| `app/api/routes/auth.py` · `create_user` (and future edit / delete / role change) | rename `_: AdminUser` to `admin: AdminUser`; after `db.commit()`: `record_audit(db, "USER_CREATED", actor=admin.username, target_type="user", target_name=user.username, details={"role": user.role})`. Edit: `"USER_UPDATED"` with `{"old_role": ..., "new_role": ...}`; delete: `"USER_DELETED"`. **Never put passwords in `details`** (keys with password/token/secret are masked anyway). |
| `main.py` · `/barrier-gates/{name}/repair`, `/parking-spots/{name}/repair`, `/exhaust-fans/{name}/repair` | after the `call_simulator_api(...)` succeeds: `await record_audit_async("GATE_REPAIR" / "SPOT_REPAIR" / "FAN_REPAIR", target_type="gate"/"spot"/"fan", target_name=name)` |
| `main.py` · `/lights/.../on|off`, `/lights/group/...`, `/exhaust-fans/{name}/on|off` | `await record_audit_async("LIGHT_ON" / "LIGHT_OFF" / "LIGHT_GROUP_ON" / "FAN_ON" / "FAN_OFF", target_type="light"/"fan", target_name=name)` |
| Automation (preventive maintenance, day/night lights, CO fans) | `await record_audit_async("AUTO_REPAIR" / "AUTO_LIGHTS_OFF" / "AUTO_FAN_ON", target_type=..., target_name=..., details={"reason": "usage 950/1000 cycles"})`, no `actor` (= the system). On a refused command pass `success=False, details={"error": ...}`. |

`main.py`'s own routes have no logged-in user yet, so they record `actor=None`. Once they get RBAC (403), pass
`actor=user.username` there too.

---

### Task B: Penalties page data (agent 2) · test DB `carpark_test_penalty`

**Goal (Level 2):** "Record received penalties caused by mistakes or violations and display them on a dedicated page."
Penalties are **already recorded**: every simulator `penalty` webhook is stored in `events` with
`event_type = 'PENALTY'` and the full payload in `raw_data`
(`Reason`, `FineAmount`, `Type`, `ComponentName`, sometimes `CarPlateNumber`, `EventId`, `ServerDateTime`).
**Do not add a table and do not touch webhook code**: read from `events`.

**Files to create:**
- `backend/app/services/penalties.py`
- `backend/app/api/routes/penalties.py`
- `backend/tests/test_penalties.py`

**Service:**
- `list_penalties(db, *, since=None, until=None, type=None, limit=50, offset=0)` → newest first, each as
  `{id, received_at, reason, fine_amount (Decimal), type, component, car_plate, event_id}` parsed from `raw_data`
  (missing fields → None; a non-numeric FineAmount → 0 and still listed).
- `penalty_summary(db, *, since=None, until=None)` → `{count, total_fine, by_type: [{type, count, total_fine}], top_reasons: [{reason, count}]}`.

**Routes** (prefix `/api/penalties`, any logged-in user `CurrentUser`):
`GET /api/penalties` (filters above) and `GET /api/penalties/summary?since=&until=`.

**Tests:** insert `Event(event_type="PENALTY", raw_data={...})` rows directly; listing order and parsing; filters;
missing/odd fields; summary totals by type; 401 without a token.

---

### Task C: Operational event log + daily summary data (agent 3) · test DB `carpark_test_events`

**Goal (Level 2):** "Store operational events and important activities in the database" and the data behind
"dynamic daily reports". Events are **already stored** in `events` (car arrivals, parking, departures, gates,
broken/fixed, penalties, CO alerts, raw webhooks). This task makes them searchable and summarised and documents the
event types so Zhi Hong's automation uses the same names. **No new table, no webhook changes.**

**Files to create:**
- `backend/app/services/event_log.py`
- `backend/app/api/routes/event_log.py`
- `backend/tests/test_event_log.py`
- `docs/EVENT_TYPES.md`: every `event_type` in use (grep `log_event(` and `"CAR_` etc. under `backend/app`),
  what it means, what `raw_data` holds, plus the naming rule for new ones (`AUTOMATION_*`, `MAINTENANCE_*`, ...).

**Service:**
- `search_events(db, *, types=None, plate=None, since=None, until=None, exclude_raw_webhooks=True, limit=50, offset=0)`:
  newest first; `exclude_raw_webhooks` hides `WEBHOOK` rows by default.
- `daily_summary(db, day: date)` (UTC day) → `{date, cars_arrived, cars_parked, cars_departed, penalties, penalty_total,
  components_broken, components_fixed, co_alerts, busiest_hour, events_by_type: {...}}`. One or two `GROUP BY`
  queries, not a loop over rows.

**Routes** (prefix `/api/logs`, `CurrentUser`): `GET /api/logs/events` (filters above) and
`GET /api/logs/daily-summary?day=YYYY-MM-DD` (default today UTC).

**Tests:** insert `Event` rows with chosen `event_time`s; filters and ordering; `WEBHOOK` rows hidden by default;
the day boundary (23:59 vs 00:00) counts correctly; empty day → zeros, not an error.

---

### Task D: User management + authorities (done by me)

**Goal (Level 2):** RBAC so only authorized users can repair and generate financial reports; an Admin can
create, edit and remove users and their authorities, shown on Jackson's admin page.

**Design:** `users.role` stays `ADMIN` / `OPERATOR` (no change to an existing table). Extra authorities per user
live in the new `user_permissions` table. **ADMIN has every authority**; an OPERATOR has only what an Admin ticks:

| Authority | Allows |
| --- | --- |
| `REPAIR` | repair gates, spots, fans |
| `FINANCIAL_REPORT` | generate / view financial reports |
| `GATE_CONTROL` | open / close gates |
| `LIGHT_CONTROL` | switch lights |
| `FAN_CONTROL` | switch exhaust fans |

Safety: nobody can delete their own account; the last ADMIN can't be deleted or demoted (safe even when two
admins act at the same moment). Every create / edit / delete is written to the audit log (never the password).

**API for Jackson's admin page** (all need an **Admin** token except the last one):

| Call | Body / result |
| --- | --- |
| `GET /api/admin/permissions` | `[{name, description}]`: the checkboxes |
| `GET /api/admin/users` | `[{id, username, role, permissions, created_at}]`: `permissions` = effective (Admin: all) |
| `POST /api/admin/users` | `{username, password (min 4), role: "ADMIN"/"OPERATOR", permissions: [...]}` → 201 + the user; 409 duplicate; 422 unknown authority |
| `PATCH /api/admin/users/{id}` | any of `{role, permissions, password}`; only what's sent changes; `permissions` replaces the list; 409 last admin |
| `DELETE /api/admin/users/{id}` | 204; 409 own account / last admin |
| `GET /api/auth/me/permissions` | any logged-in user → `{username, role, permissions}`: **hide buttons the user may not use** |

Errors come back as `{"detail": "..."}`: show that text to the admin.

**Handoff to Zhi Hong** (`db_hook.py`, top: `from app.api.routes import admin_users  # noqa: E402`; in `setup()`
after the loop): `app.include_router(admin_users.router)`. Then protect routes with the ready-made checks from
`app.services.user_admin` (401 without login, 403 without the authority, Admin always passes):

| Route | Change the user parameter to |
| --- | --- |
| `routes/control.py` `gate_action` (open/close) | `user: CanControlGates` (and `CanRepair` when `action == "repair"`, or split repair into its own route) |
| `routes/control.py` `repair_spot` | `user: CanRepair` |
| financial report routes (Christen's data, when built) | `user: CanViewFinance` |
| `main.py` `/barrier-gates/...`, `/lights/...`, `/exhaust-fans/...`, `/parking-spots/{name}/repair` | add `user: CanControlGates` / `CanControlLights` / `CanControlFans` / `CanRepair` |

How to test by hand: `pytest tests/test_admin_users.py -v` (14 passed), or wire the router locally (don't commit
`db_hook.py`) and use `/docs`: create an Operator with `REPAIR`, log in as them, `GET /api/auth/me/permissions`.

---

## 3. Step 2 and 3: after the agents finish (me, one at a time)

1. For each agent: `git status` must show **only** the files listed for its task (Task A may also show
   `database/schema.sql`). Anything else → ask the agent why before keeping it.
2. Run everything together on the hotspot: `pytest` in `backend/` → all passed.
3. Commit one task at a time:
   ```powershell
   git add <task A files> ; git commit -m "Level 2: audit logs"
   git add <task B files> ; git commit -m "Level 2: penalties API"
   git add <task C files> ; git commit -m "Level 2: event log search + daily summary"
   ```

## 4. Step 4: one handoff to Zhi Hong

Send him a single message (he owns `main.py`, `db_hook.py`, `auth.py`):

1. **Login attempts:** the `auth.py` login change + `db_hook.py` line (in `LEVEL2_TASKS.md`, "Handoff: login attempts").
2. **Routers** (all in `db_hook.py`, inside `setup()`, after the existing `for r in (...)` loop):
   ```python
   from app.api.routes import audit, event_log, login_attempts, penalties  # noqa: E402   (top of file)

   for r in (login_attempts.router, audit.router, penalties.router, event_log.router):  # Level 2 (Jiaying)
       app.include_router(r)
   ```
3. **Where to call `record_audit(...)`**: the table from Task A.
4. **For Christen (frontend):** the endpoints: `/api/auth/login-attempts`, `/api/audit`, `/api/penalties`,
   `/api/penalties/summary`, `/api/logs/events`, `/api/logs/daily-summary`, with one example response each
   (copy from `/docs` once wired).

Then: `git push -u origin lvl2-db` and open a PR `lvl2-db → main`. **Don't merge it yourself**; let Zhi Hong merge
together with his wiring.
