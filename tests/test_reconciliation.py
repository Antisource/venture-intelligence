"""Regression coverage for requirements reconciled after the final audit."""

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from venture_intelligence.agents.analyst import AnalystAgent, OllamaQwenProvider
from venture_intelligence.agents.environmental_extractor import EnvironmentalExtractor
from venture_intelligence.api.app import app
from venture_intelligence.config import Settings
from venture_intelligence.connectors.environmental import (
    DiscordConnector,
    SocialConnector,
    WebsiteConnector,
)
from venture_intelligence.connectors.factory import build_connectors
from venture_intelligence.connectors.public_sources import (
    ArxivConnector,
    GitHubConnector,
    LinkedInConnector,
    ProductHuntConnector,
)
from venture_intelligence.crawler import CrawlResult, StealthCrawler
from venture_intelligence.pipeline.entity_resolution import EntityResolver
from venture_intelligence.programs import ProgramConfig, ProgramRegistry
from venture_intelligence.shared.models import CanonicalEvent, EntityType, EventType, Source
from venture_intelligence.storage.cleanup import (
    cleanup_generic_derived_state,
    quarantine_generic_entities,
)
from venture_intelligence.storage.db import Database


class FakeCrawler:
    def __init__(self, rendered_text: str = "", widget: dict | None = None) -> None:
        self.rendered_text = rendered_text
        self.widget = widget
        self.urls: list[str] = []
        self.authorized_headers: list[dict[str, str]] = []

    async def fetch_text(self, url: str) -> CrawlResult:
        self.urls.append(url)
        body = json.dumps(self.widget) if self.widget is not None else self.rendered_text
        return CrawlResult(url, body, 200)

    async def fetch_rendered_text(self, url: str) -> CrawlResult:
        self.urls.append(url)
        return CrawlResult(url, self.rendered_text, 200)

    async def fetch_text_authorized(self, url: str, *, headers: dict[str, str]) -> CrawlResult:
        self.authorized_headers.append(headers)
        return await self.fetch_text(url)

    async def close(self) -> None:
        return None


def _registry(
    discord_servers: list[str] | None = None,
    website_urls: list[str] | None = None,
) -> ProgramRegistry:
    return ProgramRegistry(
        [
            ProgramConfig(
                id="test_program",
                name="Test Program",
                batch="T01",
                detection_strategy="triangulation",
                base_confidence=0.75,
                keywords=["Test Program", "TP01"],
                entity_id_prefix="test",
                discord_servers=discord_servers or [],
                website_urls=website_urls or [],
            )
        ]
    )


def _trigger(**payload: str) -> CanonicalEvent:
    return CanonicalEvent(
        event_type=EventType.FOUNDER_DETECTED,
        entity_type=EntityType.PERSON,
        entity_id="test__alex",
        source=Source.API,
        source_url="api://test",
        occurred_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        raw_payload={"name": "alex", **payload},
    )


def test_registry_exposes_discord_servers() -> None:
    assert _registry(["123"]).serializable()[0]["discord_servers"] == ["123"]


def test_registry_exposes_public_website_urls() -> None:
    assert _registry(website_urls=["https://example.test/{query}"]).serializable()[0][
        "website_urls"
    ] == ["https://example.test/{query}"]


def test_program_registry_entity_id_is_prefixed() -> None:
    assert _registry().entity_id("test_program", "Acme Labs") == "test__acme-labs"


def test_crawler_rejects_non_http_urls() -> None:
    result = __import__("asyncio").run(StealthCrawler().fetch_text("file:///private"))
    assert not result.ok and "http" in result.error


async def test_crawler_circuit_breaker_bounds_repeated_failures() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, request=request)

    runtime = Settings(crawler_failure_threshold=2, crawler_circuit_cooldown_seconds=60)
    crawler = StealthCrawler(runtime)
    crawler._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await crawler.fetch_text("https://example.test/one")
        await crawler.fetch_text("https://example.test/two")
        result = await crawler.fetch_text("https://example.test/three")
        assert "circuit open" in (result.error or "")
        assert calls == 2
    finally:
        await crawler.close()


