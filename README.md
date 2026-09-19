# IoT Hack: Car Park Management System

A web-based car park manager for the Park Simulator. It detects arriving cars, guides them
to spots, charges them at the exit, and shows live status on a dashboard.

**Stack:** FastAPI · SQLAlchemy · MySQL 8 (Aiven) · React + Vite (`web-interface/`)

- **Setup and run the backend:** [backend/README.md](backend/README.md)
- **Level 1 brief and progress checklist:** [docs/LEVEL1.md](docs/LEVEL1.md)
- **Database tables, queries, webhook handling:** [database/README.md](database/README.md)

## How it fits together

```
 Simulator ──webhook──▶ POST /webhook ──┬─▶ main.py  entry queue: pick spot → open gate → goto
    ▲                                   ├─▶ main.py  exit queue:  charge once → Gate B: open → leavepark
    │                                   └─▶ db_hook.py: checked copy → MySQL (spots, gates, visits, events)
    └──────── REST commands (goto, open/close gate, charge) ◀── main.py
 Browser (web-interface) ──▶ /api/auth, /api/dashboard, /api/history ──▶ MySQL
```

- MySQL is the **source of truth for the dashboard**. The simulator's `list-*` endpoints
  are costly, so they are only called once per level (on startup, or `POST /api/control/sync`).
- Every webhook payload is saved raw in `events` (`event_type = WEBHOOK`) before it is processed.

## Project structure

```
.
├── .env.example                  # copy to .env and fill in (see backend/README.md step 4)
├── backend/
│   ├── requirements.txt
│   ├── fastapi_project/
│   │   ├── main.py               # THE app (Tee): uvicorn main:app. Webhook, entry/exit/Gate B workers, simulator routes
│   │   └── db_hook.py            # MySQL add-on: login, dashboard API, stores every webhook
│   ├── app/                      # database library used by db_hook.py
│   │   ├── config.py             # settings loaded from .env
│   │   ├── db/                   # engine (Aiven TLS), SessionLocal, init_db (tables + first admin)
│   │   ├── models/               # one file per table: user, parking_spot, gate, parking_session, event
│   │   ├── schemas/              # Pydantic response models (frontend contract)
│   │   ├── core/security.py      # password hashing + JWT
│   │   ├── api/                  # role guards + /api/auth, /api/dashboard, /api/control, /api/history
│   │   └── services/
│   │       ├── simulator_client.py  # simulator login + every API call
│   │       ├── sync.py              # once-per-level upsert of spots + gates
│   │       ├── parking.py           # allocation (FOR UPDATE SKIP LOCKED), parked, exit, charge, payment, departure
│   │       ├── webhook_handlers.py  # signature, dedup, sequence checks, database-only handlers
│   │       ├── components.py        # gate state, broken / fixed
│   │       └── billing.py           # charge calculation
│   └── tests/
├── web-interface/                # React frontend (npm install, npm run dev)
├── database/
│   ├── schema.sql                # THE table definitions (source of truth)
│   ├── queries.sql               # dashboard + debugging queries
│   └── README.md
├── certs/aiven-ca.pem            # Aiven CA cert (public, safe to commit)
└── docs/
    ├── LEVEL1.md                 # level brief + checklist
    ├── aiven-mysql-setup.md
    └── simulator-api.md
```

## Roles

| Role | Can |
| --- | --- |
| Operator | view dashboard/history, open/close/repair gates, move cars, repair spots |
| Admin | everything Operator can do, plus create users, resync from simulator, trigger test webhook |

## Notes

- All timestamps are **UTC**. Use `app.models.utcnow()`, never `datetime.now()`.
- `database/schema.sql` defines the tables; startup runs it with `CREATE TABLE IF NOT EXISTS`.
  Existing tables are never altered: change `schema.sql` **and** the model, then `python -m app.db.init_db --reset` (dev only).
