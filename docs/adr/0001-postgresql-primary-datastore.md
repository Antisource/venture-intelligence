# ADR-0001: PostgreSQL as the primary datastore

- **Status:** Proposed
- **Date:** 2026-11-23
- **Related:** ADR-0002, ADR-0003, ADR-0006, D-010, D-011

## Context

- `storage/db/` composes `Database` from 11 domain mixins over `DatabaseBase`
  (`storage/db/_base.py`). Each call opens a `sqlite3` connection with WAL. There are 30
  tables plus 2 created lazily (FINDINGS §7).
- Foreign keys are "enforced by Python, not SQLite constraints"
  (`storage/db/__init__.py`). Timestamps are ISO-8601 text and JSON is stored as text.
- `Database(settings.db_path)` is built on every v1 request (16 call sites). Every
  construction re-runs DDL, migrations and the legacy quarantine (FINDINGS §7, P-242).
- 28 raw `._connect()` calls outside `storage/` bypass the interface (FINDINGS §7).
- Every route is `async def`, yet routes run synchronous SQLite and SMTP calls on the event
  loop that the monitor also uses (FINDINGS §5 cause 9, §7).
- The owner requires: PostgreSQL as the primary store, a tested SQLite migration, an
  offline suite (an ephemeral local Postgres is allowed), RLS in hosted mode (ADR-0006),
  and a real job queue (ADR-0003).
- Affected parity rows: P-271 (`data/intelligence.db`), P-279 `DB_PATH`, P-072
  `--database`, P-101 `db status`, P-102 `db fresh`, P-106–P-108 module CLIs, P-242
  migrations, P-261 replay's disposable store.

## Decision

1. **PostgreSQL 18** (PostgreSQL License) is the only runtime datastore, locally and in
   hosted mode. CI also tests the newest GA major (19, if released by now; verify). No
   extension is required, so managed Postgres services also work.
2. **SQLAlchemy 2.0 Core** (MIT; use 2.1 once stable, verify) on **psycopg 3** (3.2+,
   LGPL-3.0-only, imported unmodified) and `psycopg_pool`. No ORM: the Pydantic models in
   `shared/models/` remain the domain model. Tables are declared once in a `MetaData`
   module, which Alembic reads (ADR-0002).
3. **Synchronous I/O, off the event loop.** Storage and services stay synchronous. Routes
   that touch storage, SMTP or blocking connectors become plain `def`, so FastAPI runs
   them in its threadpool. Streaming endpoints (SSE, WebSocket) stay `async` and reach
   storage through `anyio.to_thread.run_sync`. The threadpool limit is set to match the
   connection pool (D-010).
4. **One engine per process.** The API lifespan, the worker and the CLI bootstrap each
   create one engine (`QueuePool`, `pool_pre_ping`; API `pool_size=5, max_overflow=5`,
   worker `concurrency + 2`; tune under load). Routes get it through a FastAPI dependency.
   Per-transaction settings use `SET LOCAL` (ADR-0006), so a transaction-mode PgBouncer
   (ISC) can be added later in hosted mode, but only after measurement shows it is needed.
5. **`Database` keeps its public method signatures.** It is constructed from a URL or an
   engine and never runs DDL. A new `DATABASE_URL` setting is the source of truth;
   `DB_PATH` is accepted only by `vi db import-sqlite`.
6. **Port domain by domain behind that interface.** Before any port, the 28 raw
   `._connect()` callers move behind named methods. Then, in order: table metadata →
   events and `event_queue` → entities → observations and evidence → features, signals
   and hypotheses → rankings → recommendations, alerts and lifecycles → CRM → graph →
   investigations → evaluation and batch discovery → replay. During the port, a storage
   contract suite runs every public `Database` method against both the frozen SQLite
   implementation and the Postgres one. A domain switches only when both give identical
   results.
