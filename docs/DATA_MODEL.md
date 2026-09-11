# Data model

The backend uses Pydantic models at service boundaries and SQLite for durable
state. `backend/venture_intelligence/shared/models.py` is the canonical schema
module; rows are serialized as JSON-compatible dictionaries before crossing the
API or EventBus boundary.

Core records are `Person`, `Company`, `Program`, `CanonicalEvent`, `Evidence`,
`Feature`, `Signal`, `FounderRanking`, `GraphNode`, `GraphEdge`, `Investigation`,
`CRMRecord`, and `ResearchRun`.
Events retain source, source URL, timestamps, payload, provenance metadata, and
confidence. Evidence is never promoted to a verified claim merely because a
search engine returned a snippet.

Research evidence also records discovery method, source status, query, depth,
canonical URL, and whether the observation is current or historical. All
models support deterministic `model_dump(mode="json")` serialization.

Historical archive evidence uses `HistoricalWebObservation`. Replay uses
`ReplayRequest` and an auditable `ReplayResult` containing a semantic
`ReplaySnapshot`, projection counts/errors, digests, and version metadata. The
replay database is disposable derived state, not a second source of truth and
not a migration of the live schema.

Historical evaluation uses `GroundTruthDataset`, `EvaluationRequest`, and
`EvaluationRun`. Ground truth and reports are JSON-canonical audit records in
`ground_truth_datasets` and `evaluation_runs_v2`; they do not feed live ranking,
CRM, alerts, or collection. Each metric preserves its numerator, denominator,
sample size, unit, and definition.

M4 `Feature` rows are append-only versioned measurements. They retain formula,
parameters, semantic input digest, evidence/observation IDs, program/batch
metadata, and `as_of`/computation time. Signals retain feature/evidence IDs;
ranking dimensions and rankings retain signal/feature IDs. The live API can
therefore reproduce an explicit evidence → feature → signal → ranking chain
without treating Markdown or UI state as canonical data.

Legacy polluted projections can be audited with the canonical
`venture-intelligence reconcile` command, backed by
`venture_intelligence.storage.cleanup.reconcile_polluted_entities`. It is a dry
run by default. Applied reconciliation preserves immutable evidence and events,
records quarantine/audit state, and removes only invalid derived projections.
