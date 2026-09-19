# Jiaying: Level 2 plan (database / logs)

My area: operational event logging, login attempts, audit logs, penalties, and the DB/API support for them.
FastAPI wiring (`main.py`, `db_hook.py`, `auth.py`) is **Zhi Hong's**: I only send him handoff snippets.
Team rules: [LEVEL2_TASKS.md](LEVEL2_TASKS.md).

## Status

| Feature | Status | Where |
| --- | --- | --- |
| Login attempts: record success/failure, last 3 after login | **Code done, not wired in yet** (waiting for Zhi Hong) | `services/login_attempts.py`, `routes/login_attempts.py`, `models/login_attempt.py` |
| Audit logs | To do: **Task A** | |
| Penalty page data | To do: **Task B** | |
| Operational event log + daily summary data | To do: **Task C** | |
| Handoff to Zhi Hong (all wiring in one message) | To do: **Step 4** | |

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
