"""Programmatic Alembic entry points (``vi db upgrade`` in ADR-0002 §1).

The application never runs DDL on its own; only these functions (through
``python -m venture_intelligence.storage.pg`` or the compose ``migrate``
service) change the schema.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine
from sqlalchemy.engine import URL

from venture_intelligence.storage.pg.engine import normalize_url

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def alembic_config(url: str | URL | None = None, connection: Connection | None = None) -> Config:
    """Build an Alembic config that does not depend on ``alembic.ini``."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    if url is not None:
        rendered = normalize_url(url).render_as_string(hide_password=False)
        # ConfigParser interpolation treats % specially (percent-encoded passwords).
        config.set_main_option("sqlalchemy.url", rendered.replace("%", "%%"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def head_revision() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if head is None:  # pragma: no cover - the baseline always exists
        raise RuntimeError("no Alembic revisions found")
    return head


def current_revision(bind: Connection | Engine) -> str | None:
    if isinstance(bind, Engine):
        with bind.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    return MigrationContext.configure(bind).get_current_revision()


def is_at_head(bind: Connection | Engine) -> bool:
    return current_revision(bind) == head_revision()


def upgrade(url: str | URL | None = None, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str | URL | None = None, revision: str = "-1") -> None:
    command.downgrade(alembic_config(url), revision)
