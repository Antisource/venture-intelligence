"""``python -m venture_intelligence.storage.pg`` (``vi db`` until the CLI lands).

Commands:

* ``upgrade [--revision REV]``   run Alembic migrations (default ``head``);
* ``downgrade --revision REV``   revert to ``REV`` (``base`` drops everything);
* ``current``                    print the current and head revisions;
* ``import-sqlite --sqlite PATH [--force]``  copy a legacy SQLite database.

The database URL comes from ``--database-url`` or ``DATABASE_URL``. Exit codes:
0 success, 1 failure, 2 refused (configuration, target state or usage).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from sqlalchemy.engine import URL

from venture_intelligence.storage.pg.engine import (
    DatabaseNotConfiguredError,
    create_pg_engine,
    normalize_url,
    require_url,
)


def _url(args: argparse.Namespace) -> URL:
    return normalize_url(args.database_url) if args.database_url else require_url()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m venture_intelligence.storage.pg")
    parser.add_argument("--database-url", help="PostgreSQL URL (default: DATABASE_URL)")
    commands = parser.add_subparsers(dest="command", required=True)
    upgrade = commands.add_parser("upgrade", help="migrate the schema forward")
    upgrade.add_argument("--revision", default="head")
    downgrade = commands.add_parser("downgrade", help="migrate the schema back")
    downgrade.add_argument("--revision", required=True)
    commands.add_parser("current", help="print the current and head revisions")
    importer = commands.add_parser("import-sqlite", help="copy a SQLite database into Postgres")
    importer.add_argument("--sqlite", required=True, help="path to the legacy SQLite file")
    importer.add_argument(
        "--force",
        action="store_true",
        help="import into a non-empty database, skipping rows whose key already exists",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        url = _url(args)
    except (DatabaseNotConfiguredError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    from venture_intelligence.storage.pg import migrate

    if args.command == "upgrade":
        migrate.upgrade(url, args.revision)
        return 0
    if args.command == "downgrade":
        migrate.downgrade(url, args.revision)
        return 0

    engine = create_pg_engine(url, pool_size=1, max_overflow=0)
    try:
        if args.command == "current":
            current = migrate.current_revision(engine)
            head = migrate.head_revision()
            print(json.dumps({"current": current, "head": head, "at_head": current == head}))
            return 0
        from venture_intelligence.storage.pg.importer import SqliteImportError, import_sqlite

        try:
            report = import_sqlite(args.sqlite, engine, force=args.force)
        except SqliteImportError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(report.as_dict(), indent=2))
        return 0 if report.ok else 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
