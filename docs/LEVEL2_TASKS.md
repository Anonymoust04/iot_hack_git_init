# Level 2: who works where (read before you change anything)

Five people (and their AI agents) work on Level 2 **at the same time on separate branches**.
This page decides which files each person owns, so merges stay painless. Give it to your agent
together with your own section.

## Rules for every person and agent

1. **Work on your own branch** (`lvl2-<name>`), made from the latest `main`. Never commit to `main` directly.
2. **Prefer new files.** Put your code in files you create (see "Your files" below). Edit a shared file only in the
   ways listed under "Shared files".
3. **Never reformat, rename, reorder or "clean up"** a file you don't own. No formatter over whole files.
4. **Before editing any existing file, run `git status`.** If it already has uncommitted changes you didn't make,
   stop and ask: they belong to someone else.
5. **Never** `git reset --hard`, `git push --force`, rebase a shared branch, or commit someone else's changes.
   Don't use VS Code's "stash & switch" with uncommitted work: commit first.
6. **Need a change in a file you don't own?** Don't make it. Write a short handoff (file, where, the exact lines)
   and send it to the owner, like the one at the end of this page.
7. Run `pytest` from `backend/` (phone hotspot for MySQL; `skipped` means it didn't really run) before opening a PR.

## Who owns what

| Area | Owner | Owns these files | Route prefix |
| --- | --- | --- | --- |
| Automation: failures, usage cycles, preventive maintenance, day/night lights, CO + fans, charge only after parking | **Zhi Hong (Tee)** | `backend/fastapi_project/main.py`, new `backend/fastapi_project/automation_*.py` | `/api/automation/...` |
| Database / logs: event logging, login attempts, audit logs, penalties, DB for their APIs | **Jiaying** | `database/schema.sql`, `backend/app/models/*` (new files), `backend/app/services/{login_attempts,audit,penalties,event_log}.py`, `backend/app/services/user_admin.py`, `backend/app/api/routes/{login_attempts,audit,penalties,event_log,admin_users}.py`, `docs/EVENT_TYPES.md` | `/api/auth/login-attempts`, `/api/auth/me/permissions`, `/api/audit/...`, `/api/penalties/...`, `/api/logs/...`, `/api/admin/users`, `/api/admin/permissions` |
| Admin / RBAC UI: users add/edit/remove, permissions (repair, finance, gate/light/fan control) | **Jackson** | new `web-interface/src/pages/admin/*`, new `web-interface/src/services/adminApi.js` | frontend only |
| Operations / reports UI: 3-zone dashboard, component health, CO/fan/light, broken/maintenance, penalties, audit, daily + financial report, alerts | **Christen** | new `web-interface/src/pages/ops/*`, new `web-interface/src/components/ops/*`, new `web-interface/src/services/opsApi.js` | frontend only |
| Integration: signed webhooks, backend RBAC (403), unknown/manual parked car, wiring modules together | **FastAPI owner = Zhi Hong (Tee)** | `backend/fastapi_project/db_hook.py`, `backend/app/api/routes/auth.py`, `backend/app/api/deps.py`, `backend/app/models/enums.py`, `backend/app/services/webhook_handlers.py` | applies `CanRepair` / `CanControlGates` / ... (from `services/user_admin.py`) to routes |

## Shared files: the only allowed edits

| File | Owner | Others may |
| --- | --- | --- |
| `database/schema.sql` | Jiaying | Nothing. **Ask Jiaying for a new table/column** (send the columns you need). She appends new tables at the end. |
| `backend/app/db/init_db.py` | Jiaying | Nothing. |
| `backend/fastapi_project/main.py` | Tee | Nothing. Send Tee a handoff. |
| `backend/fastapi_project/db_hook.py` | FastAPI owner | Nothing. New routers are registered here by the FastAPI owner (one `include_router` line each). |
| `backend/app/models/__init__.py`, `backend/app/schemas/__init__.py` | nobody (frozen) | Don't edit. Import new models from their own module (`from app.models.login_attempt import LoginAttempt`) and define response models inside your own route file. |
| `backend/app/config.py`, `.env.example` | FastAPI owner | Add settings **only** as a new commented block at the **end** of `Settings` / of the file, with your name in the comment. |
| `backend/requirements.txt` / `requirements.txt` | FastAPI owner | Ask before adding a package. |
| `web-interface/src/App.jsx`, `Navigation.jsx` | FastAPI owner merges | Add **one** `<Route>` / one `<NavLink>` per page, nothing else. Expect a trivial merge conflict here; keep both sides. |
| `web-interface/src/services/api.js` | frozen | Don't edit. Use your own `adminApi.js` / `opsApi.js` and import the shared helpers from `api.js` if needed. |

## Shared contracts (agree before coding)

- **Roles / permissions:** `users.role` is `ADMIN` or `OPERATOR`. Per-user authorities (`REPAIR`, `FINANCIAL_REPORT`,
  `GATE_CONTROL`, `LIGHT_CONTROL`, `FAN_CONTROL`) are in `user_permissions` (Jiaying, `services/user_admin.py`); Admin
  has all of them. Jackson's UI calls `/api/admin/users` + `/api/admin/permissions`; every page can call
  `/api/auth/me/permissions` to hide buttons. Zhi Hong protects routes with `CanRepair`, `CanControlGates`, ...
- **Event log:** operational events go into the existing `events` table via `app.services.parking.log_event(db, "TYPE", ...)`.
  Use UPPER_SNAKE event types with your area as prefix, e.g. `AUTOMATION_FAN_ON`, `MAINTENANCE_REPAIR_SENT`.
- **Penalties:** the simulator's `penalty` webhooks are already stored in `events` (`event_type = 'PENALTY'`,
  full payload in `raw_data`). Jiaying's penalty API reads from there; don't add a second store.
