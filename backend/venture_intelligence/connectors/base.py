"""
connectors/base.py — agentic connector interface and coordinator.

The system operates in two modes (§7 Pipeline Architecture):

  SCAN mode   — each connector periodically sweeps its source autonomously.
                Scheduler calls connector.scan() on a fixed interval.

  INVESTIGATE mode — when a connector publishes a discovery, the
                ConnectorCoordinator routes it to every other connector
                that declares it can follow up. This creates a self-
                propelling multi-hop discovery loop:

                  YC finds "Acme" (COMPANY_LISTED)
                    → GitHub searches for Acme repos  (REPO_CREATED)
                    → LinkedIn searches for Acme founders  (FOUNDER_DETECTED)
                      → GitHub looks up founder's profile  (COMMIT_VELOCITY_HIGH)
                        → HN searches for founder's name  (HN_MENTIONED)
                          → [no connector can go deeper → loop ends]

Cycle prevention:
  event.metadata["investigation_depth"] tracks how many hops deep we are.
  No connector investigates events at depth >= MAX_INVESTIGATION_DEPTH.
  Entity+source pairs are deduplicated so the same lookup never runs twice.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from venture_intelligence.shared.bus import EventBus
from venture_intelligence.shared.models import CanonicalEvent

logger = logging.getLogger(__name__)

# Maximum hops in the cross-connector investigation chain.
# Depth 0 = connector's own scan; depth 3 = three rounds of follow-up.
MAX_INVESTIGATION_DEPTH = 3


class BaseConnector(ABC):
    """
    Abstract base for all source connectors.

    Every connector implements two modes:
      scan()        — autonomous sweep; called by the scheduler
      investigate() — targeted follow-up on an event from another connector

    The agentic contract: investigate() should return everything the
    connector can infer from the triggering event.  It must never publish
    to the bus itself — that's the coordinator's job, so the deduplication
    and depth tracking stay in one place.
    """

    #: Stable name written into every emitted event's source field.
    source_name: str

    @abstractmethod
    async def scan(self) -> list[CanonicalEvent]:
        """
        Autonomous periodic sweep of the connector's source.

        Returns all currently visible YC F26 signals — deduplication
        against previously seen entities is the pipeline's job, not the
        connector's.  Connectors always return everything they see.
        """

    @abstractmethod
    async def investigate(self, trigger: CanonicalEvent) -> list[CanonicalEvent]:
        """
        Targeted investigation triggered by a discovery from another connector.

        trigger — the event that prompted this investigation.
        Returns new events the connector found about the trigger's entity.
        Returns [] if nothing useful was found.

        Implementations must not call bus.publish() — return events only.
        """

    @abstractmethod
    def can_investigate(self, event: CanonicalEvent) -> bool:
        """
        Return True if this connector can usefully follow up on `event`.

        Called by ConnectorCoordinator before dispatching investigate().
        Keep this cheap — it runs for every event × every connector.
        """

    @abstractmethod
    async def close(self) -> None:
        """Release held resources (browser, HTTP session, etc.)."""

    # ── Shared utility ────────────────────────────────────────────────────────

    @staticmethod
    def child_event(
        trigger: CanonicalEvent,
        **overrides,
    ) -> dict:
        """
        Produce kwargs for a follow-up CanonicalEvent that inherits
        investigation_depth from its parent.

        Usage:
          CanonicalEvent(**self.child_event(trigger, event_type=..., ...))
        """
        depth = trigger.metadata.get("investigation_depth", 0) + 1
        return {
            "occurred_at": datetime.now(timezone.utc),
            **overrides,
            "metadata": {
                **overrides.get("metadata", {}),
                "investigation_depth": depth,
                "triggered_by": trigger.event_id,
            },
        }


class ConnectorCoordinator:
    """
    The agentic brain that routes discoveries across connectors (§7).

    Workflow per event:
      1. Receive newly published event from the bus.
      2. Ask each registered connector: can_investigate(event)?
      3. Run investigate() on each that says yes (concurrently).
      4. Publish the resulting follow-up events back to the bus.
      5. Repeat until no connector produces new events.

    The coordinator also maintains an in-session deduplication set
    so the same (entity_id, source) pair is never investigated twice.
    This is a runtime guard in addition to the database's event
    INSERT OR IGNORE — it avoids redundant network calls entirely.
    """

    def __init__(self, connectors: list[BaseConnector], bus: EventBus) -> None:
        self._connectors = connectors
        self._bus = bus
        # (entity_id, connector.source_name) → True means "already investigated"
        self._investigated: set[tuple[str, str]] = set()

    async def route(self, event: CanonicalEvent) -> int:
        """
        Route one event to all connectors that can investigate it.

        Returns the total number of follow-up events published.
        """
        depth = event.metadata.get("investigation_depth", 0)
        if depth >= MAX_INVESTIGATION_DEPTH:
            logger.debug(
                "max depth %d reached for entity=%s — stopping chain",
                MAX_INVESTIGATION_DEPTH,
                event.entity_id,
            )
            return 0

        # Gather connectors that can follow up on this event
        eligible = [
            c
            for c in self._connectors
            if c.can_investigate(event)
            and (event.entity_id, c.source_name) not in self._investigated
        ]

        if not eligible:
            return 0

        # Run all eligible investigations concurrently
        tasks = [
            asyncio.create_task(self._run_connector_operation(c, c.investigate(event)))
            for c in eligible
        ]
        results = await asyncio.gather(*tasks)

        published = 0
        for connector, (result, operation_id) in zip(eligible, results):
            # Mark investigated regardless of outcome so we don't retry forever
            self._investigated.add((event.entity_id, connector.source_name))

            if isinstance(result, Exception):
                self._record_evidence_outcome(
                    connector,
                    [],
                    operation_id=operation_id,
                )
                logger.error("connector=%s investigate() raised: %s", connector.source_name, result)
                continue

            self._record_evidence_outcome(
                connector,
                result,
                operation_id=operation_id,
            )

            for follow_up in result:
                if not self._bus.publish(follow_up):
                    continue
                published += 1
                logger.info(
                    "agentic follow-up: connector=%s entity=%s type=%s depth=%d",
                    connector.source_name,
                    follow_up.entity_id,
                    follow_up.event_type,
                    follow_up.metadata.get("investigation_depth", 0),
                )

        return published

    async def run_scan_and_route(self, connector: BaseConnector) -> int:
        """
        Execute one full agentic cycle for a connector:
          scan → publish primary events → route each for cross-connector follow-up.

        Returns total events published (primary + all follow-ups).
        """
        logger.info("agentic scan starting: connector=%s", connector.source_name)

        try:
            primary_events, operation_id = await self._run_connector_operation(
                connector,
                connector.scan(),
            )
        except Exception as exc:
            logger.error("connector=%s scan() raised: %s", connector.source_name, exc)
            return 0

        if isinstance(primary_events, Exception):
            self._record_evidence_outcome(
                connector,
                [],
                operation_id=operation_id,
            )
            logger.error(
                "connector=%s scan() raised: %s",
                connector.source_name,
                primary_events,
            )
            return 0

        self._record_evidence_outcome(
            connector,
            primary_events,
            operation_id=operation_id,
        )

        total = 0
        for event in primary_events:
            if not self._bus.publish(event):
                continue
            total += 1
            # Immediately route for cross-connector follow-up
            follow_ups = await self.route(event)
            total += follow_ups

        logger.info(
            "agentic scan complete: connector=%s total_events=%d",
            connector.source_name,
            total,
        )
        return total

    @staticmethod
    def _record_evidence_outcome(
        connector: BaseConnector,
        events: list[CanonicalEvent],
        *,
        operation_id: str | None = None,
    ) -> None:
        crawler = getattr(connector, "crawler", None)
        recorder = getattr(crawler, "record_evidence_outcome", None)
        if recorder is not None:
            evidence_urls = (event.source_url for event in events)
            if operation_id is None:
                recorder(evidence_urls)
            else:
                recorder(evidence_urls, operation_id=operation_id)

    @staticmethod
    async def _run_connector_operation(connector, awaitable):
        """Run one connector call with task-local crawler outcome attribution."""
        crawler = getattr(connector, "crawler", None)
        begin = getattr(crawler, "begin_evidence_operation", None)
        end = getattr(crawler, "end_evidence_operation", None)
        operation_id = None
        token = None
        if begin is not None and end is not None:
            operation_id, token = begin()
        try:
            try:
                result = await awaitable
            except Exception as exc:
                result = exc
            return result, operation_id
        finally:
            if token is not None:
                end(token)

    def reset_session_cache(self) -> None:
        """
        Clear the in-session deduplication cache.

        Call this at the start of each polling cycle so entities discovered
        in a new scan are re-investigated with fresh data.
        """
        self._investigated.clear()
