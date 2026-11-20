"""
shared/bus.py — SQLite-backed event bus.

Implements the pub/sub contract described in §13.

Design:
  The event_queue table (created by storage/db/schema.py) acts as a durable message queue.
  Each (event_id, worker) pair is a separate queue entry with its own status.
  This gives every worker independent, exactly-once delivery per event.

Interface contract (§6.1 — swap for Kafka in V2 without changing callers):
  publish(event)           — write event + enqueue for all workers
  consume(worker, limit)   — return pending events for this worker
  ack(event_id, worker)    — mark processed
  nack(event_id, worker, error) — mark failed for retry

§5 note: No Kafka in V1.  This bus is intentionally simple —
the interface is what matters for future migration.
"""

import logging

from venture_intelligence.pipeline.entity_integrity import validate_candidate
from venture_intelligence.shared.models import CanonicalEvent
from venture_intelligence.storage.db import Database

logger = logging.getLogger(__name__)


class EventBus:
    """
    Thin façade over the database's event_queue table.

    Workers import this class, not the Database directly, so the
    transport layer (SQLite → Kafka) can be swapped without touching
    any worker code.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def publish(self, event: CanonicalEvent) -> bool:
        """
        Write the event to the events table and enqueue it for all workers.

        Idempotent: publishing the same event_id twice is a no-op.
        All eight downstream workers receive their own queue entry.
        """
        validation = validate_candidate(event, self._db)
        if not validation.accepted:
            self._db.write_candidate_rejection(
                candidate_label=validation.label or event.entity_id,
                entity_type=event.entity_type.value,
                source=event.source.value,
                source_url=event.source_url,
                query=str(event.metadata.get("query") or "") or None,
                reason=validation.reason or "candidate_validation_failed",
                confidence=event.confidence,
                provenance={
                    "event_id": event.event_id,
                    "metadata": event.metadata,
                    "observed_at": event.observed_at.isoformat(),
                },
                raw_candidate=event.raw_payload,
            )
            logger.info(
                "candidate_rejected reason=%s label=%r source=%s",
                validation.reason,
                validation.label,
                event.source,
            )
            return False
        self._db.write_event(event)
        logger.info(
            "published event_id=%s type=%s entity=%s source=%s",
            event.event_id,
            event.event_type,
            event.entity_id,
            event.source,
        )
        return True

    def consume(self, worker: str, limit: int = 50) -> list[CanonicalEvent]:
        """
        Return up to `limit` events not yet processed by this worker.

        Events are returned oldest-first (FIFO) so the pipeline processes
        events in the order they were ingested.
        """
        events = self._db.pending_events(worker, limit)
        if events:
            logger.debug("worker=%s dequeued %d events", worker, len(events))
        return events

    def ack(self, event_id: str, worker: str) -> None:
        """Mark this (event, worker) pair as successfully processed."""
        self._db.mark_processed(event_id, worker)

    def nack(self, event_id: str, worker: str, error: str) -> None:
        """
        Mark this (event, worker) pair as failed.

        Failed events are not retried automatically in V1 — they require
        manual re-queue or operator intervention.  A retry loop is planned
        for V2.
        """
        self._db.mark_failed(event_id, worker, error)
        logger.warning("worker=%s failed event_id=%s error=%s", worker, event_id, error)

    def queue_depth(self, worker: str) -> int:
        """Return how many events are waiting for this worker."""
        return len(self._db.pending_events(worker, limit=10_000))

    def queue_status(self, event_id: str, worker: str) -> str | None:
        """Expose durable queue status for validation/dependency gates."""
        return self._db.queue_status(event_id, worker)

    def publish_batch(self, events: list[CanonicalEvent]) -> int:
        """
        Publish multiple events atomically.
        Returns the count of actually-new events (duplicates are silently skipped).
        """
        published = 0
        for event in events:
            if self.publish(event):
                published += 1
        return published
