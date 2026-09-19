# Level 1: Car Park Management System

## The brief

Build a **working web-based car park management system** that detects arriving cars, guides them to
the right parking spots, handles payments at exit, and keeps the basic car park operation under control.

1. Read the documentation and explore the **simulator**: vehicles, parking spots, gates, events.
2. Plan the **dashboard and web interface**: what operators need to monitor and control.
3. Set up the **web server, webhook listener, and database**. Call the simulator's APIs, receive
   webhook events, send the right commands.
   - The web server shows the dashboard and other pages and makes API calls.
   - The webhook receives the simulator's calls and stores them in the database for processing and display.

**Goal:** keep the cars moving, parking spaces organized, and the car park running smoothly.
You win when the park works as expected **at full capacity**.

## Checklist

✅ done and tested · 🟡 partly done / not yet tried against the real simulator · ❌ not started

Backend = `backend/fastapi_project/main.py` (car flow, Tee) + `db_hook.py` / `backend/app/` (MySQL). Frontend = `web-interface/`.

| # | Requirement | Backend | Frontend | Notes |
| --- | --- | --- | --- | --- |
| 1 | Call the simulator API to **log in**, **list parking spots**, **open/close gates** | 🟡 | ❌ | `main.py` uses a **hardcoded token**, not a login; `db_hook` logs in with `SIM_EMAIL`/`SIM_PASSWORD` to copy the 30 spots + 3 gates. |
| 2 | Receive and handle simulator events, e.g. **a car arriving at the entrance** | ✅ | n/a | `main.py` queues entry/exit cars; `db_hook` stores every event (signature checked, duplicates ignored). |
| 3 | Let cars enter and **guide them to available spots** | 🟡 | n/a | Random free spot from `S1`–`S30` → open gate → `goto`. **Gate bug:** it opens `ENTRY1`, which isn't a gate (the entry gate is probably `gateA`). |
| 4 | Handle exits, **calculate the correct charge**, then ask the car to **leavepark** | 🟡 | n/a | Charged once → `GateB` → `leavepark`. **Charge is based on the planned duration, not the real time parked**; EV rule to confirm. |
| 5 | **Website with a dashboard** showing system status | ✅ API | 🟡 | Pages exist (Login, Dashboard, Vehicle Search/Details, Account) but show **mock data**. |
| 6 | Keep parking data and the dashboard **up to date** (gates, spots) | ✅ | ❌ | `db_hook` updates `parking_spots` / `gates` from webhooks. Frontend needs to call the API (and refresh every few seconds). |
| 7 | Show **occupied/free spots by zone** and **gate status**; cars leave when the park is full | ✅ | ❌ | `GET /api/dashboard` returns per-zone counts, gate states, `park_full`. `main.py` sends cars to `leavepark` when full. |
| 8 | **Authentication and roles**: Admin and Operator; Operator can control components and see status | 🟡 | ❌ | `/api/...` has JWT login + roles. `main.py`'s own routes (`/barrier-gates/...`, `/car/...`) are **open to anyone**. Frontend login is hardcoded users. |
| 9 | **Log** arrivals / parking time / departure / charges so they can be **searched efficiently** | 🟡 | ❌ | Visits (parked → left → departed) and events are in MySQL, indexed. **Charges are not**: `main.py` keeps them in memory. |
| 10 | **Presentation** for the final judging | n/a | n/a | ❌ Not started. |
| – | **Full capacity** | ✅ | n/a | All 30 spots (`S1`–`S30`) are used. |

## What's left, in order

1. **Full run against the simulator** (phone hotspot for MySQL): start simulator → `uvicorn main:app` →
   watch cars go in and out. Check `GET /api/history/events` for `HANDLER_ERROR`, `CHARGE_FAILED`,
   `PENALTY`, `WEBHOOK_BAD_SIGNATURE`.
2. **Connect the frontend** to the API (base URL `http://127.0.0.1:8000`):
   - Login → `POST /api/auth/login` (form fields `username`, `password`), send `Authorization: Bearer <token>`.
   - Dashboard → `GET /api/dashboard`, `GET /api/dashboard/spots`, `GET /api/dashboard/gates`.
   - Vehicle search → `GET /api/history/sessions?plate=...`; activity → `GET /api/history/events`.
   - Gate buttons → `POST /barrier-gates/{name}/open|close`.
3. **Presentation**: architecture diagram (backend README), webhook safety (signature, dedup,
   sequence checks), per-zone dashboard, a live demo.

## To confirm with the simulator / organizers

- **EV charge (Tee):** `main.py` sends `chargingCost = minutes × 2` (×3 in total). The docs say
  "multiply by two if electric", which may mean `chargingCost = minutes` (×2 in total). Check for penalties.
- **Gate names (Tee):** the level has `gateA`, `gateB`, `gateC`. `main.py` opens `ENTRY1` at the
  entrance, which does nothing. Tested: `gateA` and `GateB` open. Watch one car go through to confirm.
- **Payments:** does Level 1 send `payment_made`? If fake payments must block the exit, `main.py`
  has to wait for a valid one before opening `GateB`.
- **Spot types:** may a normal car use an Electric/Accessible bay when normal bays are full? (Currently no.)
