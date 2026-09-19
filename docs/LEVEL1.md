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

Backend = `backend/fastapi_project/main.py` (car flow, Tee) + `db_hook.py` / `backend/app/` (MySQL, API).
Frontend = `web-interface/` (every page reads the backend through `src/services/api.js`).

| # | Requirement | Status | Where | Still not done |
| --- | --- | --- | --- | --- |
| 1 | Call the simulator API to **log in**, **list parking spots**, **open/close gates** | **Done** | Everything logs in with `SIM_EMAIL`/`SIM_PASSWORD` from `.env` (`SIM_BASE_URL=http://127.0.0.1:9898/api/v1`). Spots + gates are read on startup and every `SIM_SYNC_SECONDS`. Gate Control buttons really open/close the barriers (tested live). | Tee's newer login on `main` (`f495283`) never fetches its token: keep this branch's `main.py` when merging. |
| 2 | Receive and handle simulator events, e.g. **a car arriving at the entrance** | **Done** | `POST /webhook`: `main.py` queues entry/exit cars; `db_hook` stores every event (signature checked, duplicates ignored, sequence gaps logged). | – |
| 3 | Let cars enter and **guide them to available spots** | **Partly done** | `main.py` (Tee): Gate A worker opens `GateA` → `goto` spot → closes. | Needs a longer live run. (Cars stopped parking on 2026-09-19 because a merge left a broken duplicate simulator login in `call_simulator_api`; fixed, and cars park again.) |
| 4 | Handle exits, **calculate the correct charge**, then ask the car to **leavepark** | **Partly done** | `main.py`: charged once → open `GateB` → `leavepark` → close. | **Simulator penalties prove the charge is wrong:** "charged 1.00, should be 4.00". `main.py` bills the *planned* duration; it must bill the minutes actually spent. Charges are not stored in MySQL. |
| 5 | **Website with a dashboard** showing system status | **Done** | Dashboard page: totals, free / occupied / cars inside, parking grid, gate control, recent activity, system online/offline. | – |
| 6 | Keep parking data and the dashboard **up to date** (gates, spots) | **Done** | Webhooks update MySQL live; the simulator's own view is re-read every `SIM_SYNC_SECONDS` (10 s) to correct out-of-order webhooks; pages refresh every `VITE_REFRESH_MS` (3 s). Tested live: dashboard occupied spots = simulator. | – |
| 7 | Show **occupied/free spots by zone** and **gate status**; cars leave when the park is full | **Done** | Parking grid: free / occupied per zone; Gate Control: live entrance / exit state, shows Opening / Closing at once after a button press; "Cars Inside" counts cars past the entrance (not the queue outside); "Car Park Full" warning. | – |
| 8 | **Authentication and roles**: Admin and Operator; Operator can control components and see status | **Partly done** | Login page uses the backend (JWT, bcrypt); Account page shows the real role; API routes check the role. | No page to **create users** (Admin only: use `/docs` → `POST /api/auth/users`). Operator can only control **gates** from the UI (no spot repair / move car buttons). `main.py`'s own routes (`/barrier-gates/...`, `/car/...`) are **open without login**. |
| 9 | **Log** arrivals / parking time / departure / charges so they can be **searched efficiently** | **Partly done** | Arrival, parked, left spot, departure are stored (indexed by plate, status, time). Vehicle Search / Details pages search by plate. Recent Activity lists them. | **Charges are not stored**: `main.py` keeps them in memory. The simulator gives no make / model / colour, so those show "—". |
| 10 | **Presentation** for the final judging | **Not done** | – | Slides + live demo. |
| – | **Full capacity** | **Done** | All 30 spots used; a spot is freed as soon as its car leaves it. | – |

## Still not done, in order

1. **Long live run** of a full level: watch for cars turned away while spots are free (`main.py`'s in-memory spot table only frees a spot on `Park/CarOut`).
2. **Bill the real time parked** in `main.py` and store the charge in MySQL (requirements 4 and 9):
   the simulator's penalties say exactly what it expects.
3. Put `main.py`'s own `/barrier-gates`, `/car` routes behind login (requirement 8).
4. **Admin user management page** and more Operator controls (repair spot, move car) in the frontend (requirement 8).
5. **Full run with the simulator**: start the backend and frontend, let a level run, check
   `GET /api/history/events?event_type=HANDLER_ERROR` and `?event_type=PENALTY` stay empty.
6. **Presentation**: architecture diagram (backend README), webhook safety (signature, dedup,
   sequence checks), per-zone dashboard, a live demo.

Small UI gaps: the **View All** button in Recent Activity does nothing yet; Vehicle Details shows a duration only after the car has left.

## Found in the live run (2026-09-19)

- **The simulator does not sign webhooks** (`"Signature": null` on all 2,557 received). They were all being
  rejected, so the dashboard never changed. Unsigned webhooks are now accepted; a *wrong* signature is still
  rejected. Set `WEBHOOK_REQUIRE_SIGNATURE=true` if a later level signs them.
- **Webhooks arrive out of order** (`SEQUENCE_OUT_OF_ORDER` in the event log), so spots and gates are also
  re-read from the simulator every `SIM_SYNC_SECONDS`. Car visits with no event for `STALE_SESSION_MINUTES`
  are closed (`SESSION_EXPIRED`).
- `ENTRY1`'s `detectedCars` count is not a live queue length (it was 38 with an empty park).

## To confirm with the simulator / organizers

- **EV charge (Tee):** `main.py` sends `chargingCost = minutes × 2` (×3 in total). The docs say
  "multiply by two if electric", which may mean `chargingCost = minutes` (×2 in total). Check for penalties.
- **Gate names:** the level has `gateA`, `gateB`, `gateC`. The dashboard labels `ENTRY_GATE` (`gateA`) as the
  entrance and `EXIT_GATE` (`gateB`) as the exit (backend `.env`). Watch one car go through to confirm.
- **Payments:** does Level 1 send `payment_made`? If fake payments must block the exit, `main.py`
  has to wait for a valid one before opening `GateB`.
- **Spot types:** may a normal car use an Electric/Accessible bay when normal bays are full?
