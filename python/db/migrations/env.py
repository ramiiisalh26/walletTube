import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from db.models import Base  # noqa: F401 — ensures all models are registered

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Replace asyncpg URL prefix with psycopg2 for Alembic's sync autogenerate.
_fallback_url = "postgresql+asyncpg://ytsearch:ytsearch_dev@localhost:5432/ytsearch"
db_url = os.environ.get("DATABASE_URL") or _fallback_url
sync_url = db_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
config.set_main_option("sqlalchemy.url", sync_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    async_url = db_url if db_url.startswith("postgresql+asyncpg") else db_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
    connectable = async_engine_from_config(
        {"sqlalchemy.url": async_url},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()