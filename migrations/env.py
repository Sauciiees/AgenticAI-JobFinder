from logging.config import fileConfig

from alembic import context

import models  # noqa: F401  (registers the tables on Base.metadata)
from database import Base, engine

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_online() -> None:
    with engine.connect() as connection:
        if connection.dialect.name == "sqlite":
            # Batch mode rebuilds tables (copy, drop, rename). With foreign keys on, dropping the old
            # users table would cascade-delete every application and chat. Turn them off meanwhile.
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite can't ALTER most things in place; batch mode copies the table instead.
            render_as_batch=connection.dialect.name == "sqlite",
            compare_type=True,
        )
        try:
            with context.begin_transaction():
                context.run_migrations()
        finally:
            if connection.dialect.name == "sqlite":
                # This connection goes back to the pool; don't leave foreign keys off for the app.
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.commit()


if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported; run against a database.")
run_migrations_online()
