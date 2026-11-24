# D-011: SQLite is retired as a runtime and test backend; replay uses disposable Postgres schemas

- **Date:** 2026-11-23
- **Area:** Datastore
- **Differs from owner instruction:** Partly (Postgres becomes the only runtime store, not just the primary one)

1. **Chosen:** PostgreSQL is the only runtime datastore, locally and in hosted mode.
   SQLite survives only as the read-only source for `vi db import-sqlite`, and as frozen
   legacy code that the importer and the dual-backend contract suite use until the last
   domain is ported; then that code is deleted. Replay's disposable SQLite file becomes a
   per-run Postgres schema (`replay_<run_id>`). A role with no privileges on live schemas
   accesses it, and the schema is dropped after the run. Tests use an ephemeral Postgres:
   a CI service container, or testcontainers locally. See ADR-0001.
2. **Owner instruction:** PostgreSQL as the *primary* datastore; existing SQLite data
   migrates through a tested path; the offline suite may use an ephemeral local Postgres.
3. **Why:** a second runtime backend would block row-level security, the Postgres-native
   queue (ADR-0003) and privilege-based append-only enforcement (ADR-0002). It would also
   double the test matrix permanently. Replay in a separate Postgres schema, accessed by a
   role without live privileges, isolates more strongly than a separate file opened by the
   same code.
   **Trade-off:** even a solo user now needs Postgres. The one-command compose stack
   (ADR-0005) mitigates this; whether a no-Docker install is required is an open owner
   question.
4. **Parity:** `--database` (P-072, P-106–P-108) accepts a database URL. Given a SQLite
   path, it exits 2 with a structured error that names `vi db import-sqlite`. `db fresh`
   (P-102) drops and recreates the schema behind the same `--confirm` gates.
   `data/intelligence.db` (P-271) and `DB_PATH` (P-279) are import-only. Replay and
   evaluation digests (P-261–P-264) must match between the SQLite build and the Postgres
   build on the demo and historical fixtures.
