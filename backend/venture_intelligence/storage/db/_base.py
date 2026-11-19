"""Connection management and shared helpers for the SQLite database layer."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# ── Connection management ──────────────────────────────────────────────────────


class DatabaseBase:
    def __init__(self, db_path: str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection with row_factory set; commit on clean exit."""
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")  # concurrent reads during writes
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _ts(dt: datetime | None) -> str | None:
        """Convert datetime → ISO-8601 UTC string, or None."""
        return dt.isoformat() if dt else None

    @staticmethod
    def _from_ts(s: str | None) -> datetime | None:
        """Parse ISO-8601 string → UTC datetime, or None."""
        if not s:
            return None
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt


def _research_published_at(provenance: list[dict[str, Any]]) -> str | None:
    if not provenance:
        return None
    raw_payload = provenance[0].get("raw_payload")
    if not isinstance(raw_payload, dict):
        return None
    research_evidence = raw_payload.get("research_evidence")
    if not isinstance(research_evidence, dict):
        return None
    value = research_evidence.get("published_at")
    return str(value) if value else None


def _json_object(value: Any) -> dict[str, Any]:
    parsed = _json_value(value)
    return parsed if isinstance(parsed, dict) else {}


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value
