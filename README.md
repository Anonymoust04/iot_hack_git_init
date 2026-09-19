# PARK//CONTROL: Smart Car Park Management System

Car park management system for the hackathon **Park Simulator**. It guides arriving cars to a free spot, charges
them at the exit, keeps gates, lights and exhaust fans working (preventive maintenance, CO ventilation), and shows
everything live on a role-protected dashboard.

**Stack:** FastAPI · SQLAlchemy · MySQL 8 (Aiven) · React + Vite · Vercel + ngrok

## Demo

| | |
| --- | --- |
| **Dashboard** | https://park-control-beta.vercel.app · log in with the admin account |
| **API docs** | https://confidant-delivery-eloquent.ngrok-free.dev/docs |
| **Video** | _add link_ |

The dashboard is hosted on Vercel, but the **simulator and backend run on the team laptop**: the backend has to
reach the simulator on `127.0.0.1`, so it is published through a fixed ngrok address. The link shows live data
while that laptop is signed in, awake and online (see [Public demo](#public-demo)).

## Features

### Level 1: a working car park
- Logs in to the simulator API, lists spots and gates, opens and closes gates, sends cars to a spot or to
  `leavepark`, and charges them at the exit.
- `POST /webhook` receives every simulator event. Each one is checked (MD5 signature, repeated `EventId`,
  `SequenceId` gaps) and stored in MySQL before it is processed.
- Dashboard: free and occupied spots per zone, gate status and control, cars inside, recent activity, vehicle
  search and details, refreshed every few seconds.
- Login with **Admin** and **Operator** roles (JWT, bcrypt passwords), and a searchable history of visits and events.

### Level 2: things break, the car park shouldn't
- **3 zones, 3 entrances, 3 exits:** every gate has its own worker, so exiting cars never wait behind entering ones.
- **Failure handling:** a component is checked before it is operated; broken and under-maintenance components are
  tracked and shown on the dashboard.
- **Usage cycles and preventive maintenance:** usage of spots, gates, lights and fans is counted, and repairs are
  sent before a component wears out.
- **Lights and CO:** lights follow the simulator's day/night clock. Fans switch on when a zone's CO risk reaches
  **Mid** (the simulator's own rating: Safe → Mid → High → Critical) and switch off once it is **Safe** again.
  Per-zone readings come from `/co-status`, fed by CO webhooks plus a periodic check.
- **RBAC:** Admins manage users and their authorities (`USER_MANAGEMENT`, `FINANCIAL_REPORTS`, `GATE_CONTROL`,
  `LIGHT_CONTROL`, `FAN_CONTROL`, `REPAIR`); the backend answers **403** without the authority.
- **Login attempts:** every success and failure is recorded (never the password), and the last three are shown
  after login.
- **Audit log** (Admin only): operator actions, user changes and system events (component broken/fixed,
  `AUTO_REPAIR`, `AUTO_FAN_ON/OFF`), each with who, what, target and result.
- **Penalties page** with totals by type, plus an **operational event log and daily summary** for the daily report.

## How it fits together

```
 Park Simulator ──webhook──▶ POST /webhook ──┬─▶ main.py: gate workers, spot choice, charging,
   (team laptop)                             │            lights / fans / CO, maintenance
        ▲                                    └─▶ db_hook.py: checks + copy ──▶ MySQL (Aiven)
        └──── commands (open gate, goto, charge, repair, lights, fans) ◀── main.py
 Browser ──▶ Vercel dashboard ──▶ FastAPI /api/... ──▶ MySQL
```

MySQL is the dashboard's source of truth. The simulator's `list-*` calls have a simulated cost, so spots and gates
are re-read only every `SIM_SYNC_SECONDS`; webhooks keep the data live in between.

## Run it locally

**Needs:** Windows, Python 3.12+, Node 20+, the Park Simulator, and a network that allows MySQL on port 27109
(some venue Wi-Fi blocks it; a phone hotspot works).

1. **Configure** `.env` in the repo root (copy `.env.example`): the `DB_*` values from Aiven, `JWT_SECRET`,
   `SIM_BASE_URL=http://127.0.0.1:9898/api/v1`, and `SIM_EMAIL` / `SIM_PASSWORD` from the simulator's
   `settings.json`, which must contain `"WebhookUrl": "http://127.0.0.1:8000/webhook"`.
2. **Install once**
   ```powershell
   cd backend;  python -m venv fastapi-env;  .\fastapi-env\Scripts\Activate.ps1;  pip install -r requirements.txt
   cd ..\web-interface;  npm install
   ```
3. **Start** the simulator (with the level loaded), then the backend, then the frontend:
   ```powershell
   cd backend\fastapi_project;  ..\fastapi-env\Scripts\python -m uvicorn main:app    # ready after ~30-40 s
   cd web-interface;            npm run dev                                          # http://localhost:5173
   ```
   Tables are created automatically at startup; nothing needs a reset. API docs: http://127.0.0.1:8000/docs.

## Public demo

**Automatic (already installed on the demo laptop).** At every Windows sign-in a Startup shortcut runs
`scripts\auto-start-public-backend.ps1`, which opens the simulator and keeps FastAPI and the ngrok tunnel running,
restarting either if it stops. Load the level in the simulator window, keep the laptop awake and online, then open
the Vercel link. Status is logged to `%LOCALAPPDATA%\ParkControl\auto-start.log`.

```powershell
# install once (already done on the demo laptop) / remove again
powershell -ExecutionPolicy Bypass -File scripts\install-public-autostart.ps1 -Domain confidant-delivery-eloquent.ngrok-free.dev
powershell -ExecutionPolicy Bypass -File scripts\install-public-autostart.ps1 -Remove

# start it by hand in the current session (no sign-out needed)
powershell -ExecutionPolicy Bypass -File scripts\auto-start-public-backend.ps1 -Domain confidant-delivery-eloquent.ngrok-free.dev
```

`scripts\public-backend.ps1 -Domain <domain>` does the same for a single session and stops when you press Enter.
Use only one of these at a time: they all use port 8000.

**Redeploy the frontend** after changing it:
```powershell
cd web-interface
npx vercel deploy --prod --build-env VITE_API_BASE_URL=https://confidant-delivery-eloquent.ngrok-free.dev --build-env VITE_REFRESH_MS=3000
```

**Tests:** `cd backend; pytest` — they use a separate database (`TEST_DB_NAME`), never the real one. Don't run them
during a demo: they start a second copy of the automation.

## Assumptions

- The simulator runs on the same computer as the backend (webhooks to `127.0.0.1:8000`, commands to `:9898`).
- The simulator we tested sends `"Signature": null`. Unsigned webhooks are accepted, a **wrong** signature is always
  rejected, and `WEBHOOK_REQUIRE_SIGNATURE=true` rejects unsigned ones too.
- Webhooks can arrive out of order, so spots and gates are also re-synced from the simulator, and a visit with no
  event for `STALE_SESSION_MINUTES` is closed.
- Times are stored in UTC; a car's visit times use the simulator's own clock (`ServerDateTime`).
- The simulator reports only a car count per spot, so plates come from webhooks: start the backend before cars arrive.
- Penalties are the simulator's own penalty events; we store and report them.
- Parking charges are not stored in the database yet, so the financial report is empty.

## Layout

```
backend/fastapi_project/main.py     car flow, gate workers, lights/fans/CO, maintenance, simulator API
backend/fastapi_project/db_hook.py  MySQL layer: CORS, API routers, stores webhooks, periodic sync
backend/app/                        models, services (parking, audit, penalties, login attempts, ...), API routes
backend/tests/                      pytest suite              database/schema.sql   all table definitions
web-interface/                      React dashboard           scripts/              deployment scripts
docs/                               level checklists, event types, simulator API notes
```

## Team

| Member | Area |
| --- | --- |
| Zhi Hong (Tee) | Automation: gates, lights, fans, CO, usage cycles, preventive maintenance; FastAPI integration |
| Jiaying | Database, logging and security: login attempts, audit, penalties, event log, users and authorities |
| Jackson | Admin / RBAC interface |
| Christen | Operations dashboard, reports and alerts interface |