- **Times** are stored in UTC (`app.models.utcnow()`); car visit times use the simulator clock (`ServerDateTime`).
- **Simulator calls from `backend/app`** go through `app.services.simulator_client.get_simulator()` (logs in from `.env`).

## Handoff: login attempts (Jiaying → FastAPI owner)

Status: done and tested on branch `lvl2-db` (`backend/tests/test_login_attempts.py`). Two small edits by the FastAPI owner:

**1. `backend/app/api/routes/auth.py`**: record every attempt and return the last three.

```python
# imports (add)
from fastapi import Request
from app.api.routes.login_attempts import LoginOut
from app.services.login_attempts import client_ip, get_last_login_attempts, record_login_attempt

# replace the login route
@router.post("/login", response_model=LoginOut)
def login(request: Request, db: DbSession, form: Annotated[OAuth2PasswordRequestForm, Depends()]):
    user = db.scalar(select(User).where(User.username == form.username))
    if user is None or not verify_password(form.password, user.password_hash):
        record_login_attempt(db, form.username, False, client_ip(request))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password")
    record_login_attempt(db, user.username, True, client_ip(request))
    return LoginOut(access_token=create_access_token(user.username, user.role), role=user.role,
                    login_attempts=get_last_login_attempts(db, user.username))
```

`LoginOut` = the old `TokenOut` (`access_token`, `token_type`, `role`) **plus** `login_attempts`
(`[{success, ip_address, attempted_at}]`, newest first, includes this login), so the existing frontend keeps working.

**2. `backend/fastapi_project/db_hook.py`**, inside `setup()`, after the existing `for r in (...)` loop:

```python
    app.include_router(login_attempts.router)   # Level 2: GET /api/auth/login-attempts
```
and at the top with the other route imports: `from app.api.routes import login_attempts  # noqa: E402`

The `login_attempts` table is created automatically on the next backend start (`schema.sql`, `CREATE TABLE IF NOT EXISTS`).

**Frontend (Jackson / Christen):** after login, show `response.login_attempts` (last three). After a page reload,
fetch them again with `GET /api/auth/login-attempts` (`Authorization: Bearer <token>`).

## Prompt template for your agent

> I'm <name> on a 36-hour hackathon team. Read `docs/LEVEL2_TASKS.md` first and follow its rules strictly.
> My area is <area>; I own only the files listed for me there. Implement: <feature>.
> Before editing any existing file, check `git status` and stop if it has changes I didn't make.
> Don't modify files owned by others; if integration needs one, give me a handoff snippet instead.
> Keep the diff small, add tests under `backend/tests/` (or verify the UI with `npm run build` and `npm run lint`),
> and at the end show `git status` and `git diff --stat`.
