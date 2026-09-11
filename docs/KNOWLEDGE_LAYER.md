# Knowledge layer

Canonical knowledge lives in JSON files under `KNOWLEDGE_DIR` (default
`knowledge/`). The compiler incrementally merges evidence, claims, provenance,
claim status, revisions, timestamps, and confidence. Raw source evidence is
retained; Markdown is a deterministic generated view only.

Statuses are `confirmed`, `supported`, `inferred`, `historical`, `stale`,
`contradicted`, and `unknown`. Historical archive observations preserve both
the original and archive URL and are never presented as current facts. The
compiler detects duplicate evidence, unsupported claims, stale/contradicted
claims, broken evidence references, and orphaned pages through
`GET /api/knowledge/validate`.

Research compiles knowledge after evidence collection. Attached evidence may
update a promoted canonical page. Unbound or contradictory evidence is retained
on a separate candidate investigation page with `unknown`/`contradicted` claim
status, so a same-name result cannot silently strengthen the canonical wiki.

## Categorised evidence and live revisions

Every evidence item is normalized into one of the canonical categories:

`first_party_identity`, `accelerator_program`, `founder_background`,
`company_activity`, `technical_publication`, `social_announcement`, or
`historical_archived`.

Evidence keeps its immutable source fields and adds an explainable confidence
percentage, label, and basis (source tier, identity match, corroboration,
recency, and contradictions). Category and overall confidence are calculated
deterministically; historical evidence is never silently treated as current.

Each incremental compile appends a JSON revision containing changed categories,
confidence deltas, provenance, and a content snapshot. The API exposes a
bounded SSE stream at `/api/knowledge/stream/{entity_id}` (and the versioned
alias) plus the polling metadata embedded in knowledge and evidence responses.
The browser subscribes to revisions and falls back to bounded JSON polling.
Markdown remains a deterministic view and is never used as application state.

Each page retains stable evidence IDs, canonical URLs, retrieval timestamps,
claim status, contradictions, and revision history. Repeated compilation merges
by stable identity without removing prior evidence. `GET
/api/knowledge/validate` reports duplicate evidence, unsupported or stale
claims, broken references, and orphaned relationships as structured JSON.

Email is a separate notification sink, never part of canonical knowledge.
Configuration, acknowledgement semantics, retries, and suppression are covered
in [`NOTIFICATIONS.md`](NOTIFICATIONS.md).
