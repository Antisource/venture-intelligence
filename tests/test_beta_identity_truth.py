"""Beta-delivery regressions for identity attribution and product truth."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from venture_intelligence.api.app import app
from venture_intelligence.config import Settings
from venture_intelligence.intelligence.service import IntelligenceService
from venture_intelligence.knowledge.compiler import KnowledgeCompiler
from venture_intelligence.monitor import pipeline_tick
from venture_intelligence.pipeline.entity_resolution import EntityResolver
from venture_intelligence.pipeline.evidence_builder import EvidenceBuilder
from venture_intelligence.pipeline.graph import GraphBuilder
from venture_intelligence.pipeline.notification import NotificationWorker
from venture_intelligence.pipeline.ranking_worker import RankingWorker
from venture_intelligence.pipeline.recommendation_worker import RecommendationWorker
from venture_intelligence.pipeline.signal_worker import SignalWorker
from venture_intelligence.pipeline.validator import EventValidator
from venture_intelligence.research.agent import ResearchAgent
from venture_intelligence.research.sources import SearchResponse, SearchResult
from venture_intelligence.shared.bus import EventBus
from venture_intelligence.shared.models import (
    CanonicalEvent,
    EntityType,
    EventType,
    ResearchBudget,
    ResearchSourceStatus,
    ResearchTarget,
    ResearchTargetType,
    Source,
)
from venture_intelligence.storage.db import Database

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


class SameNameProvider:
    domain = "public-search.example"

    async def search(self, query: str, max_results: int) -> SearchResponse:
        del max_results
        return SearchResponse(
            query=query,
            status=ResearchSourceStatus.AVAILABLE,
            provider_domain=self.domain,
            retrieved_at=NOW.isoformat(),
            results=[
                SearchResult(
                    url="https://www.linkedin.com/in/alex-kim-founder",
                    title="Alex Kim — Founder and CEO at Alpha Robotics",
                    snippet="Alex Kim founded Alpha Robotics.",
                    source_domain="linkedin.com",
                ),
                SearchResult(
                    url="https://www.ycombinator.com/companies/alpha-robotics",
                    title="Alpha Robotics — Alex Kim, founder",
                    snippet="Alex Kim is the founder of Alpha Robotics.",
                    source_domain="ycombinator.com",
                ),
                SearchResult(
                    url="https://www.imdb.com/name/nm0000001",
                    title="Alex Kim — actor",
                    snippet="Alex Kim is an actor and singer.",
                    source_domain="imdb.com",
                ),
                SearchResult(
                    url="https://www.facebook.com/alex.kim.music",
                    title="Alex Kim Music",
                    snippet="Alex Kim performs live music.",
                    source_domain="facebook.com",
                ),
            ],
        )

    async def close(self) -> None:
        return None


def _official_founder_events() -> tuple[CanonicalEvent, CanonicalEvent]:
    source_url = "https://speedrun-api.a16z.com/api/companies/companies/?cohort=SR007"
    company = CanonicalEvent(
        event_type=EventType.COMPANY_LISTED,
        entity_type=EntityType.COMPANY,
        entity_id="speedrun_current__alpha-robotics",
        source=Source.DIRECTORY,
        source_url=source_url,
        occurred_at=NOW,
        observed_at=NOW,
        ingested_at=NOW,
        confidence=0.96,
        raw_payload={
            "name": "Alpha Robotics",
            "batch": "SR007",
            "program_id": "speedrun_current",
        },
        metadata={"program_id": "speedrun_current", "batch_id": "SR007"},
    )
    founder = CanonicalEvent(
        event_type=EventType.FOUNDER_DETECTED,
        entity_type=EntityType.PERSON,
        entity_id="speedrun_current__alpha-robotics__alex-kim",
        source=Source.DIRECTORY,
        source_url=source_url,
        occurred_at=NOW,
        observed_at=NOW,
        ingested_at=NOW,
        confidence=0.96,
        raw_payload={
            "name": "Alex Kim",
            "headline": "Founder",
            "company_name": "Alpha Robotics",
            "company_source_id": company.entity_id,
            "company_source_url": source_url,
            "profile_url": "https://www.linkedin.com/in/alex-kim-founder",
            "founder_claim": {
                "full_name": "Alex Kim",
                "title": "Founder",
                "linkedin_url": "https://www.linkedin.com/in/alex-kim-founder",
                "_claim_container": "founder_set",
            },
        },
        metadata={
            "program_id": "speedrun_current",
            "batch_id": "SR007",
            "identity_evidence": "explicit_public_founder_record",
            "parent_event_id": company.event_id,
        },
    )
    return company, founder


async def _drain_pipeline(database: Database, bus: EventBus) -> None:
    await pipeline_tick(
        bus,
        database,
        EventValidator(),
        EntityResolver(database),
        GraphBuilder(database),
        EvidenceBuilder(database),
        SignalWorker(database),
        RankingWorker(database),
        RecommendationWorker(database),
        NotificationWorker(database),
    )


def test_common_name_evidence_stays_unbound_end_to_end(tmp_path) -> None:
    database = Database(tmp_path / "identity-collision.db")
    bus = EventBus(database)
    company, founder = _official_founder_events()
    assert bus.publish(company)
    assert bus.publish(founder)
    asyncio.run(_drain_pipeline(database, bus))
    founder_id = database.resolve_entity_id(founder.entity_id)
    assert founder_id is not None

    agent = ResearchAgent(
        SameNameProvider(),
        runtime_settings=Settings(research_cache_ttl_seconds=600),
        knowledge_compiler=KnowledgeCompiler(tmp_path / "knowledge"),
    )
    run = asyncio.run(
        agent.run(
            ResearchTarget(
                name="Alex Kim",
                type=ResearchTargetType.PERSON,
                program_id="speedrun_current",
                aliases=["Alpha Robotics", "a16z Speedrun"],
            ),
            ResearchBudget(
                max_queries=1,
                max_depth=0,
                max_results_per_query=5,
                max_runtime_seconds=5,
            ),
            event_bus=bus,
        )
    )
    asyncio.run(agent.close())
    asyncio.run(_drain_pipeline(database, bus))

    attached = [item for item in run.evidence if item.attribution_status == "attached"]
    unbound = [item for item in run.evidence if item.attribution_status == "unbound"]
    assert {item.source_domain for item in attached} == {"linkedin.com", "ycombinator.com"}
    assert {item.source_domain for item in unbound} == {"imdb.com", "facebook.com"}
    assert run.independent_evidence_origins == 2
    assert run.identity_status == "corroborated"
    assert run.events_emitted == 2

    research_events = database.get_events_for_entity("research__alex-kim")
    assert {item.source_url for item in research_events} == {
        "https://www.linkedin.com/in/alex-kim-founder",
        "https://www.ycombinator.com/companies/alpha-robotics",
    }
    assert database.resolve_entity_id("research__alex-kim") == founder_id

    view = IntelligenceService(database).entity(founder_id)
    assert view is not None
    canonical_urls = {url for item in view.evidence for url in item.source_urls}
    assert "https://www.imdb.com/name/nm0000001" not in canonical_urls
    assert "https://www.facebook.com/alex.kim.music" not in canonical_urls
    assert view.entity.program_status == "verified"

    canonical_page = KnowledgeCompiler(tmp_path / "knowledge").load("founder", "Alex Kim")
    ambiguous_page = KnowledgeCompiler(tmp_path / "knowledge").load(
        "investigation", "candidate-alex-kim"
    )
    assert canonical_page is not None and ambiguous_page is not None
    assert {item["source_domain"] for item in canonical_page.evidence} == {
        "linkedin.com",
        "ycombinator.com",
    }
    assert {item["source_domain"] for item in ambiguous_page.evidence} == {
        "imdb.com",
        "facebook.com",
    }
    assert all(claim.status.value == "unknown" for claim in ambiguous_page.claims)

    with database._connect() as connection:
        founded = connection.execute(
            "SELECT source_id, target_id FROM graph_edges WHERE edge_type='FOUNDED'"
        ).fetchall()
    assert [(item["source_id"], item["target_id"]) for item in founded] == [
        (founder_id, database.resolve_entity_id(company.entity_id))
    ]


def test_run_level_identity_flag_cannot_bless_an_unbound_result(tmp_path) -> None:
    database = Database(tmp_path / "event-boundary.db")
    event = CanonicalEvent(
        event_type=EventType.RESEARCH_DISCOVERED,
        entity_type=EntityType.PERSON,
        entity_id="research__alex-kim",
        source=Source.RESEARCH_AGENT,
        source_url="https://www.imdb.com/name/nm0000001",
        occurred_at=NOW,
        observed_at=NOW,
        ingested_at=NOW,
        confidence=0.75,
        raw_payload={"name": "Alex Kim"},
        metadata={
            "identity_validated": True,
            "identity_evidence": True,
            "identity_evidence_urls": [
                "https://www.linkedin.com/in/alex-kim-founder"
            ],
            "attribution_status": "unbound",
            "supporting_source_count": 3,
        },
    )
    assert EventBus(database).publish(event) is False
    assert database.get_events_for_entity(event.entity_id) == []


def test_duplicate_index_paths_remain_one_unbound_evidence_origin() -> None:
    class Provider:
        def __init__(self, domain: str, tracking: str) -> None:
            self.domain = domain
            self.tracking = tracking

        async def search(self, query: str, max_results: int) -> SearchResponse:
            del max_results
            return SearchResponse(
                query=query,
                status=ResearchSourceStatus.AVAILABLE,
                provider_domain=self.domain,
                retrieved_at=NOW.isoformat(),
                results=[
                    SearchResult(
                        url=f"https://example.com/alex-profile?utm_source={self.tracking}",
                        title="Alex Kim public profile",
                        snippet="Alex Kim is a singer.",
                        source_domain="example.com",
                        metadata={
                            "acquisition_provider": self.domain,
                            "acquisition_providers": [self.domain],
                            "evidence_origin": "https://example.com/alex-profile",
                        },
                    )
                ],
            )

        async def close(self) -> None:
            return None

    agent = ResearchAgent(
        [Provider("search-a.example", "a"), Provider("search-b.example", "b")],
        runtime_settings=Settings(research_cache_ttl_seconds=600),
    )
    run = asyncio.run(
        agent.run(
            ResearchTarget(name="Alex Kim", type=ResearchTargetType.PERSON),
            ResearchBudget(
                max_queries=1,
                max_depth=0,
                max_results_per_query=2,
                max_runtime_seconds=5,
            ),
        )
    )
    asyncio.run(agent.close())
    assert len(run.evidence) == 1
    assert run.evidence[0].attribution_status.value == "unbound"
    assert set(run.evidence[0].acquisition_providers) == {
        "search-a.example",
        "search-b.example",
    }
    assert run.independent_evidence_origins == 0
    assert run.identity_status.value == "ambiguous"
    assert run.contradictory_evidence_count == 0


def test_entity_scoped_assistant_refuses_global_list_question() -> None:
    for question in (
        "Give me a list of YC companies from the latest batch",
        "Compare every company",
    ):
        response = TestClient(app).post(
            "/api/analyst/chat",
            json={"question": question, "context_name": "Alpha Robotics"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["model"] == "deterministic-scope-boundary"
        assert payload["evidence_sufficient"] is False
        assert payload["citations"] == []
        assert payload["answer"] == (
            "This assistant is scoped to Alpha Robotics. "
            "Use Discover or command search for cross-company questions."
        )
