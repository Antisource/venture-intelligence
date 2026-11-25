"""Read-only readiness checks for ``GET /readyz`` (ADR-0004 §4).

Readiness never writes or migrates: SQLite is opened read-only and is not
created when missing, and Postgres is only queried. Reasons never include a
filesystem path, a URL or a driver error message, so the endpoint does not
leak deployment details.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text

UPGRADE_HINT = "run `python -m venture_intelligence.storage.pg upgrade`"


@dataclass
class CheckResult:
    ok: bool
    reason: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": self.ok, **self.detail}
        if self.reason:
            payload["reason"] = self.reason
        return payload


def check_sqlite(db_path: str) -> CheckResult:
    path = Path(db_path)
    if not path.is_file():
        return CheckResult(False, "sqlite database has not been initialised")
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=2)
        try:
            connection.execute("SELECT 1 FROM events LIMIT 1").fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return CheckResult(False, f"sqlite database is not readable ({type(exc).__name__})")
    return CheckResult(True)


def check_postgres(settings: Any) -> CheckResult | None:
    """Return ``None`` when ``DATABASE_URL`` is unset (Postgres not in use)."""
    from venture_intelligence.storage.pg.engine import configured_url, get_engine
    from venture_intelligence.storage.pg.migrate import current_revision, head_revision

    try:
        url = configured_url(settings)
    except ValueError:
        return CheckResult(False, "DATABASE_URL is not a PostgreSQL psycopg URL")
    if url is None:
        return None
    try:
        engine = get_engine(url, connect_args={"connect_timeout": 3})
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            revision = current_revision(connection)
    except Exception as exc:  # noqa: BLE001 - any driver failure means "not ready"
        return CheckResult(False, f"postgres is unreachable ({type(exc).__name__})")
    head = head_revision()
    detail = {"revision": revision, "head": head}
    if revision != head:
        return CheckResult(
            False,
            f"postgres schema is at {revision or 'no revision'}, head is {head}; {UPGRADE_HINT}",
            detail,
        )
    return CheckResult(True, detail=detail)


def readiness(settings: Any) -> tuple[bool, dict[str, Any]]:
    checks: dict[str, CheckResult] = {"sqlite": check_sqlite(settings.db_path)}
    postgres = check_postgres(settings)
    if postgres is not None:
        checks["postgres"] = postgres
    ready = all(check.ok for check in checks.values())
    return ready, {
        "status": "ready" if ready else "not_ready",
        "checks": {name: check.as_dict() for name, check in checks.items()},
    }
