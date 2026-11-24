"""Copy a legacy SQLite database into an upgraded PostgreSQL database.

This is the S0 skeleton of ADR-0002's ``vi db import-sqlite``:

1. The source file is opened read-only and copied into memory with SQLite's
   backup API (which includes committed WAL content). The original file is
   never written.
2. The in-memory copy is brought to the final SQLite shape with the frozen
   legacy DDL and additive migrations, so older files import too. The legacy
   data repairs (candidate quarantine) are not run: the copy stays faithful.
3. The target must be at Alembic head and, unless ``force`` is given, empty.
   With ``force`` rows whose primary key already exists are skipped
   (``ON CONFLICT DO NOTHING``), which makes reruns idempotent and never
   updates append-only rows.
4. Every table is copied row for row inside one transaction, and identity
   sequences are moved past the imported ids.

Later links add ``timestamptz``/``jsonb`` conversion, quarantine of rows that
violate the ADR-0002 constraints, the JSON state files and verification
digests.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, Table, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from venture_intelligence.storage.pg.migrate import current_revision, head_revision
from venture_intelligence.storage.pg.tables import metadata


class SqliteImportError(RuntimeError):
    """Base class for import refusals that map to a usage exit code."""


class TargetNotReadyError(SqliteImportError):
    pass


class TargetNotEmptyError(SqliteImportError):
    pass


class SourceSchemaError(SqliteImportError):
    pass


@dataclass
class TableReport:
    source: int = 0
    imported: int = 0
    skipped: int = 0
    target: int = 0

    @property
    def reconciled(self) -> bool:
        return self.imported + self.skipped == self.source and self.target >= self.source


@dataclass
class ImportReport:
    tables: dict[str, TableReport] = field(default_factory=dict)
    forced: bool = False

    @property
    def ok(self) -> bool:
        return all(report.reconciled for report in self.tables.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "forced": self.forced,
            "tables": {
                name: {
                    "source": report.source,
                    "imported": report.imported,
                    "skipped": report.skipped,
                    "target": report.target,
                }
                for name, report in sorted(self.tables.items())
            },
        }


def _open_source_copy(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise SourceSchemaError(f"SQLite file not found: {path}")
    source = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        copy = sqlite3.connect(":memory:")
        source.backup(copy)
    finally:
        source.close()
    copy.row_factory = sqlite3.Row
    _bring_to_final_shape(copy)
    return copy


def _bring_to_final_shape(conn: sqlite3.Connection) -> None:
    """Apply the frozen legacy DDL and additive migrations to the copy."""
    from venture_intelligence.storage.db.schema import _SCHEMA, SchemaMixin

    class _LegacyMigrator(SchemaMixin):
        pass

    conn.executescript(_SCHEMA)
    _LegacyMigrator()._apply_schema_migrations(conn)
    conn.commit()


def _source_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def _source_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]


def _batches(
    conn: sqlite3.Connection, table: str, columns: list[str], size: int
) -> Iterator[list[dict[str, Any]]]:
    quoted = ", ".join(f'"{column}"' for column in columns)
    cursor = conn.execute(f'SELECT {quoted} FROM "{table}" ORDER BY rowid')
    while True:
        rows = cursor.fetchmany(size)
        if not rows:
            return
        yield [dict(zip(columns, tuple(row), strict=True)) for row in rows]


def _count(connection: Connection, table: Table) -> int:
    return int(connection.execute(select(func.count()).select_from(table)).scalar_one())


def target_row_counts(connection: Connection) -> dict[str, int]:
    return {table.name: _count(connection, table) for table in metadata.sorted_tables}


def _reset_identity_sequences(connection: Connection) -> None:
    for table in metadata.sorted_tables:
        for column in table.columns:
            if column.identity is None:
                continue
            connection.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence(:table, :column), "
                    f'COALESCE(MAX("{column.name}"), 1), MAX("{column.name}") IS NOT NULL) '
                    f'FROM "{table.name}"'
                ),
                {"table": table.name, "column": column.name},
            )


def import_sqlite(
    sqlite_path: str | Path,
    engine: Engine,
    *,
    force: bool = False,
    batch_size: int = 1000,
) -> ImportReport:
    """Copy every table of ``sqlite_path`` into ``engine``'s database."""
    source = _open_source_copy(Path(sqlite_path))
    report = ImportReport(forced=force)
    try:
        present = _source_tables(source)
        unknown = sorted(present - set(metadata.tables) - {"features_m4_migration"})
        if unknown:
            raise SourceSchemaError(f"SQLite tables with no Postgres mapping: {unknown}")
        with engine.begin() as connection:
            head = head_revision()
            current = current_revision(connection)
            if current != head:
                raise TargetNotReadyError(
                    f"target schema is at {current or 'no revision'}, head is {head}; "
                    "run `python -m venture_intelligence.storage.pg upgrade` first"
                )
            before = target_row_counts(connection)
            if not force and any(before.values()):
                non_empty = sorted(name for name, count in before.items() if count)
                raise TargetNotEmptyError(
                    f"target database is not empty ({', '.join(non_empty)}); "
                    "pass --force to skip rows that already exist"
                )
            for table in metadata.sorted_tables:
                table_report = TableReport()
                report.tables[table.name] = table_report
                if table.name in present:
                    columns = _source_columns(source, table.name)
                    extra = sorted(set(columns) - set(table.columns.keys()))
                    if extra:
                        raise SourceSchemaError(
                            f"{table.name}: SQLite columns with no Postgres mapping: {extra}"
                        )
                    statement = pg_insert(table).on_conflict_do_nothing()
                    for batch in _batches(source, table.name, columns, batch_size):
                        table_report.source += len(batch)
                        connection.execute(statement, batch)
                after = _count(connection, table)
                table_report.target = after
                table_report.imported = after - before[table.name]
                table_report.skipped = table_report.source - table_report.imported
            _reset_identity_sequences(connection)
    finally:
        source.close()
    return report
