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
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

# backend/ holds the `app` package (models, services, API routes)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.routes import auth, control, dashboard, history  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.init_db import apply_schema, check_schema, seed_admin  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services import parking  # noqa: E402
from app.services import webhook_handlers as webhooks  # noqa: E402
from app.services.simulator_client import get_simulator  # noqa: E402
from app.services.sync import has_spots, sync_from_simulator  # noqa: E402

log = logging.getLogger("db_hook")
_queue: asyncio.Queue = asyncio.Queue()


def setup(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origin_list,
        allow_origin_regex=get_settings().cors_origin_regex or None,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for r in (auth.router, dashboard.router, control.router, history.router):
        app.include_router(r)
    app.add_exception_handler(OperationalError, _database_unreachable)
    app.add_api_route("/health", lambda: {"status": "ok"}, methods=["GET"], tags=["meta"])
    app.add_event_handler("startup", _startup)


async def _database_unreachable(request: Request, exc: OperationalError) -> JSONResponse:
    """MySQL down or blocked (e.g. venue Wi-Fi): tell the frontend why, instead of a bare 500."""
    log.error("MySQL unreachable during %s %s: %s", request.method, request.url.path, exc.orig)
    return JSONResponse(
        status_code=503,
        content={"detail": "Database unreachable: check the network (use a phone hotspot) and the DB_* settings in .env."},
    )


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
    await asyncio.to_thread(_sync_now)
    if get_settings().sim_sync_seconds > 0:
        asyncio.create_task(_sync_loop(get_settings().sim_sync_seconds))


async def _sync_loop(every: float) -> None:
    """Keep spots + gates equal to the simulator's own view (SIM_SYNC_SECONDS), on top of webhooks."""
    while True:
        await asyncio.sleep(every)
        await asyncio.to_thread(_sync_now, quiet=True)


def _sync_now(quiet: bool = False) -> None:
    """On every backend start: copy the simulator's current spots + gates, so the dashboard starts
    from the real state (cars may have moved while we were down). Webhooks keep it live after that."""
    try:
        with SessionLocal() as db:
            counts = sync_from_simulator(db, get_simulator(), log_event=not quiet)
            parking.expire_stale_sessions(db, timedelta(minutes=get_settings().stale_session_minutes))
            if not quiet:
                print(f"[DB] Synced {counts['spots']} spots and {counts['gates']} gates from the simulator.")
    except Exception as e:
        print(f"[DB] Could not sync spots from the simulator: {e}")


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
