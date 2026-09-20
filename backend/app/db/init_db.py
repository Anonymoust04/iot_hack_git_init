"""Apply database/schema.sql to MySQL and seed the first admin user.

    cd backend
    python -m app.db.init_db           # check connection, create missing tables, seed admin
    python -m app.db.init_db --reset   # DROP all our tables first (deletes ALL data!), then as above

Safe to run many times: CREATE TABLE IF NOT EXISTS skips existing tables.
NOTE: it only ALTERs existing tables to ADD the columns listed in UPGRADES below. For any other change,
use --reset (dev data only) or run the ALTER TABLE yourself.
"""

import logging
import sys

from sqlalchemy import Engine, inspect, select, text

from app.config import ROOT_DIR, get_settings
from app.core.security import hash_password
from app.db.session import Base, SessionLocal, engine
from app.models import Role, User
from app.models.payment_record import PaymentRecord  # noqa: F401  (register model for schema checks)

log = logging.getLogger(__name__)

SCHEMA_FILE = ROOT_DIR / "database" / "schema.sql"
# Child tables first (foreign keys). Includes tables from the old scaffold so --reset cleans them.
ALL_TABLES = ["events", "payment_records", "parking_sessions", "parking_spots", "gates", "user_permissions", "users", "event_logs", "zones"]


def schema_statements() -> list[str]:
    """Split schema.sql into statements (on ';' at end of line), dropping '--' comments."""
    lines = [ln for ln in SCHEMA_FILE.read_text(encoding="utf-8").splitlines()
             if not ln.strip().startswith("--")]
    return [stmt.strip() for stmt in "\n".join(lines).split(";\n") if stmt.strip().rstrip(";")]


def apply_schema(eng: Engine = engine) -> None:
    with eng.begin() as conn:
        for stmt in schema_statements():
            conn.execute(text(stmt.rstrip(";")))
    upgrade_schema(eng)


# Columns added to existing tables after they were first created (CREATE TABLE IF NOT EXISTS can't).
# Only ADDs what is missing: never drops or changes data. Safe to run on every start.
UPGRADES = {
    # Level 2 merge: two versions of user_permissions existed; the table needs all of these
    "user_permissions": [
        ("enabled", "BOOLEAN NOT NULL DEFAULT TRUE"),
        ("granted_by", "VARCHAR(64) NULL"),
        ("granted_at", "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"),
    ],
}
MYSQL_DUPLICATE_COLUMN = 1060


def upgrade_schema(eng: Engine = engine) -> None:
    insp = inspect(eng)
    for table, columns in UPGRADES.items():
        if not insp.has_table(table):
            continue
        existing = {c["name"]: c for c in insp.get_columns(table)}
        for name, ddl in columns:
            if name in existing:
                continue
            try:
                with eng.begin() as conn:
                    conn.execute(text(f"ALTER TABLE `{table}` ADD COLUMN `{name}` {ddl}"))
                log.info("Schema upgrade: added %s.%s", table, name)
            except Exception as exc:  # another backend added it at the same moment
                if getattr(getattr(exc, "orig", None), "args", [None])[0] != MYSQL_DUPLICATE_COLUMN:
                    raise
    # an older user_permissions had permission VARCHAR(32); names now go up to 64
    if insp.has_table("user_permissions"):
        perm = next((c for c in inspect(eng).get_columns("user_permissions") if c["name"] == "permission"), None)
        if perm is not None and getattr(perm["type"], "length", 64) < 64:
            with eng.begin() as conn:
                conn.execute(text("ALTER TABLE `user_permissions` MODIFY `permission` VARCHAR(64) NOT NULL"))
            log.info("Schema upgrade: widened user_permissions.permission to 64")


def check_schema(eng: Engine = engine) -> None:
    """Fail loudly if the real tables are missing columns the models expect.
    Happens when schema.sql gained a column after the tables were created
    (CREATE TABLE IF NOT EXISTS never changes an existing table)."""
    insp = inspect(eng)
    problems = []
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            problems.append(f"table {table.name} is missing")
            continue
        real = {c["name"] for c in insp.get_columns(table.name)}
        missing = sorted(c.name for c in table.columns if c.name not in real)
        if missing:
            problems.append(f"{table.name} is missing columns {missing}")
    if problems:
        raise RuntimeError(
            "Database is out of date with database/schema.sql: " + "; ".join(problems)
            + ". Fix: ALTER TABLE to add them (keeps data), or `python -m app.db.init_db --reset` (DELETES data)."
        )


def drop_all(eng: Engine = engine) -> None:
    with eng.begin() as conn:
        for table in ALL_TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS `{table}`"))


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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(f"Connecting to {engine.url.render_as_string(hide_password=True)} ...")
    with engine.connect() as conn:
        version = conn.scalar(text("SELECT VERSION()"))
        cipher = conn.execute(text("SHOW SESSION STATUS LIKE 'Ssl_cipher'")).first()
        print(f"OK: MySQL {version}, TLS: {(cipher[1] if cipher else '') or 'OFF (not encrypted)'}")

    if "--reset" in sys.argv:
        print("--reset: dropping tables:", ", ".join(ALL_TABLES))
        drop_all()

    apply_schema()
    check_schema()
    seed_admin()
    print("Tables:", ", ".join(sorted(inspect(engine).get_table_names())))
    print("Done.")


if __name__ == "__main__":
    main()
