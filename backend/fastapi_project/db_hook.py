"""MySQL layer for main.py. main.py only calls two things:

    db_hook.setup(app)             once, right after `app = FastAPI(...)`
    await db_hook.record(request)  at the top of POST /webhook

It adds: login with Admin/Operator roles, the dashboard + history API (/api/...), and a
database copy of every webhook (spots, gates, car visits, penalties) for the dashboard.
main.py's own car flow (queues, spot choice, gates, charges) does not depend on any of this:
webhooks are stored by a separate background worker, and database errors are only logged.
"""

import asyncio
import logging
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

# backend/ holds the `app` package (models, services, API routes)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.routes import auth, control, dashboard, history  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.init_db import apply_schema, check_schema, seed_admin  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services import webhook_handlers as webhooks  # noqa: E402
from app.services.simulator_client import get_simulator  # noqa: E402
from app.services.sync import has_spots, sync_from_simulator  # noqa: E402

log = logging.getLogger("db_hook")
_queue: asyncio.Queue = asyncio.Queue()


def setup(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for r in (auth.router, dashboard.router, control.router, history.router):
        app.include_router(r)
    app.add_api_route("/health", lambda: {"status": "ok"}, methods=["GET"], tags=["meta"])
    app.add_event_handler("startup", _startup)


async def record(request: Request) -> None:
    """Hand the raw webhook to the database worker. Never raises, never waits on MySQL."""
    try:
        _queue.put_nowait(await request.body())
    except Exception:
        log.exception("Could not queue webhook for the database")


async def _startup() -> None:
    try:
        # tables from database/schema.sql (no-op if they exist) + first admin from .env
        await asyncio.to_thread(apply_schema)
        await asyncio.to_thread(check_schema)
        await asyncio.to_thread(seed_admin)
    except Exception as e:
        print(f"[DB] MySQL not ready, dashboard/login won't work (use a phone hotspot?): {e}")
        return
    asyncio.create_task(_worker())
    await asyncio.to_thread(_sync_if_empty)


def _sync_if_empty() -> None:
    """One list-parking-spots + list-barriers call to fill the spots / gates tables.
    Retried on later webhooks while the level isn't loaded yet (0 spots)."""
    try:
        with SessionLocal() as db:
            if not has_spots(db):
                counts = sync_from_simulator(db, get_simulator())
                print(f"[DB] Synced {counts['spots']} spots and {counts['gates']} gates from the simulator.")
    except Exception as e:
        print(f"[DB] Could not sync spots from the simulator yet: {e}")


def _store(body: bytes) -> None:
    with SessionLocal() as db:
        # signature check, EventId dedup, SequenceId check, raw copy in `events`
        payload, rejected = webhooks.receive(db, body)
        if rejected or payload.get("EventClass") == "payment_made":
            # payments stay a raw WEBHOOK row: validating them needs the charge, which main.py keeps in memory
            return
        # car parked / left its spot / left the park, gates, broken/fixed, penalties, CO alerts
        webhooks.process(db, payload)


async def _worker() -> None:
    """One at a time, in arrival order, so SequenceId checks and car state stay consistent."""
    while True:
        body = await _queue.get()
        try:
            await asyncio.to_thread(_sync_if_empty)
            await asyncio.to_thread(_store, body)
        except Exception:
            log.exception("Storing webhook failed")
        finally:
            _queue.task_done()
