# IoT Hack: Car Park Management System

A web-based car park manager for the Park Simulator. It detects arriving cars, guides them
to spots, charges them at the exit, and shows live status on a dashboard.

**Stack:** FastAPI · SQLAlchemy · MySQL 8 · frontend TBD

## How it fits together

```
 Simulator ──webhook──▶  POST /webhook  ──▶ events (raw) ──▶ webhook_handlers.py → parking.py
    ▲                                                          │
    └──────── REST commands (goto, open gate, charge) ◀────────┘
                                                              DB (MySQL)
 Browser (dashboard) ──▶ /api/auth, /api/dashboard, /api/control, /api/history ──▶ ▲
```

- Our DB is the **source of truth for the dashboard**. The simulator's `list-*` endpoints
  cost money, so they are only used for a one-off sync (`POST /api/control/sync`).
- Every webhook payload is saved raw in `events` (`event_type = WEBHOOK`) before it is processed.

## Project structure

```
.
├── .env.example            # copy to .env and fill in
├── backend/
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py         # FastAPI app, CORS, startup (create tables, seed admin)
│   │   ├── config.py       # settings loaded from .env
│   │   ├── db/session.py   # "database.py": engine (Aiven TLS), SessionLocal, get_db, utcnow
│   │   ├── db/init_db.py   # applies database/schema.sql + seeds admin; `python -m app.db.init_db`
│   │   ├── models/         # one file per table: user, parking_spot, gate, parking_session, event
│   │   ├── schemas/        # Pydantic request/response models (frontend contract)
│   │   ├── core/security.py# password hashing + JWT
│   │   ├── api/
│   │   │   ├── deps.py     # get_db, current user, role guards (AdminUser / OperatorUser)
│   │   │   └── routes/     # auth, dashboard, control, history, webhook
│   │   └── services/
│   │       ├── simulator_client.py  # every simulator API call
│   │       ├── sync.py              # level-start upsert of spots + gates
│   │       ├── parking.py           # allocation (FOR UPDATE SKIP LOCKED), parked, exit, charge, departure
│   │       ├── webhook_handlers.py  # webhook -> parking.py + simulator calls (TODO: payload format)
│   │       └── billing.py           # charge calculation
│   └── tests/
├── frontend/               # framework TBD (see frontend/README.md)
├── database/
│   ├── schema.sql          # THE table definitions (source of truth)
│   ├── queries.sql         # dashboard + debugging queries
│   └── README.md           # tables explained, allocation/departure, how to test
├── certs/aiven-ca.pem      # Aiven CA cert (public, safe to commit)
└── docs/
    ├── aiven-mysql-setup.md  # connect to Aiven
    └── simulator-api.md      # condensed simulator API reference
```

## Setup

1. **MySQL:** the team shares one Aiven MySQL database. Follow **[docs/aiven-mysql-setup.md](docs/aiven-mysql-setup.md)**.
   ⚠️ The venue Wi-Fi blocks MySQL, so use a phone hotspot.
2. **Env:** `cp .env.example .env`, then fill in the `DB_*` values, the simulator URL/creds, and `JWT_SECRET`.
   Check the DB connection with `cd backend && python -m app.db.init_db`.
3. **Backend:**
   ```bash
   cd backend
   python -m venv .venv
   .venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
   pip install -r requirements.txt
   uvicorn app.main:app --reload --port 8000
   ```
   Tables are created automatically on first start, and an admin user is seeded from
   `BOOTSTRAP_ADMIN_*`.
4. **Docs:** http://localhost:8000/docs. Click *Authorize* and log in as admin.
5. **Webhook:** point the simulator's webhook URL at `http://<your-ip>:8000/webhook`
   (use ngrok or similar if the simulator can't reach your machine), then call
   `POST /api/control/test-webhook` and check the `events` table.
6. **Tests:** `cd backend && pytest`. They run on real MySQL in a separate database `TEST_DB_NAME`
   (`carpark_test`), which is created automatically and wiped. The real `DB_NAME` is never touched.

## Roles

| Role | Can |
| --- | --- |
| Operator | view dashboard/history, open/close/repair gates, move cars, repair spots |
| Admin | everything Operator can do, plus create users, resync from simulator, trigger test webhook |

## Level 1 TODO

- [ ] Capture real webhook payloads (`/test`, then look at `events` WHERE event_type = WEBHOOK) and fill in `services/webhook_handlers.py`
- [ ] Arrival: pick spot → open entry gate → `goto` spot (or `leavepark` if full)
- [ ] Exit: calculate charge → `charge` once → open exit gate → free spot
- [ ] On startup: login + one-off sync
- [ ] Pick frontend framework and build the dashboard, control, history, and login pages
- [ ] Presentation for final judging

## Notes

- All timestamps are **UTC** (MySQL `DATETIME` has no timezone). Always use
  `app.models.utcnow()`, never `datetime.now()`, or the billing math will break.
- `database/schema.sql` defines the tables; `init_db` and app startup run it with `CREATE TABLE IF NOT EXISTS`.
  Existing tables are never altered: change `schema.sql` **and** the model, then `python -m app.db.init_db --reset` (dev only).
