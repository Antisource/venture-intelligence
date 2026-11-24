# API contract

FastAPI publishes OpenAPI at `/docs` and `/openapi.json`.

The M1 foundation endpoints are:

- `GET /api/v1/search`: typed command/program/validated-entity search.
- `GET /api/v1/sources`: provider and connector capabilities, acquisition and
  access modes, discovery/verification roles, restrictions, and defaults.
- `GET /api/v1/system/temporal-contract`: timestamp and validity semantics;
  reports the isolated M2 replay foundation as available.
- `GET /api/v1/replay/contract`: typed read-only replay modes, isolation,
  event bound, local CLI command, and suppressed side effects. Remote replay
  execution is not exposed.
- `GET /api/v1/evaluations/contract`: typed metric/target/replay semantics and
  local-only commands. `GET /api/v1/evaluations` and
  `GET /api/v1/evaluations/{evaluation_run_id}` expose persisted reports.
  Import and execution are deliberately not remote API operations.
- `GET /api/v1/intelligence/rankings`: integrity-gated, optionally as-of
  rankings with movement and top referenced feature/signal.
- `GET /api/v1/intelligence/entities/{entity_id}`: complete typed
  evidence-to-ranking lineage and history for a validated canonical identity.
- `GET /api/v1/decisions` and `GET /api/v1/decisions/entities/{entity_id}`:
  integrity-gated CRM board and full feedback/recommendation/alert lineage.
- `POST /api/v1/decisions/entities/{entity_id}/actions`: append a typed,
  provenance-bearing partner action. Recommendation and alert lifecycle routes
  append human review states without mutating prior events.
- `GET /api/v1/batch-discovery/contract`: M8 stages, source taxonomy/access
  matrix, promotion rules and deterministic offline tool benchmark.
- `GET /api/v1/batch-discovery` and `GET /api/v1/batch-discovery/{id}`:
  latest candidate hypotheses and their immutable evidence.
- `POST /api/v1/batch-discovery/runs`: unknown-candidate discovery without a
  supplied founder/company identity. `POST /manual-observations` imports typed
  public evidence but cannot bypass promotion gates.

The React client is regenerated with `pnpm api:types`, which exports the live
FastAPI schema to `frontend/src/api/openapi.json` and then generates
`frontend/src/api/schema.d.ts`. Generated files are reproducible and should not
be hand-edited.

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/health` | process/database health |
| GET | `/api/stats` | event/entity counters |
| GET | `/api/v1/programs` | active YAML registry |
| GET | `/api/v1/capabilities?lens=L1..L4` | capability registry view for a lens (default L1) plus teasers for higher lenses; L1 never lists people-level data |
| GET | `/api/v1/replay/contract` | read-only local replay contract; no execution |
| GET | `/api/v1/intelligence/rankings` | versioned ranking intelligence and movement |
| GET | `/api/v1/intelligence/entities/{entity_id}` | founder/company feature and evidence lineage |
| GET | `/api/v1/decisions` | typed, historically bounded CRM decision board |
| GET | `/api/v1/decisions/entities/{entity_id}` | action, feedback, recommendation, and alert timelines |
| POST | `/api/v1/decisions/entities/{entity_id}/actions` | append an idempotent typed partner action |
| POST | `/api/v1/decisions/recommendations/{id}/lifecycle` | append accepted/dismissed/completed review state |
| POST | `/api/v1/decisions/alerts/{id}/lifecycle` | append acknowledged/dismissed/resolved state |
| POST | `/api/v1/research` | bounded public research run |
| GET | `/api/v1/batch-discovery/contract` | discovery/verification/enrichment and source policy contract |
| GET | `/api/v1/system/readiness` | credential-safe product readiness and recovery actions |
| GET | `/api/v1/cycles` | persisted scheduler state and intelligence-cycle history |
| GET | `/api/v1/cycles/{cycle_id}` | one complete cycle diagnostic record |
| POST | `/api/v1/cycles/run` | shared bounded manual intelligence cycle |
| PUT | `/api/v1/scheduler` | persist explicit scheduler enable/disable state |
| GET | `/api/v1/batch-discovery?program_id=...` | latest candidate hypothesis revisions |
| GET | `/api/v1/batch-discovery/{hypothesis_id}` | hypothesis plus immutable observations |
| POST | `/api/v1/batch-discovery/runs` | bounded unknown latest-batch discovery |
| POST | `/api/v1/batch-discovery/manual-observations` | typed public candidate evidence import |
| POST | `/api/v1/intel/gather` | bounded, non-persisting connector preview with per-source attempted/status/error and candidate-validation outcomes |
| GET | `/api/founders` | ranked entities |
| GET | `/api/alerts` | evidence-backed alerts |
| GET | `/api/knowledge/{entity_type}/{entity_id}` | canonical knowledge page |
| GET | `/api/v1/knowledge/{entity_id}` | polling-friendly JSON snapshot with revision metadata |
| GET | `/api/knowledge/stream/{entity_id}` | bounded SSE stream of JSON wiki revisions |
| GET | `/api/knowledge/validate` | knowledge reference/claim validation |
| GET/PUT | `/api/settings/email` | optional email destination and preferences |
| POST | `/api/settings/email/test` | bounded email test delivery |
| POST | `/api/settings/email/digest` | local preview or explicitly configured partner-digest delivery |
| GET/POST | `/api/crm...` | CRM board and mutations |
| GET | `/api/graph...` | graph snapshot/neighborhood |
| GET | `/api/investigations...` | investigation records |
| POST | `/api/analyst/chat` | JSON-grounded analyst response |
| WebSocket | `/ws` and `/api/ws` | connected/ping/stats channel |

Legacy aliases without `/api` remain where they do not conflict with React
routes. The unprefixed `GET /crm` is reserved for the React workspace;
machine clients use `GET /api/crm`. Error
responses use FastAPI's JSON `detail` field; successful research responses are
typed `ResearchRun` JSON and include structured source statuses and stop reason.
Gather responses are typed `GatherResponse` JSON with
`mode="preview_corroboration"`, `persisted_events=0`, considered versus actually
attempted sources, and found/accepted/rejected aggregates. Per-source `status`
uses the closed `GatherSourceStatus` OpenAPI enum, including normalized
auth-required, blocked, rate-limited, unavailable, and budget-exhausted states.