async def test_crawler_classifies_login_redirect_as_auth_required() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, content=b"<html>Sign in to continue</html>")

    crawler = StealthCrawler()
    crawler._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )
    try:
        result = await crawler.fetch_text("https://www.linkedin.com/search/results/people/")
        assert not result.ok and "authentication required" in (result.error or "")
    finally:
        await crawler.close()


async def test_crawlee_adapter_invocation_is_bounded(monkeypatch) -> None:
    calls = []

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b'{"ok":true,"status_code":200,"text":"public page","error":null}', b""

    async def fake_exec(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    crawler = StealthCrawler(
        Settings(crawler_render_enabled=True, crawler_engine="crawlee", connector_timeout_seconds=1)
    )
    result = await crawler.fetch_rendered_text("https://example.test")
    assert result.ok
    assert calls and calls[0][0][0] == "node"
    assert Path(calls[0][0][1]).parts[-2:] == ("scripts", "crawlee_public_fetcher.mjs")


def test_crawlee_runtime_dependencies_are_declared() -> None:
    dependencies = json.loads(Path("package.json").read_text(encoding="utf-8"))["dependencies"]
    assert "crawlee" in dependencies
    assert "playwright" in dependencies
    script = Path("scripts/crawlee_public_fetcher.mjs").read_text(encoding="utf-8")
    assert "log.setLevel(LogLevel.OFF)" in script
    assert "useSessionPool: false" in script
    assert "persistCookiesPerSession: false" in script


async def test_social_scan_uses_x_and_instagram() -> None:
    crawler = FakeCrawler(rendered_text="Test Program @alex and @mira")
    events = await SocialConnector(
        _registry(),
        crawler,
        runtime_settings=Settings(restricted_social_access_mode="approved_public_web"),
    ).scan()
    assert {
        event.raw_payload.get("x_handle") for event in events if "x.com" in event.source_url
    } == {
        "alex",
        "mira",
    }
    assert any("instagram.com" in url for url in crawler.urls)


async def test_social_scan_drops_generic_platform_handles_before_event_creation() -> None:
    crawler = FakeCrawler(rendered_text="Test Program @twitter @keyframes @alex")
    events = await SocialConnector(
        _registry(),
        crawler,
        runtime_settings=Settings(restricted_social_access_mode="approved_public_web"),
    ).scan()
    labels = {str(event.raw_payload.get("name", "")).casefold() for event in events}
    assert labels == {"alex"}


async def test_social_profile_investigation_supports_instagram() -> None:
    crawler = FakeCrawler(rendered_text="Founder at Test Program is pre-seed funded")
    events = await SocialConnector(
        _registry(),
        crawler,
        runtime_settings=Settings(restricted_social_access_mode="approved_public_web"),
    ).investigate(_trigger(instagram_handle="alex"))
    assert len(events) == 1
    assert events[0].confidence == 0.55
    assert "instagram.com/alex" in events[0].source_url
    assert (
        events[0].raw_payload["environmental_extraction"]["extraction_method_used"]
        == "regex_fallback"
    )


async def test_website_investigation_emits_public_canonical_event(tmp_path) -> None:
    crawler = FakeCrawler(rendered_text="Acme Labs builds public infrastructure")
    events = await WebsiteConnector(
        _registry(website_urls=["https://example.test/{query}"]), crawler
    ).investigate(_trigger(name="Acme Labs", program_id="test_program"))
    assert len(events) == 1
    assert events[0].source == Source.WEBSITE
    assert events[0].event_type == EventType.COMPANY_WEBSITE_FOUND
    assert events[0].source_url == "https://example.test/Acme+Labs"
    from venture_intelligence.pipeline.entity_resolution import EntityResolver
    from venture_intelligence.pipeline.evidence_builder import EvidenceBuilder
    from venture_intelligence.storage.db import Database

    db = Database(str(tmp_path / "website.sqlite"))
    canonical_id = EntityResolver(db).resolve(events[0])
    EvidenceBuilder(db).process(events[0], canonical_id)
    evidence = db.get_evidence_for_entity(canonical_id)
    assert any(item.claim == "Has a public company website" for item in evidence)


def test_social_handles_are_bounded_and_unique() -> None:
    assert SocialConnector._handles("@alex @alex @mira") == ["alex", "mira"]


async def test_discord_scan_never_promotes_widget_usernames() -> None:
    crawler = FakeCrawler(widget={"members": [{"username": "alex"}]})
    events = await DiscordConnector(_registry(["123"]), crawler).scan()
    assert events == []


async def test_discord_investigation_matches_public_member() -> None:
    crawler = FakeCrawler(widget={"members": [{"username": "alex"}]})
    events = await DiscordConnector(_registry(["123"]), crawler).investigate(_trigger())
    assert len(events) == 1
    assert events[0].entity_id == _trigger().entity_id
    assert events[0].entity_type == _trigger().entity_type
    assert events[0].confidence == 0.35
    assert events[0].metadata["public_widget"] is True
    assert events[0].metadata["identity_authority"] is False


class ProductHuntApiFixture:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict, dict[str, str]]] = []

    async def post_json_text(
        self,
        url: str,
        payload: dict,
        *,
        headers: dict[str, str],
        **_kwargs,
    ):
        self.calls.append((url, payload, headers))
        return CrawlResult(url, json.dumps(self.payload), 200)

    async def close(self) -> None:
        return None


