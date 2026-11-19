"""Private helpers shared by the domain model modules."""

from datetime import datetime, timezone
from uuid import uuid4

# Classes and helpers defined here keep the historical public module path.
# OpenAPI component names (e.g. venture_intelligence__shared__models__SourceAccessMode
# on a name collision), pydantic core-schema refs and pickled qualified paths all
# derive from ``__module__``, which Python takes from ``__name__`` at definition time.
__name__ = "venture_intelligence.shared.models"

# ── Helpers ────────────────────────────────────────────────────────────────────


def _now() -> datetime:
    """Always UTC — never naive datetimes in this codebase."""
    return datetime.now(timezone.utc)


def _uid() -> str:
    return str(uuid4())


def _utc(value: datetime) -> datetime:
    """Normalize an aware timestamp to UTC without inventing a timezone."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include an explicit timezone")
    return value.astimezone(timezone.utc)
