# Historical replay

Milestone 2 introduces a local, bounded replay foundation. A replay reads the
immutable live event store but rebuilds all derived projections in a disposable
SQLite database owned by an explicit `ReplayContext`. The source database is
never passed to a projection worker.

## Time semantics

The default knowledge boundary is `observed_at <= cutoff`: the replay includes
only evidence the system had actually observed by that instant. `ingested_at`
is persistence time and is never the default historical boundary. An explicit
`occurred_at` mode is available for event-time reconstruction; it is deliberately
separate because it can include evidence observed later about an earlier event.
Explicit `TemporalValidity` bounds and graph provenance are preserved. Observation
time is not promoted into world-time validity.

## Deterministic projections

Eligible events are ordered by the selected time axis, `observed_at`, and stable
event ID. The isolated pipeline revalidates candidates, resolves canonical
identities, rebuilds evidence, graph relationships, versioned features, signals,
hypotheses, rankings, and recommendations, and produces a canonical semantic snapshot plus
input/projection SHA-256 digests. Volatile internal UUIDs and database update
timestamps are excluded from the semantic digest. Identical inputs and request
parameters therefore produce the same replay ID and relevant projection digest.

Invalid candidates remain structured replay rejections and cannot enter graph,
signals, rankings, recommendations, CRM, or alerts. A bounded run that rejects
an event or reaches `max_events` is reported as `partial` without discarding
valid projections already rebuilt.

## Isolation and side effects

Replay never invokes connectors, the EventBus notifier path, notification/email/
Slack delivery, CRM workflow mutation, or WebSocket broadcast. Recommendations
use the pure decision boundary and are written only to the disposable replay
store. The store is removed before `ReplayResult` is returned. The result records
cutoff, mode, selected input IDs, counts, errors, schemas/engine versions,
digests, suppressed side effects, and the explicit `live_state_mutated=false`
invariant.

From M4 onward, the semantic snapshot and projection counts include features.
From M5 onward, replay independently selects immutable CRM actions using the
same requested time axis as events. Eligible action IDs and action/feedback
versions enter the replay digest and snapshot; actions observed after the
cutoff cannot affect historical features, rankings, or recommendations. Replay
writes actions only to the disposable store and never materializes or mutates a
live CRM board. Replay metadata records feature, signal, ranking,
recommendation, and CRM-action versions so downstream evaluation remains
auditable after formula changes.

Remote replay execution is intentionally not exposed. The API publishes only a
typed read-only contract at `GET /api/v1/replay/contract`, and the React `/replay`
route renders that contract without an execution control.

## Local CLI

Use a timezone-aware ISO-8601 cutoff:

```powershell
uv run python -m venture_intelligence.replay.cli `
  --database data/intelligence.db `
  --until 2026-01-15T12:00:00+00:00 `
  --mode observed_at `
  --max-events 5000
```

Optional `--from` creates a bounded range. `--mode occurred_at` requests the
explicit event-time reconstruction. The CLI prints a JSON `ReplayResult`; it
returns a non-zero exit for partial or failed runs.

Milestone 3 now consumes this unchanged isolation boundary for typed ground-truth
metrics and bounded source ablation. Evaluation execution remains local-only;
the React `/evaluate` workspace reads persisted reports. See
[`EVALUATION.md`](EVALUATION.md). Remote administration remains deferred.
