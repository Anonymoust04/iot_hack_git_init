import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api.routes import auth, control, dashboard, history, webhook
from app.config import get_settings
from app.core.security import hash_password
from app.db.session import Base, SessionLocal, engine
from app.models import Role, User

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


def seed_admin() -> None:
    settings = get_settings()
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.role == Role.ADMIN)) is None:
            db.add(User(
                username=settings.bootstrap_admin_username,
                password_hash=hash_password(settings.bootstrap_admin_password),
                role=Role.ADMIN,
            ))
            db.commit()
            log.info("Seeded admin user '%s'", settings.bootstrap_admin_username)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)  # TODO: switch to Alembic migrations if schema churns
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
