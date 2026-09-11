# Architecture

Venture Intelligence is a local-first Python backend with a logically separate
React/TypeScript frontend. FastAPI serves the Vite production bundle at `/`, but the frontend
imports no Python code and communicates only through JSON HTTP/WebSocket
contracts.

```text
frontend/ (native React shell, feature workspaces, generated API types)
        │ same-origin JSON HTTP/WebSocket
        ▼
FastAPI API + static asset serving
        │
connectors/research → evidence candidate → identity gate
                                           │ accepted only
                           CanonicalEvent → SQLite EventBus
                                            │
                                validator → resolver → graph/evidence
                                                         │
                                   versioned features → signals → ranking
                                             ▲                      │
                          typed CRM actions ─┘        recommendations/alerts
        │
research/ (bounded public discovery)   agents/analyst (optional Qwen)
```

The frontend product boundary is fully native React. Production uses hash routes
such as `/#/research` so unprefixed compatibility API paths cannot collide with
page navigation. `AppShell` owns global
navigation, program scope, theme, health, and command search. Feature-oriented
routes own Inbox, Discover, Portfolio, and a contextual Research workspace.
Research embeds canonical intelligence/lineage, categorized knowledge, temporal
relationships, and decision controls; hypotheses retain their separate candidate
IDs and cannot enter canonical graph/decision controls before promotion. System
contains sources, runs, settings, replay, and evaluation. Old product URLs redirect
to these workspaces rather than composing duplicate pages. No frontend component
generates evidence or inferred facts. The standalone ES-module application and
its former compatibility endpoint are retired. APIs use `/api` and `/api/v1`; old client
bookmarks redirect to native workspaces without maintaining duplicate renderers.

The existing knowledge-page endpoint now declares its authoritative KnowledgePage
response model in OpenAPI. React consumes the generated contract. SSE revision
hints invalidate server-state queries; bounded polling remains available after
stream failure. No knowledge persistence or historical semantics were changed.

Historical replay is a separate, side-effect-free branch:

```text
live immutable events + CRM actions (read only)
        │ observed_at cutoff by default
        ▼
ReplayContext → disposable SQLite store
        │
validation → resolution → evidence/graph → versioned features
        → signals → rankings → recommendations
        │
        └── canonical snapshot + audit digests; store disposed
```

No live database handle is given to replay projection workers. Connectors,
alerts, email/Slack, CRM mutation, and broadcasts are not part of the replay
execution graph. See [`REPLAY.md`](REPLAY.md).

Historical evaluation is downstream of that isolated branch:

```text
validated ground truth + ReplayRequest → disposable historical projection
        → exact typed identity comparison → metrics/diagnostics/ablation
        → versioned JSON report + provenance digest
```

Evaluation never reads current rankings, alerts, or CRM state. Only ground-truth
datasets and completed evaluation reports are persisted in live SQLite; these
audit tables do not feed the operational pipeline. See [`EVALUATION.md`](EVALUATION.md).

The authoritative backend source is `backend/venture_intelligence/`, installed
under the stable import name `venture_intelligence`. There is one backend copy;
`storage/db.py` owns SQLite schema and
serialization. `shared/models.py` owns typed event, evidence, research, and
analyst contracts. `connectors/` owns public-source collection, while
`pipeline/` owns deterministic derived state.

M4 makes `pipeline/feature_worker.py` the operational evidence-to-feature
boundary. Signals no longer aggregate graph-global repository data or parse raw
search terms. Feature, signal, hypothesis, and ranking rows record deterministic
versions/digests and exact upstream IDs. The read-only intelligence service
assembles only the rows referenced by a selected ranking. See
[`FEATURE_LINEAGE.md`](FEATURE_LINEAGE.md).

M5 makes `crm/workflow.py` the only partner-action write boundary and
`decisions/` the typed read/write API projection. CRM actions are append-only
temporal facts with provenance and stable input digests. The feature worker
turns historically eligible human judgments and realized outcomes into bounded,
versioned inputs while retaining exact action IDs. Human judgment remains
distinct from public evidence and ground truth. Recommendation/alert lifecycle
rows are separate append-only histories. Replay copies eligible actions into
its disposable store; it never mutates the live CRM materialized view.

M6 makes `ProgramRegistry` authoritative across resolution, evidence, features,
signals, rankings, graph program nodes, evaluation and React filters. Current
signals use `program_affinity`; `yc_likelihood`, `yc_website`, `yc_url`, and
`yc_batch_added` remain narrow persisted-data compatibility aliases. They do not
drive new accelerator-specific logic. Legacy polluted identities are handled by
an explicit dry-run-first reconciliation command using the centralized identity
gate. Raw events, observations and evidence remain immutable; invalid canonical,
graph, ranking, CRM and other derived projections are quarantined or removed.

M7 places a typed bounded projection boundary in `graph/`. Current snapshots
read the integrity-gated materialized graph; historical requests delegate to
the isolated replay service and retain the M2 `observed_at <= cutoff` rule,
explicit validity and provenance. The React `/graph` feature consumes that
contract directly. Its lazy React Three Fiber renderer is separated from the
accessible relationship-table view, so the application remains inspectable
without WebGL and other routes do not load Three.js.

M8 adds a pre-canonical `batch_discovery/` boundary beside target-based
`research/`. It begins from program-derived public/indexed queries rather than
a supplied identity or official-directory seed. Immutable observations are
clustered conservatively into versioned hypotheses; supporting, contradictory,
other-batch, ambiguous, stale and derivative evidence all participate in a
deterministic assessment. Only a hypothesis passing the identity/batch gate can
emit canonical events. Technical/publication connectors operate after discovery
and cannot manufacture membership. Historical reconstruction reads observations
using `observed_at <= cutoff` and never writes live state. See
[`LATEST_BATCH_DISCOVERY.md`](LATEST_BATCH_DISCOVERY.md).

## Runtime lifecycle

`python -m venture_intelligence.monitor` initializes the database, EventBus,
registry, connectors, workers, API server, pipeline loop, and investigation loop.
Each connector runs in an isolated task and closes its crawler on cancellation.
The API-only Uvicorn command is available when background collection is not
desired. Tests use temporary SQLite paths and fake crawlers/providers.

## Contract and failure boundaries

Canonical events are Pydantic JSON-native records. Before EventBus persistence,
the identity gate rejects platform/source names, raw search terms, invalid
labels, and candidates without appropriate public identity support. A rejected
candidate is stored as structured audit evidence and never receives a worker
queue row. The validator then checks URL, time, confidence, identifier, and
event/entity compatibility. Resolver completion is required before graph,
evidence, signals, ranking, CRM, alerts, and APIs can use a person/company.
Source failures remain structured data and one connector failure does not
cancel peers.

The Research Agent enters this same EventBus rather than writing rankings or
entities directly. The analyst model receives structured context and returns a
typed response; it cannot add source facts to the database.
