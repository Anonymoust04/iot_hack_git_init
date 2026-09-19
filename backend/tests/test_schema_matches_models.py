"""database/schema.sql is the source of truth; the SQLAlchemy models must match it.
If this fails, someone changed one without the other."""

import pytest
from sqlalchemy import inspect, text

from app.db.init_db import check_schema
from app.db.session import Base


def test_models_match_mysql_tables(mysql):
    insp = inspect(mysql)
    for table in Base.metadata.sorted_tables:
        db_cols = {c["name"]: c for c in insp.get_columns(table.name)}
        model_cols = {c.name: c for c in table.columns}
        assert set(db_cols) == set(model_cols), f"{table.name}: columns differ"
        for name, col in model_cols.items():
            if not col.primary_key:
                assert db_cols[name]["nullable"] == col.nullable, f"{table.name}.{name}: NULL/NOT NULL differs"


def test_enum_values_match(mysql):
    insp = inspect(mysql)
    for table in Base.metadata.sorted_tables:
        db_cols = {c["name"]: c for c in insp.get_columns(table.name)}
        for col in table.columns:
            if hasattr(col.type, "enums"):
                assert list(db_cols[col.name]["type"].enums) == list(col.type.enums), f"{table.name}.{col.name}"


def test_check_schema_catches_missing_column(mysql):
    check_schema(mysql)  # up to date: no error
    with mysql.begin() as conn:
        conn.execute(text("ALTER TABLE gates DROP COLUMN under_maintenance"))
    try:
        with pytest.raises(RuntimeError, match=r"gates is missing columns \['under_maintenance'\]"):
            check_schema(mysql)
    finally:
        with mysql.begin() as conn:
            conn.execute(text("ALTER TABLE gates ADD COLUMN under_maintenance BOOLEAN NOT NULL DEFAULT FALSE AFTER broken"))
