import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, control, dashboard, history, webhook
from app.config import get_settings
from app.db.init_db import apply_schema, check_schema, seed_admin

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    apply_schema()  # CREATE TABLE IF NOT EXISTS from database/schema.sql (no-op if tables exist)
    check_schema()  # refuse to start if existing tables lack columns the code needs
    seed_admin()
    # TODO(team): on startup, login + sync_from_simulator() once (see services/parking.py)
    yield


settings = get_settings()
app = FastAPI(title="Car Park Management", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth.router, dashboard.router, control.router, history.router, webhook.router):
    app.include_router(r)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
