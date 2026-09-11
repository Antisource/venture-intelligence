"""Monitor orchestration: resilient connector scans, pipeline, and API."""

from __future__ import annotations

import asyncio
import logging
import sys
import time

import uvicorn

from venture_intelligence.agent.loop import AgentLoop
from venture_intelligence.api.app import app as fastapi_app
from venture_intelligence.config import settings
from venture_intelligence.connectors.base import ConnectorCoordinator
from venture_intelligence.connectors.factory import build_connectors
from venture_intelligence.observability.metrics import metrics
from venture_intelligence.pipeline.entity_resolution import EntityResolver
from venture_intelligence.pipeline.evidence_builder import EvidenceBuilder
from venture_intelligence.pipeline.graph import GraphBuilder
from venture_intelligence.pipeline.notification import NotificationWorker
from venture_intelligence.pipeline.ranking_worker import RankingWorker
from venture_intelligence.pipeline.recommendation_worker import RecommendationWorker
from venture_intelligence.pipeline.signal_worker import SignalWorker
from venture_intelligence.pipeline.validator import EventValidator
from venture_intelligence.programs import get_program_registry
from venture_intelligence.shared.bus import EventBus
from venture_intelligence.storage.db import Database

logger = logging.getLogger(__name__)
PIPELINE_INTERVAL = 60


async def run_connector(connector, coordinator: ConnectorCoordinator) -> None:
    """Keep one connector alive; a source error never stops its peers."""
    try:
        while True:
            started = time.monotonic()
            coordinator.reset_session_cache()
            await coordinator.run_scan_and_route(connector)
            metrics.record_duration(
                f"connector_scan_ms.{connector.source_name}", (time.monotonic() - started) * 1000
            )
            await asyncio.sleep(settings.connector_poll_interval)
    except asyncio.CancelledError:
        raise
    finally:
        await connector.close()


async def pipeline_tick(
    bus: EventBus,
    db: Database,
    validator: EventValidator,
    resolver: EntityResolver,
    graph: GraphBuilder,
    evidence: EvidenceBuilder,
    signals: SignalWorker,
    ranker: RankingWorker,
    recommender: RecommendationWorker,
    notifier: NotificationWorker,
) -> set[str]:
    """Process all queued events while isolating failures to the affected item."""
    for event in bus.consume("validator"):
        valid, error = validator.validate(event)
        if valid:
            bus.ack(event.event_id, "validator")
        else:
            bus.nack(event.event_id, "validator", error or "validation failed")
    affected: set[str] = set()
    for worker, handler in (
        ("resolver", lambda event: resolver.resolve(event)),
        (
            "graph_builder",
            lambda event: graph.process(
                event, db.resolve_entity_id(event.entity_id) or event.entity_id
            ),
        ),
        (
            "evidence_builder",
            lambda event: evidence.process(
                event, db.resolve_entity_id(event.entity_id) or event.entity_id
            ),
        ),
    ):
        for event in bus.consume(worker):
            if bus.queue_status(event.event_id, "validator") == "failed":
                bus.nack(event.event_id, worker, "blocked by failed validator")
                continue
            try:
                handler(event)
                bus.ack(event.event_id, worker)
                if worker == "evidence_builder":
                    affected.add(db.resolve_entity_id(event.entity_id) or event.entity_id)
            except Exception as error:
                logger.warning(
                    "pipeline worker=%s event=%s error=%s", worker, event.event_id, error
                )
                bus.nack(event.event_id, worker, str(error))
    for entity_id in affected:
        try:
            signals.process_entity(entity_id)
        except Exception as error:
            logger.warning("signal extraction entity=%s error=%s", entity_id, error)
    if affected:
        for worker in (ranker.rank_all, recommender.generate_all, notifier.dispatch_pending):
            try:
                worker()
            except Exception as error:
                logger.warning("post-processing error=%s", error)
    return affected


async def run_pipeline(*args) -> None:
    while True:
        await pipeline_tick(*args)
        await asyncio.sleep(PIPELINE_INTERVAL)


async def main() -> None:
    # Importing the projection worker must not configure process-wide logging
    # (or contaminate a CLI JSON result). Legacy terminals may not encode names.
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="backslashreplace")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        stream=sys.stderr,
    )
    db = Database(settings.db_path)
    bus = EventBus(db)
    registry = get_program_registry()
    connectors = build_connectors(registry)
    coordinator = ConnectorCoordinator(connectors, bus)
    workers = (
        EventValidator(),
        EntityResolver(db),
        GraphBuilder(db),
        EvidenceBuilder(db),
        SignalWorker(db),
        RankingWorker(db),
        RecommendationWorker(db),
        NotificationWorker(db),
    )
    agent = AgentLoop(db, bus, coordinator)
    server = uvicorn.Server(
        uvicorn.Config(fastapi_app, host=settings.api_host, port=settings.api_port)
    )
    logger.info("venture-intelligence starting with %d configured connectors", len(connectors))
    await asyncio.gather(
        server.serve(),
        run_pipeline(bus, db, *workers),
        agent.run(),
        *(run_connector(connector, coordinator) for connector in connectors),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Monitor stopped")
