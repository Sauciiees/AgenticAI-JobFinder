"""Brings the database schema up to date. Run before starting the server:

    python db_migrate.py

Databases created before migrations existed (by the old create_all) already have the baseline
tables but no alembic_version table; they are stamped at the baseline first, then upgraded.
A local SQLite database is copied to <name>.backup-<timestamp> before any change is made.
"""

import shutil
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from database import engine

ALEMBIC_INI = Path(__file__).resolve().parent / "alembic.ini"


def _backup_sqlite_if_needed(cfg: Config) -> None:
    if engine.dialect.name != "sqlite" or not engine.url.database:
        return
    db_file = Path(engine.url.database)
    if not db_file.is_file() or db_file.stat().st_size == 0:
        return
    head = ScriptDirectory.from_config(cfg).get_current_head()
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    if current != head:
        backup = db_file.with_name(f"{db_file.name}.backup-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(db_file, backup)
        print(f"Backed up {db_file} to {backup} before migrating.")


def migrate() -> None:
    cfg = Config(str(ALEMBIC_INI))
    _backup_sqlite_if_needed(cfg)
    tables = set(inspect(engine).get_table_names())
    if "users" in tables and "alembic_version" not in tables:
        print("Existing database without migration history: marking it as the baseline.")
        command.stamp(cfg, "0001_baseline")
    command.upgrade(cfg, "head")


if __name__ == "__main__":
    migrate()
    print("Database schema is up to date.")
