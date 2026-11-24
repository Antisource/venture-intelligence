# D-012: Append-only tables allow exactly three audited maintenance paths: erasure, reconcile and retention purge

- **Date:** 2026-11-23
- **Area:** Evidence invariants
- **Differs from owner instruction:** Partly (adds erasure, reconcile and retention-purge exceptions to immutable records)

1. **Chosen:** privileges and triggers that fire for every role protect two tiers of
   tables:
   - **Tier 1, the evidence record:** events, observations, bindings, rejections,
     `model_runs`, the audit logs and `maintenance_log`;
   - **Tier 2, append-only histories:** CRM events, recommendation and alert lifecycles,
     and features.

   Only three `SECURITY DEFINER` functions may change these rows:
   - **`vi_erase_subject`** tombstones personal fields, deletes derived rows, writes
     `erasure_log` and adds the subject to `subject_suppressions`. The identity gate
     checks suppressions, which implements opt-out. Because `erasure_log` is inside the
     dump, every erasure also appends a content-free record (subject key hash, erased
     row ids, timestamp, actor) to an append-only JSONL ledger on a separate volume. The
     ledger is excluded from the dump and backed up alongside it. Restore replays it after
     `pg_restore` and refuses to finish if replay fails, so erasures made after a backup
     survive a restore.
   - **Reconcile** (P-103, P-243) is kept, not retired. It may delete tier-2 rows of
     polluted entities, after archiving each one to `reconcile_archive`. It never touches
     tier 1: its in-place re-pointing of observations becomes appended bindings, and
     derived rows are rebuilt.
   - **`vi_purge_people_audit`**, the retention purge, deletes `people_query_audit` rows
     older than the retention period (12 months by default, configurable) and appends a
     `maintenance_log` row for each run. It touches no other table.

   See ADR-0002, ADR-0005, ADR-0006 and D-021.
2. **Owner instruction:** observations are immutable; people data supports erasure and
   opt-out under GDPR and India's DPDP Act; every existing capability keeps working.
3. **Why:** erasure cannot work if no row may ever change. Reconcile today rewrites
   `observations.entity_id` and destroys CRM and lifecycle history for polluted entities
   (`storage/cleanup.py:124-175, 436-462`). That contradicts the "observations immutable"
   rule already in the architecture docs. The owner also set a 12-month retention for the
   people-query audit log (D-021), which an immutable table cannot honour. Three narrow,
   audited, database-enforced paths keep immutability true for every other writer,
   including bugs and ad-hoc SQL. They also make erasure provable, reconcile recoverable
   and retention enforceable.
   **Trade-off:** "immutable" now means "immutable except through these three logged
   functions". Row ids, the source host and timestamps survive erasure as content-free
   tombstones so that audit counts stay coherent; the owner accepted that residue
   (D-021). The ledger is one more volume to back up.
4. **Parity:** the reconcile command keeps its dry-run default and its `derived_rows`
   report, and its tests (P-103, P-243) keep their assertions. The append-only lifecycle
   tests behind P-237, P-245 and P-248 pass on Postgres. New tests assert that an update or
   delete fails for every role outside the three functions, and that a subject erased
   after a backup stays erased after restoring it.