def _company_trigger(**metadata: str) -> CanonicalEvent:
    return CanonicalEvent(
        event_type=EventType.COMPANY_LISTED,
        entity_type=EntityType.COMPANY,
        entity_id="test__acme-labs",
        source=Source.API,
        source_url="api://test",
        occurred_at=datetime.now(timezone.utc),
        raw_payload={"name": "Acme Labs", "program_id": "test_program"},
        metadata=metadata,
    )


async def test_product_hunt_official_api_is_credential_gated(monkeypatch) -> None:
    monkeypatch.delenv("PRODUCT_HUNT_API_ENABLED", raising=False)
    monkeypatch.delenv("PRODUCT_HUNT_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("PRODUCT_HUNT_KNOWN_POSITIVE_POST_ID", raising=False)

    crawler = ProductHuntApiFixture({})
    connector = ProductHuntConnector(
        _registry(),
        crawler,
        runtime_settings=Settings(_env_file=None),
    )

    assert connector.configured is False
    assert await connector.scan() == []
    assert await connector.investigate(_company_trigger()) == []
    assert crawler.calls == []


async def test_product_hunt_official_api_preserves_target_identity_and_provenance() -> None:
    crawler = ProductHuntApiFixture(
        {
            "data": {
                "post": {
                    "id": "42",
                    "name": "Acme Labs",
                    "slug": "acme-labs",
                    "tagline": "Infrastructure",
                    "description": "Public launch",
                    "url": "https://www.producthunt.com/posts/acme-labs",
                    "website": "https://acme.example",
                    "createdAt": "2026-01-01T00:00:00Z",
                    "featuredAt": None,
                    "makers": [{"name": "Alice Example", "username": "alice"}],
                    "topics": {"nodes": [{"name": "Developer Tools"}]},
                }
            }
        }
    )
    connector = ProductHuntConnector(
        _registry(),
        crawler,
        runtime_settings=Settings(
            _env_file=None,
            product_hunt_api_enabled=True,
            product_hunt_access_token="test-token",
        ),
    )
    events = await connector.investigate(_company_trigger(product_hunt_post_id="42"))
    assert len(events) == 1
    assert events[0].entity_id == "test__acme-labs"
    assert events[0].entity_type is EntityType.COMPANY
    assert events[0].metadata["accelerator_membership_authority"] is False
    assert events[0].raw_payload["product_hunt_post"]["makers"][0]["name"] == ("Alice Example")
    assert crawler.calls[0][0] == "https://api.producthunt.com/v2/api/graphql"
    assert crawler.calls[0][2]["Authorization"] == "Bearer test-token"


async def test_product_hunt_does_not_attach_a_mismatched_post_or_person() -> None:
    crawler = ProductHuntApiFixture({"data": {"post": {"id": "42", "name": "Different Company"}}})
    connector = ProductHuntConnector(
        _registry(),
        crawler,
        runtime_settings=Settings(
            _env_file=None,
            product_hunt_api_enabled=True,
            product_hunt_access_token="test-token",
        ),
    )
    assert await connector.investigate(_company_trigger(product_hunt_post_id="42")) == []
    assert await connector.investigate(_trigger(program_id="test_program")) == []


def test_factory_registers_social_and_discord() -> None:
    crawler = FakeCrawler()
    connectors = build_connectors(_registry(), crawler)
    sources = {connector.source_name for connector in connectors}
    assert {"social", "discord"}.issubset(sources)
    assert all(connector.crawler is crawler for connector in connectors)


async def test_extractor_returns_none_when_ollama_is_disabled() -> None:
    extractor = EnvironmentalExtractor(Settings(ollama_enabled=False))
    assert (
        await extractor.extract("Founder at Test Program", "blog", "https://example.test") is None
    )


async def test_extractor_rejects_malformed_ollama_json(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": '{"entity_name": null}'}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: FakeClient())
    extractor = EnvironmentalExtractor(Settings(ollama_enabled=True))
    assert (
        await extractor.extract("Founder at Test Program", "blog", "https://example.test") is None
    )


async def test_extractor_uses_explicit_regex_fallback() -> None:
    extractor = EnvironmentalExtractor(Settings(ollama_enabled=False))
    result = await extractor.extract_with_fallback(
        "Founder at Acme is pre-seed funded", "blog", "https://example.test"
    )
    assert result is not None and result.extraction_method_used == "regex_fallback"


def test_modelfile_uses_required_qwen_parameters() -> None:
    model_file = Path("ollama_modelfile/Modelfile").read_text(encoding="utf-8")
    for expected in (
        "FROM qwen2.5-coder:3b",
        "num_gpu 999",
        "num_ctx 4096",
        "num_predict 400",
        "temperature 0",
        "top_p 0.1",
    ):
        assert expected in model_file


def test_versioned_api_is_in_openapi() -> None:
    paths = app.openapi()["paths"]
    assert "/api/v1/programs" in paths
    assert "/api/v1/intel/gather" in paths
    assert "/api/v1/research" in paths


def test_legacy_graph_investigation_and_websocket_surfaces() -> None:
    with TestClient(app) as client:
        assert client.get("/api/graph/missing").status_code == 200
        assert client.get("/api/graph").status_code == 200
        assert client.get("/api/investigations/missing").status_code == 200
        assert client.get("/api/investigations").status_code == 200
        assert client.get("/api/events").status_code == 200
        assert client.get("/api/evaluation").status_code == 200
        analyst = client.post("/api/analyst/chat", json={"question": "What is configured?"})
        assert analyst.status_code == 200 and analyst.json()["answer"]
        with client.websocket_connect("/ws") as socket:
            assert socket.receive_json()["type"] == "connected"
            socket.send_text("ping")
            assert socket.receive_json() == {"type": "pong"}


def test_frontend_is_served() -> None:
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert '<div id="app"></div>' in response.text
    asset = re.search(r'src="(/frontend/dist/assets/[^"]+\.js)"', response.text)
    assert asset is not None and client.get(asset.group(1)).status_code == 200
    assert client.get("/frontend/favicon.svg").status_code == 200
    for retired in (
        "/legacy",
        "/frontend/legacy.html",
        "/frontend/src/main.js",
        "/frontend/src/api/client.js",
    ):
        assert client.get(retired, headers={"Accept": "application/json"}).status_code == 404
    shell = Path("frontend/index.html").read_text(encoding="utf-8")
    assert len(shell) < 2_000
    assert 'src="/src/main.tsx"' in shell


def test_frontend_live_evidence_contract_and_provenance_detail() -> None:
    research = Path("frontend/src/features/research/ResearchWorkspace.tsx").read_text(
        encoding="utf-8"
    )
    knowledge = Path("frontend/src/features/research/KnowledgeEvidence.tsx").read_text(
        encoding="utf-8"
    )
    graph = Path("frontend/src/features/graph/GraphPage.tsx").read_text(encoding="utf-8")
    scene = Path("frontend/src/features/graph/GraphScene.tsx").read_text(encoding="utf-8")
    assert "Insufficient evidence" in research
    assert "KnowledgeEvidence" in research and "DecisionDetails" in research
    assert "EventSource" in knowledge and "refetchInterval: 15000" in knowledge
    assert "evidence_by_category" in knowledge and "category_confidence" in knowledge
    assert "knowledge.updated" in knowledge and "stream.close()" in knowledge
    assert "graphQuery" in graph and "Collapse neighbors" in graph and "Provenance" in graph
    assert "@react-three/fiber" in scene and "OrbitControls" in scene and "Canvas" in scene
    assert not Path("frontend/src/pages/graph.js").exists()
    assert not Path("frontend/src/api/client.js").exists()
    assert "https://" not in graph
    navigation = Path("frontend/src/shell/navigation.ts").read_text(encoding="utf-8")
    for route in ("/inbox", "/discover", "/portfolio", "/research", "/system/sources"):
        assert route in navigation


async def test_specialized_public_events_keep_semantic_entity_types() -> None:
    registry = _registry()
    crawler = FakeCrawler("Acme Test Program public result")
    trigger = CanonicalEvent(
        event_type=EventType.COMPANY_LISTED,
        entity_type=EntityType.COMPANY,
        entity_id="yc__acme",
        source=Source.YC_WEBSITE,
        source_url="https://example.test/acme",
        occurred_at=datetime.now(timezone.utc),
        raw_payload={"name": "Acme"},
        metadata={"program_id": "test_program"},
    )
    github_crawler = FakeCrawler(
        json.dumps(
            {
                "items": [
                    {
                        "full_name": "acme/app",
                        "private": False,
                        "html_url": "https://github.com/acme/app",
                    }
                ]
            }
        )
    )
    github = await GitHubConnector(registry, github_crawler).investigate(trigger)
    arxiv = await ArxivConnector(registry, crawler).investigate(trigger)
    linkedin = await LinkedInConnector(registry, crawler).investigate(trigger)
    assert github and github[0].entity_type == EntityType.REPOSITORY
    assert arxiv and arxiv[0].entity_type == EntityType.PAPER
    assert linkedin == []


async def test_github_connector_uses_optional_token_without_exposing_it() -> None:
    crawler = FakeCrawler(
        json.dumps(
            {
                "items": [
                    {
                        "full_name": "acme/app",
                        "private": False,
                        "html_url": "https://github.com/acme/app",
                    }
                ]
            }
        )
    )
    runtime_settings = Settings(_env_file=None, github_token="test-token")
    trigger = CanonicalEvent(
        event_type=EventType.COMPANY_LISTED,
        entity_type=EntityType.COMPANY,
        entity_id="test__acme",
        source=Source.API,
        source_url="api://test",
        occurred_at=datetime.now(timezone.utc),
        raw_payload={"name": "Acme"},
        metadata={"program_id": "test_program"},
    )

    events = await GitHubConnector(
        _registry(), crawler, runtime_settings=runtime_settings
    ).investigate(trigger)

    assert events and events[0].entity_type is EntityType.REPOSITORY
    assert crawler.authorized_headers == [
        {
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer test-token",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    ]
    assert "test-token" not in repr(runtime_settings)


def test_generic_person_labels_are_rejected(tmp_path) -> None:
    event = CanonicalEvent(
        event_type=EventType.FOUNDER_DETECTED,
        entity_type=EntityType.PERSON,
        entity_id="linkedin__twitter",
        source=Source.LINKEDIN,
        source_url="https://linkedin.com/search",
        occurred_at=datetime.now(timezone.utc),
        raw_payload={"name": "Twitter Media"},
    )
    with pytest.raises(ValueError, match="generic person label"):
        EntityResolver(Database(str(tmp_path / "generic.db"))).resolve(event)


def test_legacy_generic_entities_have_safe_quarantine_path(tmp_path) -> None:
    db = Database(str(tmp_path / "cleanup.db"))
    with db._connect() as connection:
        connection.execute(
            "INSERT INTO companies "
            "(company_id, canonical_name, first_seen_at, updated_at) "
            "VALUES (?,?,?,?)",
            ("yc__twitter", "twitter", "2026-01-01", "2026-01-01"),
        )
    candidates = quarantine_generic_entities(db)
    assert candidates and candidates[0]["entity_id"] == "yc__twitter"
    quarantine_generic_entities(db, apply=True)
    with db._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM quarantined_entities").fetchone()[0] == 1


def test_generic_derived_cleanup_preserves_immutable_evidence(tmp_path) -> None:
    db = Database(str(tmp_path / "derived-cleanup.db"))
    with db._connect() as connection:
        now = "2026-01-01T00:00:00+00:00"
        connection.execute(
            "INSERT INTO graph_nodes VALUES (?,?,?,?,?,?)",
            ("yc__twitter", "Company", "twitter", "{}", now, now),
        )
        connection.execute(
            "INSERT INTO graph_nodes VALUES (?,?,?,?,?,?)",
            ("good__acme", "Company", "Acme", "{}", now, now),
        )
        connection.execute(
            """
            INSERT INTO graph_edges
              (edge_id, source_id, target_id, edge_type, confidence,
               observed_at, valid_from, valid_to, evidence_ids, properties)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            ("edge", "yc__twitter", "good__acme", "MENTIONS", 0.4, now, None, None, "[]", "{}"),
        )
        connection.execute(
            "INSERT INTO events "
            "(event_id,schema_version,event_type,entity_type,entity_id,source,"
            "source_url,occurred_at,observed_at,ingested_at,confidence,"
            "raw_payload,metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "raw-event",
                "1.0",
                "research_discovered",
                "company",
                "yc__twitter",
                "research_agent",
                "https://x",
                now,
                now,
                now,
                0.2,
                "{}",
                "{}",
            ),
        )
        connection.execute(
            "INSERT INTO observations VALUES (?,?,?,?,?,?,?,?)",
            ("obs", "raw-event", "yc__twitter", "research_agent", "keyword", "twitter", now, 0.2),
        )
        connection.commit()
    report = cleanup_generic_derived_state(db, apply=True)
    assert report["graph_nodes"] and report["graph_edges"] == 1
    with db._connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM graph_nodes WHERE node_id='yc__twitter'"
            ).fetchone()[0]
            == 0
        )
        assert connection.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0] == 0
        assert (
            connection.execute("SELECT COUNT(*) FROM events WHERE event_id='raw-event'").fetchone()[
                0
            ]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM observations WHERE observation_id='obs'"
            ).fetchone()[0]
            == 1
        )


async def test_analyst_qwen_provider_is_optional_and_json_native(monkeypatch) -> None:
    result = await AnalystAgent(Settings(analyst_enabled=False)).answer("What is known?")
    assert result.model == "local-boundary" and result.evidence_sufficient is False
    assert OllamaQwenProvider(Settings()).name == "qwen-ollama"
