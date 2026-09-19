# IoT Hack: Car Park Management System

A web-based car park manager for the Park Simulator. It detects arriving cars, guides them
to spots, charges them at the exit, and shows live status on a dashboard.

**Stack:** FastAPI · SQLAlchemy · Supabase (Postgres) · frontend TBD

## How it fits together

```
 Simulator ──webhook──▶  POST /webhook  ──▶ event_logs ──▶ services/parking.py handlers
    ▲                                                          │
    └──────── REST commands (goto, open gate, charge) ◀────────┘
                                                              DB (Supabase)
 Browser (dashboard) ──▶ /api/auth, /api/dashboard, /api/control, /api/history ──▶ ▲
```

- Our DB is the **source of truth for the dashboard**. The simulator's `list-*` endpoints
  cost money, so they are only used for a one-off sync (`POST /api/control/sync`).
- Every webhook payload is saved raw in `event_logs` before it is processed.

## Project structure

```
.
├── .env.example            # copy to .env and fill in
├── backend/
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py         # FastAPI app, CORS, startup (create tables, seed admin)
│   │   ├── config.py       # settings loaded from .env
│   │   ├── db/session.py   # SQLAlchemy engine, session, Base
│   │   ├── models/         # tables: users, parking_spots, gates, zones, parking_sessions, event_logs
│   │   ├── schemas/        # Pydantic request/response models (frontend contract)
│   │   ├── core/security.py# password hashing + JWT
│   │   ├── api/
│   │   │   ├── deps.py     # get_db, current user, role guards (AdminUser / OperatorUser)
│   │   │   └── routes/     # auth, dashboard, control, history, webhook
│   │   └── services/
│   │       ├── simulator_client.py  # every simulator API call
│   │       ├── parking.py           # spot allocation, sync, webhook event handlers (TODO)
│   │       └── billing.py           # charge calculation
│   └── tests/
├── frontend/               # framework TBD (see frontend/README.md)
└── docs/simulator-api.md   # condensed simulator API reference
```

## Setup

1. **Env:** `cp .env.example .env`, then fill in `DATABASE_URL` (Supabase → Project Settings →
   Database → Connection string → URI; use the Session pooler), the simulator URL/creds, and `JWT_SECRET`.
2. **Backend:**
   ```bash
   cd backend
   python -m venv .venv
   .venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
   pip install -r requirements.txt
   uvicorn app.main:app --reload --port 8000
   ```
   Tables are created automatically on first start, and an admin user is seeded from
   `BOOTSTRAP_ADMIN_*`.
3. **Docs:** http://localhost:8000/docs. Click *Authorize* and log in as admin.
4. **Webhook:** point the simulator's webhook URL at `http://<your-ip>:8000/webhook`
   (use ngrok or similar if the simulator can't reach your machine), then call
   `POST /api/control/test-webhook` and check the `event_logs` table.
5. **Tests:** `cd backend && pytest`

## Roles

| Role | Can |
| --- | --- |
| Operator | view dashboard/history, open/close/repair gates, move cars, repair spots |
| Admin | everything Operator can do, plus create users, resync from simulator, trigger test webhook |

## Level 1 TODO

- [ ] Capture real webhook payloads (`/test`, then look at `event_logs`) and fill in the handlers in `services/parking.py`
- [ ] Arrival: pick spot → open entry gate → `goto` spot (or `leavepark` if full)
- [ ] Exit: calculate charge → `charge` once → open exit gate → free spot
- [ ] On startup: login + one-off sync
- [ ] Pick frontend framework and build the dashboard, control, history, and login pages
- [ ] Presentation for final judging

## Notes

- Choosing Supabase means Postgres. If MySQL turns out to be mandatory, change `DATABASE_URL`
  to `mysql+pymysql://...` and `pip install pymysql`. The models only use portable types.
