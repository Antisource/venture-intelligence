"""Event log and per-worker event queue persistence."""

import json
import sqlite3
from datetime import datetime, timezone

from venture_intelligence.shared.models import (
    CanonicalEvent,
)


class EventsMixin:
    # ════════════════════════════════════════════════════════════════════════════
    # EVENTS
    # ════════════════════════════════════════════════════════════════════════════

    def write_event(self, event: CanonicalEvent) -> None:
        """
        Persist a CanonicalEvent.  Idempotent on event_id (INSERT OR IGNORE).
        This is the only entry point for new events — nothing else writes
        directly to the events table.
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO events (
                    event_id, schema_version, event_type, entity_type, entity_id,
                    source, source_url, occurred_at, occurred_at_precision,
                    observed_at, ingested_at, processed_at, ranked_at,
                    notified_at, validity, confidence, raw_payload, metadata
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event.event_id,
                    event.schema_version,
                    event.event_type,
                    event.entity_type,
                    event.entity_id,
                    event.source,
                    event.source_url,
                    self._ts(event.occurred_at),
                    event.occurred_at_precision,
                    self._ts(event.observed_at),
                    self._ts(event.ingested_at),
                    self._ts(event.processed_at),
                    self._ts(event.ranked_at),
                    self._ts(event.notified_at),
                    json.dumps(event.validity.model_dump(mode="json"))
                    if event.validity is not None
                    else None,
                    event.confidence,
                    json.dumps(event.raw_payload),
                    json.dumps(event.metadata),
                ),
            )
            # Enqueue for all pipeline workers
            for worker in [
                "validator",
                "resolver",
                "graph_builder",
                "evidence_builder",
                "signal_extractor",
                "ranker",
                "recommender",
                "notifier",
            ]:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO event_queue
                      (event_id, worker, status, created_at)
                    VALUES (?,?,'pending',?)
                    """,
                    (event.event_id, worker, self._ts(event.ingested_at)),
                )

    def mark_processed(self, event_id: str, worker: str) -> None:
        """Mark one worker's queue entry as done."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE event_queue SET status='done', processed_at=?
                WHERE event_id=? AND worker=?
                """,
                (now, event_id, worker),
            )

    def mark_failed(self, event_id: str, worker: str, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE event_queue SET status='failed', error=?
                WHERE event_id=? AND worker=?
                """,
                (error, event_id, worker),
            )

    def pending_events(self, worker: str, limit: int = 100) -> list[CanonicalEvent]:
        """Return up to `limit` events not yet processed by `worker`."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.* FROM events e
                JOIN event_queue q ON q.event_id = e.event_id
                WHERE q.worker=? AND q.status='pending'
                ORDER BY e.ingested_at ASC
                LIMIT ?
                """,
                (worker, limit),
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def queue_status(self, event_id: str, worker: str) -> str | None:
        """Return a worker queue status for downstream validation gates."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM event_queue WHERE event_id=? AND worker=?",
                (event_id, worker),
            ).fetchone()
        return row["status"] if row else None

    def get_events_for_entity(self, entity_id: str) -> list[CanonicalEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE entity_id=? ORDER BY occurred_at ASC",
                (entity_id,),
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def _row_to_event(self, row: sqlite3.Row) -> CanonicalEvent:
        return CanonicalEvent(
            event_id=row["event_id"],
            schema_version=row["schema_version"],
            event_type=row["event_type"],
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            source=row["source"],
            source_url=row["source_url"],
            occurred_at=self._from_ts(row["occurred_at"]),
            occurred_at_precision=row["occurred_at_precision"],
            observed_at=self._from_ts(row["observed_at"]),
            ingested_at=self._from_ts(row["ingested_at"]),
            processed_at=self._from_ts(row["processed_at"]),
            ranked_at=self._from_ts(row["ranked_at"]),
            notified_at=self._from_ts(row["notified_at"]),
            validity=json.loads(row["validity"]) if row["validity"] else None,
            confidence=row["confidence"],
            raw_payload=json.loads(row["raw_payload"]),
            metadata=json.loads(row["metadata"]),
        )
