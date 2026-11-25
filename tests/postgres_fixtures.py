"""Ephemeral PostgreSQL for tests marked ``postgres`` (ADR-0001 §9).

Resolution order for the server:

1. ``VI_TEST_DATABASE_URL`` (CI's service container, or a developer's own
   throwaway server). Only the process environment is read, never ``.env``.
2. A ``postgres:18`` container started with testcontainers when Docker is
   reachable.
3. Otherwise every test that needs Postgres is skipped with the reason.

Each test gets its own database, created on that server and dropped after
the test, so nothing is shared with other tests or with the operator's data.
``migrated_postgres_url`` clones a template database that was migrated to
head once per session (``CREATE DATABASE ... TEMPLATE``).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from venture_intelligence.storage.pg.engine import dispose_engines, normalize_url

TEST_DATABASE_URL_ENV = "VI_TEST_DATABASE_URL"
POSTGRES_IMAGE = "postgres:18"
_TEMPLATE_DATABASE = f"vi_test_template_{uuid.uuid4().hex[:8]}"


def _docker_unreachable_reason() -> str | None:
    try:
        import docker  # installed with testcontainers
    except ImportError:
        return "testcontainers is not installed (uv sync --all-groups)"
    try:
        client = docker.from_env()
        try:
            client.ping()
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001 - any failure means "no Docker here"
        return f"Docker is not reachable ({type(exc).__name__})"
    return None


@pytest.fixture(scope="session")
def postgres_server_url() -> Iterator[URL]:
    """URL of a Postgres server whose maintenance database we may connect to."""
    configured = os.environ.get(TEST_DATABASE_URL_ENV, "").strip()
    if configured:
        yield normalize_url(configured)
        return
    reason = _docker_unreachable_reason()
    if reason is not None:
        pytest.skip(f"postgres tests need {TEST_DATABASE_URL_ENV} or Docker: {reason}")
    from testcontainers.community.postgres import PostgresContainer

    container = PostgresContainer(POSTGRES_IMAGE, driver="psycopg")
    container.start()
    try:
        yield normalize_url(container.get_connection_url())
    finally:
        container.stop()


def _admin_execute(server_url: URL, *statements: str) -> None:
    engine = create_engine(server_url, isolation_level="AUTOCOMMIT", poolclass=None)
    try:
        with engine.connect() as connection:
            for statement in statements:
                connection.execute(text(statement))
    finally:
        engine.dispose()


def _create_database(server_url: URL, template: str | None = None) -> URL:
    name = f"vi_test_{uuid.uuid4().hex[:12]}"
    clause = f' TEMPLATE "{template}"' if template else ""
    _admin_execute(server_url, f'CREATE DATABASE "{name}"{clause}')
    return server_url.set(database=name)


def _drop_database(server_url: URL, database_url: URL) -> None:
    dispose_engines()
    _admin_execute(server_url, f'DROP DATABASE IF EXISTS "{database_url.database}" WITH (FORCE)')


@pytest.fixture(scope="session")
def _migrated_template(postgres_server_url: URL) -> Iterator[str]:
    from venture_intelligence.storage.pg import migrate

    _admin_execute(postgres_server_url, f'CREATE DATABASE "{_TEMPLATE_DATABASE}"')
    try:
        migrate.upgrade(postgres_server_url.set(database=_TEMPLATE_DATABASE))
        yield _TEMPLATE_DATABASE
    finally:
        _admin_execute(
            postgres_server_url, f'DROP DATABASE IF EXISTS "{_TEMPLATE_DATABASE}" WITH (FORCE)'
        )


@pytest.fixture
def postgres_url(postgres_server_url: URL) -> Iterator[URL]:
    """A fresh, empty database (no migrations applied)."""
    url = _create_database(postgres_server_url)
    try:
        yield url
    finally:
        _drop_database(postgres_server_url, url)


@pytest.fixture
def migrated_postgres_url(postgres_server_url: URL, _migrated_template: str) -> Iterator[URL]:
    """A fresh database already at Alembic head."""
    url = _create_database(postgres_server_url, template=_migrated_template)
    try:
        yield url
    finally:
        _drop_database(postgres_server_url, url)
