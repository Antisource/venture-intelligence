"""Alembic environment for the PostgreSQL datastore (ADR-0002 §1).

The database URL is resolved in this order:

1. a SQLAlchemy ``Connection`` passed in ``config.attributes["connection"]``
   (programmatic callers and tests);
2. ``sqlalchemy.url`` from the Alembic config (``alembic -x`` users or
   :func:`venture_intelligence.storage.pg.migrate.alembic_config`);
3. the ``DATABASE_URL`` setting.

Offline (``--sql``) mode is supported for reviewing the generated DDL.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection
from venture_intelligence.storage.pg.engine import create_pg_engine, normalize_url, require_url
from venture_intelligence.storage.pg.tables import metadata

config = context.config
target_metadata = metadata


def _url():
    configured = config.get_main_option("sqlalchemy.url")
    return normalize_url(configured) if configured else require_url()


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=_url().render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = create_pg_engine(_url(), pool_size=1, max_overflow=0)
    try:
        with engine.connect() as connection:
            _run(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