7. **Dialect differences are handled explicitly, each with a contract test:**
   - **NULL ordering:** ascending sorts put NULLs first in SQLite and last in Postgres.
     Every `ORDER BY` states `NULLS FIRST|LAST` and adds a unique tiebreaker.
   - **`LIKE`:** case-insensitive in SQLite, case-sensitive in Postgres. Use `ILIKE` or
     `lower()` wherever current behaviour is case-insensitive.
   - **Timestamps:** text becomes `timestamptz`; the API keeps the `+00:00` format.
   - **JSON:** text becomes `jsonb`, which reorders keys. Digests are therefore computed
     from canonical Pydantic JSON, never from JSON read back from the database.
   - **Other SQLite-only behaviour:** `INSERT OR IGNORE/REPLACE` becomes `ON CONFLICT`,
     `AUTOINCREMENT` becomes an identity column, 0/1 becomes `boolean`, and SQLite's
     type affinity becomes strict typing.
8. **SQLite is retired (D-011).** It survives only as the read-only source for
   `vi db import-sqlite` (ADR-0002). The frozen legacy module is used by the importer and
   the contract suite, then deleted after the last domain ports. Replay's disposable
   SQLite file becomes a disposable Postgres schema (`replay_<run_id>`), built by the same
   migrations, accessed by a role with no privilege on live schemas, and dropped after the
   run.
9. **Tests use an ephemeral Postgres.** CI uses a GitHub Actions `services: postgres`
   container. Locally, a session fixture uses `VI_TEST_DATABASE_URL` if set; otherwise it
   starts a container with testcontainers-python (Apache-2.0) on Docker Desktop or Podman.
   It migrates a template database once, and each test gets a `CREATE DATABASE ...
   TEMPLATE` clone. If the suite takes more than twice today's runtime (verify), it
   switches to per-test `TRUNCATE`.

## Alternatives considered

- **Raw psycopg 3 with hand-written SQL:** the smallest and fastest option, but it gives
  no shared metadata for Alembic drift checks and no way to run the same query code on
  SQLite during the port, so parity would be argued query by query. Rejected for the
  port; Core's `text()` still allows raw SQL on hot paths.
- **asyncpg (Apache-2.0) with a full async rewrite:** the highest throughput, and it
  matches the V2 note in `storage/db/__init__.py`. But it rewrites 97 methods and every
  caller in the same change as the dialect switch, which doubles parity risk. asyncpg has
  no sync API, and the local install holds about 7k rankings, a load that does not need
  async. Revisit only if event-loop saturation is measured.
- **SQLAlchemy ORM or SQLModel (MIT):** less boilerplate for CRUD, but the identity map
  and unit of work fight append-only tables, and SQLModel would bind 61 model classes to
  tables. Rejected.
- **SQLite kept as a "lite" backend:** zero operations for solo users, but it blocks RLS,
  the Postgres-native queue (ADR-0003) and privilege-based append-only rules (ADR-0002),
  and it doubles the test matrix permanently. Rejected (D-011).

## Consequences

- **Gains:** real constraints and RLS; no more per-request DDL; blocking I/O leaves the
  event loop.
- **Costs:** Postgres is required even for one user (ADR-0005 makes this one command).
  Threadpool concurrency is bounded (AnyIO's default is 40 tokens).
- **Migration steps:** (1) move the raw `._connect()` callers behind named methods;
  (2) create the engine once and inject it; (3) convert storage-touching routes to `def`;
  (4) add the metadata and the baseline migration; (5) port each domain under the
  contract suite; (6) build the importer; (7) move replay to schemas; (8) delete the
  legacy SQLite code.
- **Parity risks:**
  - `--database` (P-072, P-106–P-108) accepts a URL. Given a `.db` path, it exits 2 with
    a structured error that names `vi db import-sqlite`.
  - `db fresh` (P-102) drops and recreates the schema, behind the same `--confirm` gates.
  - Result ordering and digests could change; the contract suite guards them.

## How parity is verified

- The storage contract suite passes on both backends for every domain before it switches.
- The full pytest suite (375 at the S0 tip) runs on Postgres in CI. Tests behind P-242,
  P-271 and P-279 are rewritten with the same expectations.
- Replay and evaluation digests (P-261–P-264) match between the SQLite build and the
  Postgres build, for the demo dataset and for historical fixtures.
- `frontend/src/api/openapi.json` stays byte-identical, and the route-table test from
  D-007 still passes.
- An AST test fails if an `async def` route calls `Database` directly, or if `Database(`
  is constructed outside the bootstrap modules.
