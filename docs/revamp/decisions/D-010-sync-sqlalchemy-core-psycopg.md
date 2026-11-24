# D-010: Synchronous SQLAlchemy Core on psycopg 3, not an async rewrite

- **Date:** 2026-11-23
- **Area:** Datastore access
- **Differs from owner instruction:** No (adds the driver and concurrency model)

1. **Chosen:** storage uses SQLAlchemy 2.0 Core (MIT) on psycopg 3 (LGPL-3.0-only),
   synchronously, with one connection pool per process. Routes that touch storage,
   SMTP or blocking connectors become plain `def`, so they run in FastAPI's
   threadpool. Streaming routes reach storage through `anyio.to_thread`. No ORM.
   See ADR-0001.
2. **Owner instruction:** PostgreSQL as the primary datastore, with production-grade
   components where they help. The instruction names no driver and no concurrency
   model. The old `storage/db/__init__.py` docstring anticipated "PostgreSQL + asyncpg".
3. **Why:** an async rewrite would change 97 storage methods and every caller in the
   same step as the dialect switch, which doubles parity risk. Core shares table
   metadata with Alembic, so drift is caught by `alembic check`. Parity is proven by one
   storage contract suite that runs every public `Database` method against the frozen
   legacy `sqlite3` implementation and the new Postgres one, so it is argued per method,
   not query by query, and the new queries may use Postgres-only constructs. The
   threadpool removes blocking I/O from the event loop, the actual defect in FINDINGS
   §5 cause 9 and §7.
   **Trade-off:** concurrency is bounded by the threadpool and pool size (AnyIO's
   default is 40 tokens), and peak throughput is lower than asyncpg's. Revisit only if
   event-loop or threadpool saturation is measured.
4. **Parity:** `Database` keeps its public method signatures. A storage contract suite
   runs every method on both the frozen legacy `sqlite3` implementation and the Postgres
   implementation before each domain switches. `openapi.json` stays
   byte-identical.
