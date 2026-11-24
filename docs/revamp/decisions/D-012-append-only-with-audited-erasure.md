# D-012: Append-only tables allow exactly two audited maintenance paths: erasure and reconcile

- **Date:** 2026-11-23
- **Area:** Evidence invariants
- **Differs from owner instruction:** Partly (adds erasure and reconcile exceptions to immutable records)

1. **Chosen:** privileges and triggers that fire for every role protect two tiers of
   tables:
   - **Tier 1, the evidence record:** events, observations, bindings, rejections,
     `model_runs` and the audit logs;
   - **Tier 2, append-only histories:** CRM events, recommendation and alert lifecycles,
     and features.

   Only two `SECURITY DEFINER` functions may change these rows:
   - **`vi_erase_subject`** tombstones personal fields, deletes derived rows, writes
     `erasure_log` and adds the subject to `subject_suppressions`. The identity gate
     checks suppressions, which implements opt-out, and backups replay `erasure_log` on
     restore.
   - **Reconcile** (P-103, P-243) is kept, not retired. It may delete tier-2 rows of
     polluted entities, after archiving each one to `reconcile_archive`. It never touches
     tier 1: its in-place re-pointing of observations becomes appended bindings, and
     derived rows are rebuilt.

   See ADR-0002 and ADR-0006.
2. **Owner instruction:** observations are immutable; people data supports erasure and
   opt-out under GDPR and India's DPDP Act; every existing capability keeps working.
3. **Why:** erasure cannot work if no row may ever change. Reconcile today rewrites
   `observations.entity_id` and destroys CRM and lifecycle history for polluted entities
   (`storage/cleanup.py:124-175, 436-462`). That contradicts the "observations immutable"
   rule already in the architecture docs. Two narrow, audited, database-enforced paths
   keep immutability true for every other writer, including bugs and ad-hoc SQL. They also
   make erasure provable and reconcile recoverable.
   **Trade-off:** "immutable" now means "immutable except through these two logged
   functions". Row ids, the source host and timestamps survive erasure so that audit
   counts stay coherent; whether that residue is acceptable is a legal call for the owner.
4. **Parity:** the reconcile command keeps its dry-run default and its `derived_rows`
   report, and its tests (P-103, P-243) keep their assertions. The append-only lifecycle
   tests behind P-237, P-245 and P-248 pass on Postgres. New tests assert that an update or
   delete fails for every role outside the two functions.
