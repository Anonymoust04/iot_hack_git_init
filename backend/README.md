# FastAPI Parking Simulator Backend

This backend application integrates with **ParkingSimulator** to automate parking management, including entrance gate opening, spot assignment (range `S1`..`S30`), EV charging fee calculations, payment processing, exit gate opening, and non-blocking auto-gate closure.

The car flow is in `fastapi_project/main.py`. `fastapi_project/db_hook.py` adds **MySQL 8 (Aiven)** on top: login with Admin/Operator roles, the dashboard + history API, and a database copy of every webhook.

---

## 🚀 Quick Start Guide

### 1. Prerequisites
- Python 3.12+ installed on your system.
- `ParkingSimulator-win-x64` executable running locally.
- Access to the team's Aiven MySQL database. ⚠️ **The venue Wi-Fi blocks MySQL: use a phone hotspot.**

---

### 2. Virtual Environment Setup

Navigate to the `backend` directory:
```powershell
cd backend
```

Create and activate a virtual environment:

**Windows (PowerShell):**
```powershell
python -m venv fastapi-env
.\fastapi-env\Scripts\Activate.ps1
```

**Linux / macOS:**
```bash
python3 -m venv fastapi-env
source fastapi-env/bin/activate
```

> If PowerShell blocks the activate script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

---

### 3. Install Dependencies

Install all required packages (FastAPI, SQLAlchemy, MySQL driver, auth, tests):
```powershell
pip install -r requirements.txt
```

---

### 4. Configure `.env` (database + simulator login)

In the **repo root** (one level above `backend/`), copy the example file and fill it in:
```powershell
copy ..\.env.example ..\.env
```

| Key | Value |
| :--- | :--- |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` | Aiven service → Overview → Connection information ([guide](../docs/aiven-mysql-setup.md)) |
| `DB_SSL_CA` | `certs/aiven-ca.pem` (already in the repo) |
| `SIM_BASE_URL` | `http://127.0.0.1:9898/api/v1` (the simulator's `ListenAddress` + `/api/v1`) |
| `SIM_EMAIL`, `SIM_PASSWORD` | `Name` / `Password` from the simulator's `settings.json` (used by the database sync and `/api/control`; `main.py` has its own token) |
| `JWT_SECRET` | any long random string |
| `BOOTSTRAP_ADMIN_USERNAME`, `BOOTSTRAP_ADMIN_PASSWORD` | the first dashboard admin, created on first start |

Check the database connection and create the tables:
```powershell
python -m app.db.init_db
```
It should print `OK: MySQL 8.x, TLS: TLS_AES_...` and the 5 tables.

---

### 5. Configure Parking Simulator (`settings.json`)

Ensure `ParkingSimulator-win-x64/settings/settings.json` has `WebhookUrl` set to your FastAPI server:

```json
{
  "TeamName": "git init",
  "ListenAddress": "http://0.0.0.0:9898",
  "WebhookUrl": "http://127.0.0.1:8000/webhook",
  "ParkingSpeedMuliplier": 1,
  "MinParkingTime": 1,
  "MaxParkingTime": 5,
  "Name": "admin",
  "Password": "admin",
  "GameSpeedMultiplier": 1.0,
  "lvl2Password": "",
  "lvl3Password": ""
}
```

> **Note:** Ensure invalid `//` comments are removed from `settings.json` so the simulator can parse it.

---

### 6. Run the Backend Server

Start the simulator first, then navigate into the `fastapi_project` directory and start Uvicorn:

```powershell
cd fastapi_project
uvicorn main:app --reload
```

The server will start at: **`http://127.0.0.1:8000`**

On startup it: starts the entry / exit / Gate B workers and closes the exit gate (`main.py`), then creates any missing tables, seeds the admin user and copies spots + gates from the simulator into MySQL **once** (`db_hook.py`). If the level wasn't loaded yet, the copy is retried on the next webhook; `POST /api/control/sync` (admin) forces it.

If MySQL is unreachable, the car flow still runs; only login and the dashboard API stop working.

> **Start the backend before cars arrive.** A car whose arrival webhook was missed (backend stopped) stays stuck at `ENTRY1`: the simulator only reports a count there, not the plates. Restart the level in the simulator.

---

### 7. Try It

1. Open **http://127.0.0.1:8000/docs**, click **Authorize**, and enter a dashboard account's **username** and **password**. The first admin account is seeded from `BOOTSTRAP_ADMIN_USERNAME` and `BOOTSTRAP_ADMIN_PASSWORD` in `.env`; changing those settings later does not reset an existing account. Swagger sends the credentials as form fields to `/api/auth/login` and adds the returned bearer token to `/api/control` requests.
   The simulator's `POST http://127.0.0.1:9898/api/v1/auth/login` uses JSON `email`/`password` and issues a different token; do not paste that token into this Authorize dialog.
