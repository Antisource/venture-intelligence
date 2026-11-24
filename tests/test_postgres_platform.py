"""S0 PostgreSQL platform: migrations, append-only triggers, SQLite import, probes."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from alembic import command
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError
from venture_intelligence.api.app import app
from venture_intelligence.config import settings
from venture_intelligence.shared.models import (
    CanonicalEvent,
    Company,
    EntityType,
    EventType,
    Observation,
    Person,
    Source,
)
from venture_intelligence.storage.db import Database
from venture_intelligence.storage.pg import migrate
from venture_intelligence.storage.pg.__main__ import main as pg_main
from venture_intelligence.storage.pg.importer import (
    TargetNotEmptyError,
    TargetNotReadyError,
    import_sqlite,
)
from venture_intelligence.storage.pg.tables import APPEND_ONLY_TABLES, metadata


def _event(entity_id: str = "yc__acme") -> CanonicalEvent:
    return CanonicalEvent(
        event_type=EventType.COMPANY_LISTED,
        entity_type=EntityType.COMPANY,
        entity_id=entity_id,
        source=Source.YC_WEBSITE,
        source_url=f"https://ycombinator.com/companies/{entity_id}",
        occurred_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        confidence=0.97,
        raw_payload={"name": "Acme", "slug": "acme"},
    )


def _seeded_sqlite(path) -> tuple[Database, CanonicalEvent]:
    db = Database(str(path))
    event = _event()
    db.write_event(event)
    db.write_event(_event("yc__globex"))
    db.write_company(Company(canonical_name="Acme Corp", slug="acme"))
    db.write_person(Person(canonical_name="Ada Lovelace", github_username="ada"))
    db.write_observation(
        Observation(
            event_id=event.event_id,
            entity_id=event.entity_id,
            source=Source.YC_WEBSITE,
            fact="Listed on the YC directory",
            value={"batch": "F26"},
        )
    )
    return db, event


def _sqlite_counts(path) -> dict[str, int]:
    connection = sqlite3.connect(path)
    try:
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {
            name: connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            for name in names
        }
    finally:
        connection.close()


# ── Migrations ────────────────────────────────────────────────────────────────


@pytest.mark.postgres
def test_migrations_upgrade_from_empty_without_drift_and_downgrade_cleanly(postgres_url) -> None:
    migrate.upgrade(postgres_url)
    engine = create_engine(postgres_url)
    try:
        assert set(inspect(engine).get_table_names()) == set(metadata.tables) | {"alembic_version"}
        assert migrate.is_at_head(engine)
        # The baseline matches the declared metadata (ADR-0002 drift check).
        with engine.connect() as connection:
            command.check(migrate.alembic_config(connection=connection))

        migrate.downgrade(postgres_url, "base")
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == {"alembic_version"}
        with engine.connect() as connection:
            functions = connection.execute(
                text("SELECT count(*) FROM pg_proc WHERE proname = 'vi_reject_append_only_change'")
            ).scalar_one()
        assert functions == 0
        assert migrate.current_revision(engine) is None

        migrate.upgrade(postgres_url)  # the cycle is repeatable
        assert migrate.is_at_head(engine)
    finally:
        engine.dispose()


# ── Append-only evidence record ───────────────────────────────────────────────


_INSERTS = {
    "events": (
        "INSERT INTO events (event_id, event_type, entity_type, entity_id, source, source_url, "
        "occurred_at, observed_at, ingested_at, confidence) VALUES ('e1', 'company_listed', "
        "'company', 'yc__acme', 'yc_website', 'https://example.org', '2026-07-01T00:00:00+00:00', "
        "'2026-07-01T00:00:00+00:00', '2026-07-01T00:00:00+00:00', 0.9)"
    ),
    "observations": (
        "INSERT INTO observations (observation_id, event_id, entity_id, source, fact, "
        "observed_at, confidence) VALUES ('o1', 'e1', 'yc__acme', 'yc_website', 'listed', "
        "'2026-07-01T00:00:00+00:00', 0.9)"
    ),
}


@pytest.mark.postgres
@pytest.mark.parametrize("table", sorted(_INSERTS))
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE {table} SET confidence = 0.1",
        "DELETE FROM {table}",
        "TRUNCATE {table}",
    ],
    ids=["update", "delete", "truncate"],
)
def test_append_only_trigger_rejects_changes(migrated_postgres_url, table, statement) -> None:
    assert table in APPEND_ONLY_TABLES
    engine = create_engine(migrated_postgres_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(_INSERTS[table]))
        with pytest.raises(DBAPIError, match="vi_append_only"):
            with engine.begin() as connection:
                connection.execute(text(statement.format(table=table)))
        with engine.connect() as connection:
            row = connection.execute(text(f"SELECT confidence FROM {table}")).one()
        assert row.confidence == 0.9
    finally:
        engine.dispose()


# ── SQLite import ─────────────────────────────────────────────────────────────


@pytest.mark.postgres
def test_import_sqlite_round_trips_a_database_built_by_the_current_class(
    tmp_path, migrated_postgres_url
) -> None:
    sqlite_path = tmp_path / "legacy.db"
    db, event = _seeded_sqlite(sqlite_path)
    source_counts = _sqlite_counts(sqlite_path)
    assert source_counts["events"] == 2 and source_counts["event_queue"] > 2

    engine = create_engine(migrated_postgres_url)
    try:
        report = import_sqlite(sqlite_path, engine)
        assert report.ok
        for name, count in source_counts.items():
            assert report.tables[name].source == count
            assert report.tables[name].imported == count
        with engine.connect() as connection:
            for name, count in source_counts.items():
                assert connection.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar() == count
            stored = connection.execute(
                text("SELECT source_url, confidence, raw_payload FROM events WHERE event_id=:id"),
                {"id": event.event_id},
            ).one()
            company = connection.execute(text("SELECT canonical_name, slug FROM companies")).one()
            fact = connection.execute(text("SELECT fact, value FROM observations")).one()
        assert stored.source_url == event.source_url
        assert stored.confidence == pytest.approx(0.97)
        assert '"slug": "acme"' in stored.raw_payload
        assert (company.canonical_name, company.slug) == ("Acme Corp", "acme")
        assert fact.fact == "Listed on the YC directory" and '"F26"' in fact.value

        # Identity sequences continue after the imported ids.
        with engine.begin() as connection:
            next_id = connection.execute(
                text(
                    "INSERT INTO event_queue (event_id, worker, created_at) "
                    "VALUES ('new', 'validator', 'now') RETURNING queue_id"
                )
            ).scalar_one()
            highest = connection.execute(
                text("SELECT max(queue_id) FROM event_queue WHERE event_id <> 'new'")
            ).scalar_one()
        assert next_id == highest + 1

        # A non-empty target is refused unless forced; forcing is idempotent.
        with pytest.raises(TargetNotEmptyError):
            import_sqlite(sqlite_path, engine)
        forced = import_sqlite(sqlite_path, engine, force=True)
        assert forced.ok
        assert all(table.imported == 0 for table in forced.tables.values())
        assert forced.tables["events"].skipped == 2
    finally:
        engine.dispose()

    # The source file is read only: nothing was written to it.
    assert _sqlite_counts(sqlite_path) == source_counts
    assert db.get_company  # the source Database stays usable


@pytest.mark.postgres
def test_import_sqlite_refuses_a_target_that_is_not_at_head(tmp_path, postgres_url) -> None:
    sqlite_path = tmp_path / "legacy.db"
    _seeded_sqlite(sqlite_path)
    engine = create_engine(postgres_url)
    try:
        with pytest.raises(TargetNotReadyError):
            import_sqlite(sqlite_path, engine)
    finally:
        engine.dispose()
    rendered = postgres_url.render_as_string(hide_password=False)
    assert pg_main(["--database-url", rendered, "import-sqlite", "--sqlite", str(sqlite_path)]) == 2


@pytest.mark.postgres
def test_cli_upgrade_then_import_reports_counts(tmp_path, postgres_url, capsys) -> None:
    sqlite_path = tmp_path / "legacy.db"
    _seeded_sqlite(sqlite_path)
    rendered = postgres_url.render_as_string(hide_password=False)
    assert pg_main(["--database-url", rendered, "upgrade"]) == 0
    capsys.readouterr()
    assert pg_main(["--database-url", rendered, "import-sqlite", "--sqlite", str(sqlite_path)]) == 0
    output = capsys.readouterr().out
    assert '"ok": true' in output and '"events"' in output


# ── Probes ────────────────────────────────────────────────────────────────────


def test_livez_answers_without_touching_a_datastore(tmp_path, monkeypatch) -> None:
    missing = tmp_path / "never-created.db"
    monkeypatch.setattr(settings, "db_path", str(missing))
    response = TestClient(app).get("/livez")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}
    assert not missing.exists()


def test_readyz_is_503_until_sqlite_exists_and_never_creates_it(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "ready.db"
    monkeypatch.setattr(settings, "db_path", str(db_path))
    monkeypatch.setattr(settings, "database_url", None)
    client = TestClient(app)

    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["sqlite"]["ok"] is False
    assert not db_path.exists()
    assert str(tmp_path) not in response.text

    Database(str(db_path))
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"sqlite": {"ok": True}}}


@pytest.mark.postgres
def test_readyz_checks_postgres_is_at_head(tmp_path, monkeypatch, postgres_url) -> None:
    db_path = tmp_path / "ready.db"
    Database(str(db_path))
    monkeypatch.setattr(settings, "db_path", str(db_path))
    rendered = postgres_url.render_as_string(hide_password=False)
    monkeypatch.setattr(settings, "database_url", SecretStr(rendered))
    client = TestClient(app)

    response = client.get("/readyz")
    assert response.status_code == 503
    postgres = response.json()["checks"]["postgres"]
    assert postgres["ok"] is False and "upgrade" in postgres["reason"]
    assert postgres_url.password is None or postgres_url.password not in response.text

    migrate.upgrade(postgres_url)
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["checks"]["postgres"]["ok"] is True


def test_health_response_is_unchanged() -> None:
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert set(response.json()) == {"status", "project", "db"}
