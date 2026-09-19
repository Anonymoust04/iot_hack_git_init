# PARK//CONTROL: Smart Car Park Management System

A web-based car park management system for the hackathon **Park Simulator**. It detects arriving cars, guides
them to a free spot of the right type, charges them at the exit, keeps gates, lights and exhaust fans running
(with preventive maintenance and CO monitoring), and shows everything live on a role-protected dashboard.

**Stack:** Python FastAPI · SQLAlchemy · MySQL 8 (Aiven, TLS) · React + Vite · Cloudflare quick tunnel (live demo)

## Demo

- **Demo video:** _add the link here_ (simulator and dashboard side by side)
- **Live demo:** started on request from the team laptop with `scripts\live-demo.ps1`, which prints a public
  `https://….trycloudflare.com` link (see [Live demo](#live-demo-public-link)).
- **Slides:** see the submitted PDF.

## What we built

### Level 1: a working car park
- **Simulator integration:** logs in to the simulator API, lists spots and gates, opens/closes gates, sends cars
  to spots or away (`leavepark`), and charges them at the exit.
- **Webhook listener** (`POST /webhook`): every simulator event is received, checked, and stored in MySQL.
- **Car flow:** an arriving car gets a free spot, the entrance gate opens and closes behind it; at the exit the car is
  charged once, the exit gate opens, and the car leaves. A full park sends new cars away at once so the entrance
  never blocks.
- **Dashboard:** free / occupied spots per zone, gate status and control, cars inside, recent activity, vehicle
  search and details, all refreshed live.
- **Authentication and roles:** JWT login with bcrypt-hashed passwords; **Admin** and **Operator** roles.
- **History:** car arrivals, parking, departures and events are stored and searchable by plate and time.

### Level 2: things break, the car park shouldn't
- **3 zones, multiple entrances and exits:** each entrance and exit has its own gate worker, so exiting cars never
  wait behind entering ones.
- **Failure handling:** a component is checked before it is operated (no commands to broken gates); broken and
  under-maintenance components are detected, recorded and shown on the dashboard (`/component-health`).
- **Usage cycles and preventive maintenance:** usage of spots, gates, lights and fans is tracked, and repair
  commands are sent **before** a component reaches its limit.
- **Lights and exhaust fans:** lights follow the simulator's day/night time; fans switch on automatically when a
  zone's CO level rises.
- **RBAC:** Admins manage users (create, edit, remove, change role and authorities) from the Admin page.
  Per-user authorities (`USER_MANAGEMENT`, `FINANCIAL_REPORTS`, `GATE_CONTROL`, `LIGHT_CONTROL`, `FAN_CONTROL`,
  `REPAIR`) are stored in the database and checked by the backend (**403** without the authority).
- **Login attempts:** every successful and failed login is recorded (never the password), and the last three are
  shown after login.
- **Audit log:** important actions, changes, repairs and system events: who, what, which component, success or
  failure. Admin only; secrets in details are masked.
- **Penalties:** every penalty the simulator sends is stored and listed on its own page with totals by type.
- **Operational event log and daily summary:** searchable events plus a per-day summary (cars in/out, penalties,
  broken/fixed components, CO alerts, busiest hour) for the daily report.
- **Webhook safety:** MD5 signature check, duplicate `EventId`s ignored, `SequenceId` gaps logged; every payload is
  stored raw before processing.

## How it fits together

```
 Park Simulator ──webhook──▶ POST /webhook ──┬─▶ main.py: entry/exit gate workers, spot choice, charging,
   (this laptop)                             │            lights / fans / CO, usage cycles, preventive repair
        ▲                                    └─▶ db_hook.py: checks + copy ──▶ MySQL (Aiven)
        └──── REST commands (open gate, goto, charge, repair, lights, fans) ◀── main.py
 Browser (React dashboard) ──▶ FastAPI /api/... ──▶ MySQL        (live data refreshed every few seconds)
```

MySQL is the dashboard's source of truth. The simulator's `list-*` calls have a simulated operating cost, so spots
and gates are re-read from the simulator only every `SIM_SYNC_SECONDS`; webhooks keep the data live in between.

## How to run

**Needs:** Windows, Python 3.12+, Node.js 20+, the Park Simulator (`ParkingSimulator-win-x64`), and a network that
allows MySQL on port 27109 (some venue Wi-Fi networks block it; a phone hotspot works).

1. **Configure.** Copy `.env.example` to `.env` (repo root) and fill in the `DB_*` values (Aiven), `JWT_SECRET`, and
   the simulator login (`SIM_EMAIL` / `SIM_PASSWORD` = `Name` / `Password` in the simulator's `settings.json`).
   In the simulator's `settings.json`: `"WebhookUrl": "http://127.0.0.1:8000/webhook"`.
2. **Install once.**
   ```powershell
   cd backend;  python -m venv fastapi-env;  .\fastapi-env\Scripts\Activate.ps1;  pip install -r requirements.txt
   cd ..\web-interface;  npm install
   ```
3. **Start, in this order:**
   1. the **simulator**, with the level loaded;
   2. the **backend**: `cd backend\fastapi_project` then `..\fastapi-env\Scripts\python -m uvicorn main:app`
      (wait for `Application startup complete`, about 30–40 s: it creates missing tables, seeds the admin and syncs
      the simulator);
   3. the **frontend**: `cd web-interface` then `npm run dev`, and open http://localhost:5173.
4. **Log in** with the admin from `BOOTSTRAP_ADMIN_USERNAME` / `BOOTSTRAP_ADMIN_PASSWORD` (created on first start).
   API documentation: http://127.0.0.1:8000/docs.

Tables are created automatically (`database/schema.sql`, `CREATE TABLE IF NOT EXISTS`); nothing needs a reset.

### Live demo (public link)

Close your own backend/frontend, start the simulator, then from the repo root:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\live-demo.ps1
```
It downloads `cloudflared` if needed, opens two free Cloudflare quick tunnels, starts the backend and frontend
configured for them, and prints the public **DEMO LINK**. The simulator stays on the laptop (the backend reaches it
on `127.0.0.1`), so the link works while the laptop is on and online. Press Enter in the script window to stop.

### Tests

```powershell
cd backend;  .\fastapi-env\Scripts\Activate.ps1;  pytest
```
Tests run against a separate MySQL database (`TEST_DB_NAME`, created and wiped automatically), never the real one.

## Assumptions

- **The simulator runs on the same computer as the backend.** It sends webhooks to `127.0.0.1:8000`, and the
  backend sends commands to it on `127.0.0.1:9898`. The public demo link tunnels only the dashboard and the API.
- **Webhook signatures:** the simulator we tested sends `"Signature": null`. A **wrong** signature is always
  rejected and logged; unsigned webhooks are accepted, and `WEBHOOK_REQUIRE_SIGNATURE=true` rejects them too if a
  level signs its webhooks.
- **Webhooks can arrive out of order** (the simulator sends them concurrently), so spots and gates are also
  re-synced from the simulator every `SIM_SYNC_SECONDS` (default 10 s); a visit with no event for
  `STALE_SESSION_MINUTES` is closed (its departure event was missed).
- **Time:** stored in UTC; a car's visit times use the simulator's own clock (`ServerDateTime`), because it runs at
  a different speed than real time.
- **The simulator reports only a car count per spot, not plates.** Plates come from the webhooks; a car whose
  arrival webhook was missed (backend offline) can't be identified, so the backend should run before cars arrive.
- **Roles:** an Admin has every authority; a new Operator starts with gate, light, fan and repair authorities, and
  an Admin can change them per user.
- **Penalties** are the simulator's own `penalty` events; we store and report them, we don't invent fines.
- **Secrets** (`.env`) are not in the repository; `certs/aiven-ca.pem` is Aiven's public CA certificate.

## Project structure

```
backend/fastapi_project/main.py   car flow, gate workers, lights/fans/CO, usage cycles, preventive maintenance
backend/fastapi_project/db_hook.py MySQL layer: CORS, API routers, stores every webhook, periodic simulator sync
backend/app/                      models, services (parking, audit, penalties, login attempts, users/RBAC, ...), API routes
backend/tests/                    pytest suite (runs on a separate test database)
database/schema.sql               all table definitions        web-interface/   React + Vite dashboard
docs/                             Level 1/2 checklists, event types, simulator API notes, team task split
scripts/live-demo.ps1             one-command public demo link
```

## Team

| Member | Area |
| --- | --- |
| Zhi Hong (Tee) | Automation (gates, lights, fans, CO, usage cycles, preventive maintenance) and FastAPI integration |
| Jiaying | Database, logging and security data (login attempts, audit, penalties, event log, users and authorities) |
| Jackson | Admin / RBAC user interface |
| Christen | Operations dashboard, reports and alerts interface |
