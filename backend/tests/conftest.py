"""Tests run against REAL MySQL (same server as .env), in a SEPARATE database:
TEST_DB_NAME (default carpark_test). It is created automatically and WIPED by the tests.
The real database (DB_NAME) is never touched.

If MySQL is unreachable, database tests are skipped; pure unit tests still run.
"""

import os

import pytest

from app.config import get_settings

_real = get_settings()
if _real.test_db_name == _real.db_name:
    raise RuntimeError("TEST_DB_NAME must differ from DB_NAME — tests wipe the test database!")

# Point the whole app at the test database BEFORE app.db.session creates its engine.
os.environ["DB_NAME"] = _real.test_db_name
# Never talk to a real simulator from tests (a running one would overwrite the test tables)
os.environ["SIM_BASE_URL"] = "http://127.0.0.1:9/api/v1"
os.environ["SIM_SYNC_SECONDS"] = "0"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["BOOTSTRAP_ADMIN_USERNAME"] = "admin"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "admin-pw"
get_settings.cache_clear()

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db.init_db import apply_schema, drop_all  # noqa: E402
from app.db.session import SessionLocal, engine, make_engine  # noqa: E402


@pytest.fixture(scope="session")
def mysql():
    """Create + (re)build the test database once per test run."""
    s = get_settings()
    server = make_engine(s, database="")  # connect without selecting a database
    try:
        with server.begin() as conn:
            conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{s.db_name}` CHARACTER SET utf8mb4"))
    except Exception as exc:
        pytest.skip(f"MySQL not reachable ({type(exc).__name__}) — skipping database tests")
    finally:
        server.dispose()
    drop_all()
    apply_schema()
    yield engine
    engine.dispose()


@pytest.fixture
def db(mysql):
    """A session on a clean database (users kept, everything else emptied)."""
    with mysql.begin() as conn:
        for table in ("events", "payment_records", "parking_sessions", "parking_spots", "gates"):
            conn.execute(text(f"DELETE FROM {table}"))
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture(scope="session")
def client(mysql):
    from main import app

    with TestClient(app) as c:  # runs startup: apply schema + seed admin
        yield c


def login(client: TestClient, username: str, password: str) -> dict:
    r = client.post("/api/auth/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def admin_headers(client):
    return login(client, "admin", "admin-pw")