2. `GET /api/dashboard`: free / occupied spots per zone and gate states.
3. Let cars arrive in the simulator, then `GET /recent-arrivals` or `GET /api/history/sessions`.

---

### 8. Run the Tests

From `backend/` with `fastapi-env` active:
```powershell
pytest
```
Tests use a separate database `carpark_test` (auto-created and wiped), never your real one. If MySQL is unreachable the database tests are **skipped**, not failed: check for `skipped` in the summary.

---

## 📡 API Endpoints Overview

Everything under `/api/` (except login) needs a logged-in dashboard user (**Authorize** in `/docs`). The simulator routes from `main.py` (`/list-*`, `/barrier-gates/...`, `/car/...`) are open to the caller; the backend logs in to the simulator using `SIM_EMAIL` and `SIM_PASSWORD` and refreshes its simulator token when needed.

| Endpoint | Method | Who | Description |
| :--- | :--- | :--- | :--- |
| **`/docs`** | `GET` | | Interactive Swagger API documentation |
| **`/webhook`** | `POST` | simulator | Webhook listener: queues cars (`main.py`), stores a checked copy in MySQL (`db_hook.py`) |
| **`/api/auth/login`** | `POST` | | Log in, returns a JWT |
| **`/api/auth/users`** | `GET` / `POST` | Admin | List / create Admin and Operator users |
| **`/api/dashboard`** | `GET` | any | Free / reserved / occupied spots **per zone**, gate states, `park_full` |
| **`/api/dashboard/spots`**, **`/api/dashboard/gates`** | `GET` | any | Every spot / gate with its current state |
| **`/api/history/sessions`** | `GET` | any | Search car visits by plate, status, date (entry, parked, exit, charges) |
| **`/api/history/events`** | `GET` | any | Event log (arrivals, charges, penalties, raw webhooks...) |
| **`/api/control/gates/{name}/{action}`** | `POST` | Operator | `open` / `close` / `repair` a barrier gate (logged in) |
| **`/api/control/cars/{plate}/goto/{destination}`** | `POST` | Operator | Move a car (logged in) |
| **`/api/control/sync`** | `POST` | Admin | Re-copy spots + gates from the simulator (costly: once per level) |
| **`/recent-arrivals`** | `GET` | open | Recently arrived cars (in memory, since last restart) |
| **`/all-events`** | `GET` | open | Raw webhook logs (in memory, last 100) |
| **`/list-parking-spots`**, **`/list-barriers`**, ... | `GET` | open | Pass-through to the simulator's `list-*` (costly, use sparingly) |
| **`/barrier-gates/{name}/open`** / **`close`** / **`repair`** | `POST` | open | Manually open / close / repair a barrier gate |
| **`/car/{name}/goto/{destination}`** | `POST` | open | Direct a car to a parking spot, `exit` or `leavepark` |
| **`/car/{name}/charge`** | `POST` | open | Request payment for parking and EV charging |

---

## ✨ System Features & Architecture

```
Simulator ──webhook──▶ /webhook ──┬─▶ entry_queue ─▶ pick free spot ─▶ open gate ─▶ goto spot ─▶ close gate   (main.py)
                                  ├─▶ exit_queue  ─▶ charge once   ─▶ gate_b_queue ─▶ open GateB ─▶ leavepark
                                  └─▶ check signature / EventId / SequenceId ─▶ MySQL                       (db_hook.py)
Dashboard ◀── /api/dashboard, /api/history ◀── MySQL
```

1. **Parallel Dual-Queue Architecture (`entry_queue` & `exit_queue`)**:
   - Entrance and Exit event streams run concurrently on separate background workers.
   - Exiting cars never wait behind entering cars.

2. **Thread-Safe Spot Allocation**:
   - Randomly selects available spots from `S1` through `S30`.
   - Uses `asyncio.Lock()` to prevent race conditions when cars enter simultaneously.

3. **EV Charging & Payment Rules**:
   - `parkingCost` = total minutes parked.
   - `chargingCost` = `parkingCost * 2` if the vehicle is an EV/Electric car.
   - Single-charge enforcement prevents double-charging penalties.

4. **Automated Non-Blocking Gate Closure**:
   - Automatically closes barrier gates 5 seconds after opening to allow cars to pass safely.

5. **MySQL Database** (`db_hook.py`, tables explained in [`../database/README.md`](../database/README.md)):
   - Every webhook is stored after an MD5 signature check, `EventId` dedup and `SequenceId` gap check, by a background worker, so it never slows the car flow down.
   - Keeps `parking_spots` (free / occupied, per zone), `gates` (state, broken), `parking_sessions` (each car's visit) and `events` (history, penalties) up to date for the dashboard.
   - Login with Admin / Operator roles (JWT, bcrypt).
