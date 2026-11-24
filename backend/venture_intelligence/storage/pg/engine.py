"""Engine and transaction factory for the PostgreSQL datastore (ADR-0001 §2-§4).

Synchronous SQLAlchemy 2 Core on psycopg 3.  One engine per process: the API
lifespan, the worker and the CLI bootstrap each call :func:`get_engine` once
and pass the engine down.  No ORM sessions; callers open a transaction with
:func:`begin` (or ``engine.begin()``) and run Core statements on it.

The URL comes from the ``DATABASE_URL`` setting.  ``postgres://`` and
``postgresql://`` URLs are accepted and routed to the psycopg 3 driver.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock
from typing import Any

from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.engine import URL, make_url

_DRIVER = "postgresql+psycopg"
_ENGINES: dict[str, Engine] = {}
_ENGINES_LOCK = Lock()


class DatabaseNotConfiguredError(RuntimeError):
    """Raised when Postgres is needed but ``DATABASE_URL`` is unset."""


def normalize_url(url: str | URL) -> URL:
    """Return ``url`` with the psycopg 3 driver selected.

    ``postgres://`` (Heroku/compose style) and bare ``postgresql://`` URLs are
    rewritten to ``postgresql+psycopg://``; any other driver is rejected so a
    misconfigured URL fails loudly instead of loading an unexpected DBAPI.
    """
    parsed = make_url(url) if isinstance(url, str) else url
    backend, _, driver = parsed.drivername.partition("+")
    if backend not in {"postgres", "postgresql"}:
        raise ValueError(f"DATABASE_URL must be a PostgreSQL URL, got {backend!r}")
    if driver and driver != "psycopg":
        raise ValueError(f"DATABASE_URL driver must be psycopg (psycopg 3), got {driver!r}")
    return parsed.set(drivername=_DRIVER)


def configured_url(settings: Any | None = None) -> URL | None:
    """Return the normalized ``DATABASE_URL`` from settings, or ``None``."""
    if settings is None:
        from venture_intelligence.config import settings as runtime_settings

        settings = runtime_settings
    raw = getattr(settings, "database_url", None)
    if raw is None:
        return None
    value = raw.get_secret_value() if hasattr(raw, "get_secret_value") else str(raw)
    value = value.strip()
    return normalize_url(value) if value else None


def require_url(settings: Any | None = None) -> URL:
    url = configured_url(settings)
    if url is None:
        raise DatabaseNotConfiguredError(
            "DATABASE_URL is not set; point it at a PostgreSQL database "
            "(see compose.yaml or .env.example)"
        )
    return url


def create_pg_engine(
    url: str | URL,
    *,
    pool_size: int = 5,
    max_overflow: int = 5,
    **kwargs: Any,
) -> Engine:
    """Create a new engine (``QueuePool`` with ``pool_pre_ping``).

    Prefer :func:`get_engine`, which keeps one engine per URL per process.
    Tests and one-shot tools may create and dispose their own engines.
    """
    return create_engine(
        normalize_url(url),
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        **kwargs,
    )


def get_engine(url: str | URL | None = None, **kwargs: Any) -> Engine:
    """Return the process-wide engine for ``url`` (default: ``DATABASE_URL``)."""
    resolved = normalize_url(url) if url is not None else require_url()
    key = resolved.render_as_string(hide_password=False)
    with _ENGINES_LOCK:
        engine = _ENGINES.get(key)
        if engine is None:
            engine = create_pg_engine(resolved, **kwargs)
            _ENGINES[key] = engine
        return engine


def dispose_engines() -> None:
    """Dispose every cached engine (process shutdown and tests)."""
    with _ENGINES_LOCK:
        engines = list(_ENGINES.values())
        _ENGINES.clear()
    for engine in engines:
        engine.dispose()


@contextmanager
def begin(engine: Engine | None = None) -> Iterator[Connection]:
    """Yield a connection inside one transaction; commit on clean exit."""
    with (engine or get_engine()).begin() as connection:
        yield connection
