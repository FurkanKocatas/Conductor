"""Alembic environment — sync psycopg engine, URL from DATABASE_URL.

The application talks to Postgres over asyncpg; migrations run separately (in CI /
deploy, or the compose `migrate` one-shot) over a plain sync psycopg connection,
so they never contend with the running app. `MIGRATE_DATABASE_URL` overrides
`DATABASE_URL` to let the multi-DB runner point this at each tenant database.
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _url() -> str:
    raw = os.environ.get("MIGRATE_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
    if not raw:
        sys.exit("DATABASE_URL (or MIGRATE_DATABASE_URL) is required to migrate")
    # App uses asyncpg via `postgresql://`; SQLAlchemy needs the psycopg (v3) driver.
    if raw.startswith("postgresql://"):
        raw = "postgresql+psycopg://" + raw[len("postgresql://"):]
    elif raw.startswith("postgres://"):
        raw = "postgresql+psycopg://" + raw[len("postgres://"):]
    return raw


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    engine = engine_from_config(section, prefix="sqlalchemy.",
                                poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
