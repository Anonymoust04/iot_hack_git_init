"""Database connection (the "database.py" of the project).

    from app.db.session import SessionLocal, get_db, Base, utcnow
"""

import ssl
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import URL, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import ROOT_DIR, Settings, get_settings


def utcnow() -> datetime:
    """All times are stored as naive UTC (MySQL DATETIME has no time zone).
    Always use this instead of datetime.now() so billing math never mixes time zones."""
    return datetime.now(UTC).replace(tzinfo=None)


def build_url(s: Settings, database: str | None = None) -> URL:
    return URL.create(
        "mysql+pymysql",
        username=s.db_user,
        password=s.db_password,  # URL.create escapes special characters for us
        host=s.db_host,
        port=s.db_port,
        database=database if database is not None else s.db_name,
        query={"charset": "utf8mb4"},
    )


def build_connect_args(s: Settings) -> dict:
    # Make NOW()/CURRENT_TIMESTAMP use UTC on every connection, whatever the server's zone.
    args: dict = {"init_command": "SET time_zone = '+00:00'", "connect_timeout": 10}
    if s.db_ssl_ca:
        ca_path = Path(s.db_ssl_ca) if Path(s.db_ssl_ca).is_absolute() else ROOT_DIR / s.db_ssl_ca
        if not ca_path.is_file():
            raise RuntimeError(f"DB_SSL_CA file not found: {ca_path} (see docs/aiven-mysql-setup.md)")
        # Full verification: server cert must be signed by the Aiven CA AND match the hostname.
        args["ssl"] = ssl.create_default_context(cafile=str(ca_path))
    return args


def make_engine(s: Settings, database: str | None = None):
    return create_engine(
        build_url(s, database),
        connect_args=build_connect_args(s),
        # READ COMMITTED: no InnoDB "gap locks" on lookups. With MySQL's default (REPEATABLE READ),
        # two cars arriving together both gap-lock "no session for this plate yet" and then
        # deadlock on INSERT. Row locks (FOR UPDATE SKIP LOCKED) still work the same.
        isolation_level="READ COMMITTED",
        pool_pre_ping=True,   # test connections before use (Aiven drops idle ones)
        pool_recycle=280,     # recycle before MySQL's wait_timeout
        pool_size=s.db_pool_size,
        max_overflow=2,
    )


engine = make_engine(get_settings())
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
