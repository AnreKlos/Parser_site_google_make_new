"""Alembic environment configuration — async SQLite via aiosqlite."""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

# --- Alembic Config -----------------------------------------------------------
config = context.config

# Set up Python loggers from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# --- Project imports ----------------------------------------------------------
from config import settings
from db.models import Base

target_metadata = Base.metadata

# Override the INI-file URL with the one from project settings
config.set_main_option("sqlalchemy.url", settings.database_url)


def run_migrations_offline() -> None:
    """Offline mode — generate SQL script without connecting to DB."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    """Configure Alembic context with a live connection and run."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Online mode — connect to the async DB and run migrations."""
    connectable = create_async_engine(
        settings.database_url,
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online mode — wraps async runner."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
