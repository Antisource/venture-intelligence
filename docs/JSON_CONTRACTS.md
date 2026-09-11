# JSON Contracts

Pydantic models in `backend/venture_intelligence/shared/models.py` are the canonical
cross-component contracts. API routes use request models and JSON response
models; the EventBus serializes `CanonicalEvent` into SQLite; the frontend
generates TypeScript declarations from FastAPI OpenAPI and consumes the JSON
through one typed boundary client.

Important contracts include:

- `CanonicalEvent`: event type, entity type/ID, source URL, timestamps,
  confidence, raw payload, and metadata.
- `ResearchRun`: target, bounded queries, source statuses, evidence, entities,
  hypotheses, confidence labels, next actions, budget, stop reason, and event
  count.
- `ResearchEvidence`: raw and canonical URL, title/abstract, domain, retrieval
  and publication time, discovery method, query, confidence, target
  relationship, direct/search status, temporal status, provenance, depth, and
  metadata. arXiv API records additionally retain authors, categories, DOI,
  journal, and PDF metadata when supplied by the official package.
- `AnalystResult`: answer, provider/model name, evidence sufficiency, and
  caveats.
- `HistoricalWebObservation`: original/archive URL, snapshot and retrieval
  timestamps, temporal precision/status, conservative website-lifecycle
  signals, and provenance.
- `ReplayRequest`, `ReplayResult`, and `ReplaySnapshot`: cutoff/mode, bounded
  immutable inputs, semantic projection, input/projection digests, counts,
  structured errors, schema/engine metadata, isolation, and suppressed side
  effects. `ReplayContractResponse` is the read-only OpenAPI/browser contract;
  execution remains a local administrative CLI boundary.
- `GroundTruthImportRequest`, `GroundTruthDataset`, `EvaluationRequest`, and
  `EvaluationRun`: typed target level, outcome/cutoff timestamps, exact canonical
  identities, replay snapshot, explicit metric numerators/denominators, missed
  and false-positive diagnostics, source ablations, provenance, digests, and
  version metadata. API execution remains disabled; read-only OpenAPI contracts
  generate the `/evaluate` TypeScript types.
- `BatchDiscoveryObservation`, `CandidateHypothesis`, and `BatchDiscoveryRun`:
  immutable pre-canonical observations, conservative identity clustering,
  explainable batch assessment, source independence, typed drop reasons,
  discovery funnel, coverage, profile enrichment, and replay eligibility. A
  hypothesis is not a canonical person or company.
- CRM, recommendation, and alert lifecycle records: append-only human decisions
  with actor, before/after state, timestamps, provenance, input digest, and exact
  feature/ranking lineage.

## Research enums

`DIRECT_PUBLIC` means a public page was read directly; `SEARCH_ENGINE` is the
DuckDuckGo/optional Scholar discovery path; `RSS` is public feed discovery;
`PUBLIC_API` is the official arXiv API path. `AUTH_REQUIRED`, `BLOCKED`,
`RATE_LIMITED`, `TEMPORARILY_UNAVAILABLE`, and `UNAVAILABLE` are source
outcomes, not evidence claims.

Use `model_dump(mode="json")` or `model_dump_json()` at boundaries. Do not pass
database rows, HTTP clients, model SDK objects, or arbitrary Python objects
between components.

Knowledge evidence additionally carries one of seven canonical categories,
deterministic confidence percentage and basis, canonical URL, temporal status,
and provenance. Knowledge revisions are append-only JSON snapshots delivered by
SSE hints or read through the polling endpoint; generated Markdown is never
application state.

The authoritative browser schema is `frontend/src/api/schema.d.ts`. Regenerate
it and the checked-in OpenAPI document with `pnpm api:types`; do not hand-edit
either generated file.
