# CRM workflow

CRM records persist in SQLite and use explicit stages:
`discovery`, `qualification`, `partner_review`, `investigation`,
`relationship`, `investment`, `portfolio`, and `passed`.

Milestone 5 makes every partner action an append-only, typed `CRMEvent` with an
action version, actor, before/after stage, occurrence and observation times,
provenance, input digest, and the ranking snapshot visible when the action was
recorded. Supported actions are `meeting_scheduled`, `passed`, `interested`,
`introduced`, `invested`, `watchlisted`, `follow_up_requested`,
`relationship_started`, `stage_changed`, and `note_added`. Stable action IDs
make client retries idempotent; old events are never rewritten.

Feedback is explicitly classified as workflow state, human judgment, or a
realized external outcome. It is not ground truth. The feature worker computes
bounded, versioned positive/negative/outcome counts only for historically
eligible actions and preserves exact action IDs through feature → signal →
ranking → recommendation lineage. Public identity evidence is still mandatory:
CRM feedback alone cannot create or promote an entity.

The authoritative typed routes are:

- `GET /api/v1/decisions` — integrity-gated board;
- `GET /api/v1/decisions/entities/{entity_id}` — actions, feedback lineage,
  recommendation timeline, and alert timeline;
- `POST /api/v1/decisions/entities/{entity_id}/actions` — append an action;
- `POST /api/v1/decisions/recommendations/{id}/lifecycle`;
- `POST /api/v1/decisions/alerts/{id}/lifecycle`.

React `/crm` and `/crm/{entity_id}` consume these generated OpenAPI types. The
older `/api/crm`, advance, and note routes remain compatible adapters and use
the same workflow boundary.
