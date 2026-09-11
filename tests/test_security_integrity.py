"""Focused API security and JSON-integrity regression tests."""

import asyncio
import json
from datetime import datetime, timezone

import httpx
import venture_intelligence.api.app as api_app
from fastapi.testclient import TestClient
from venture_intelligence.config import settings
from venture_intelligence.crawler import StealthCrawler
from venture_intelligence.knowledge.compiler import KnowledgeCompiler
from venture_intelligence.shared.bus import EventBus
from venture_intelligence.shared.models import (
    CanonicalEvent,
    Company,
    EntityType,
    EventType,
    GraphNode,
    GraphNodeType,
    Source,
)


def _event(name: str, *, entity_id: str) -> CanonicalEvent:
    return CanonicalEvent(
        event_type=EventType.COMPANY_LISTED,
        entity_type=EntityType.COMPANY,
        entity_id=entity_id,
        source=Source.YC_WEBSITE,
        source_url="https://www.ycombinator.com/companies/acme",
        occurred_at=datetime.now(timezone.utc),
        confidence=0.97,
        raw_payload={"name": name, "slug": name.casefold().replace(" ", "-")},
    )


def test_company_evidence_api_excludes_generic_records_and_returns_json(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "api.db"))
    monkeypatch.setattr(settings, "knowledge_dir", str(tmp_path / "knowledge"))

    with TestClient(api_app.app) as client:
        db = api_app._db
        assert db is not None
        valid = Company(
            company_id="company__acme",
            canonical_name="Acme Labs",
            website="https://acme.test",
            identity_confidence=0.9,
        )
        generic = Company(company_id="company__twitter", canonical_name="twitter")
        db.write_company(valid)
        db.write_company(generic)
        db.write_entity_validation("company__acme", "company", evidence_count=1)
        # Simulate a legacy polluted row: the API boundary must still reject it.
        db.write_entity_validation("company__twitter", "company", evidence_count=1)
        db.write_event(_event("Acme Labs", entity_id="yc__acme"))
        db.write_event(_event("twitter", entity_id="yc__twitter"))

        response = client.get("/api/companies/evidence")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        payload = response.json()
        assert isinstance(payload, dict) and isinstance(payload["results"], list)
        assert [row["company"] for row in payload["results"]] == ["Acme Labs"]
        assert all(
            row["company"].casefold() not in {"twitter", "keyframes"} for row in payload["results"]
        )


def test_rejected_candidate_is_json_auditable_and_never_reviewed(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "api.db"))

    with TestClient(api_app.app) as client:
        db = api_app._db
        assert db is not None
        event = CanonicalEvent(
            event_type=EventType.COMPANY_LISTED,
            entity_type=EntityType.COMPANY,
            entity_id="search__twitter",
            source=Source.LINKEDIN,
            source_url="https://www.linkedin.com/search/results/companies/",
            occurred_at=datetime.now(timezone.utc),
            confidence=0.3,
            raw_payload={"name": "twitter", "query": "accelerator twitter"},
            metadata={"query": "accelerator twitter"},
        )
        assert EventBus(db).publish(event) is False

        rejection_response = client.get("/api/candidates/rejections")
        assert rejection_response.status_code == 200
        rejection = rejection_response.json()["rejections"][0]
        assert rejection["candidate_label"] == "twitter"
        assert rejection["reason"] == "generic_label"
        assert json.loads(rejection["raw_candidate"])["name"] == "twitter"
        assert json.loads(rejection["provenance"])["event_id"] == event.event_id
        assert client.get("/api/review-queue").json()["results"] == []


def test_knowledge_api_preserves_provenance_as_json(monkeypatch, tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "api.db"))
    monkeypatch.setattr(settings, "knowledge_dir", str(knowledge_dir))
    KnowledgeCompiler(knowledge_dir).compile_page(
        "company",
        "Acme Labs",
        payload={"name": "Acme Labs"},
        evidence=[
            {
                "evidence_id": "ev-acme",
                "url": "https://acme.test/about",
                "source_domain": "acme.test",
                "retrieved_at": "2026-08-19T00:00:00+00:00",
                "claim": "Public company website identifies Acme Labs",
            }
        ],
        provenance=[
            {
                "source": "yc_website",
                "url": "https://www.ycombinator.com/companies/acme",
                "retrieved_at": "2026-08-19T00:00:00+00:00",
            }
        ],
        confidence=0.9,
    )

    with TestClient(api_app.app) as client:
        response = client.get("/api/knowledge/company/acme-labs")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        payload = response.json()
        assert payload["provenance"][0]["source"] == "yc_website"
        assert payload["evidence"][0]["url"].startswith("https://")


def test_graph_api_filters_generic_labels_at_the_boundary(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "api.db"))
    with TestClient(api_app.app) as client:
        db = api_app._db
        assert db is not None
        db.write_company(Company(company_id="company__acme", canonical_name="Acme Labs"))
        db.write_company(Company(company_id="company__twitter", canonical_name="twitter"))
        db.write_graph_node(
            GraphNode(node_id="company__acme", node_type=GraphNodeType.COMPANY, label="Acme Labs")
        )
        db.write_entity_validation("company__acme", "company", evidence_count=1)
        db.write_graph_node(
            GraphNode(node_id="company__twitter", node_type=GraphNodeType.COMPANY, label="twitter")
        )
        db.write_entity_validation("company__twitter", "company", evidence_count=1)
        response = client.get("/api/graph")
        assert response.status_code == 200
        labels = {node["label"].casefold() for node in response.json()["nodes"]}
        assert "twitter" not in labels
        assert "acme labs" in labels


def test_crawler_rejects_private_targets_before_network_access():
    crawler = StealthCrawler()
    try:
        for url in (
            "http://127.0.0.1/admin",
            "http://localhost:8000/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
        ):
            result = asyncio.run(crawler.fetch_text(url))
            assert not result.ok
            assert (
                "private" in (result.error or "").casefold()
                or "local" in (result.error or "").casefold()
            )
    finally:
        asyncio.run(crawler.close())


def test_crawler_rejects_unsafe_redirects_and_follows_safe_redirects():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/unsafe":
            return httpx.Response(
                302, headers={"location": "http://127.0.0.1/private"}, request=request
            )
        if request.url.path == "/safe":
            return httpx.Response(307, headers={"location": "/final"}, request=request)
        return httpx.Response(200, text="public", request=request)

    crawler = StealthCrawler()
    crawler._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )
    try:
        unsafe = asyncio.run(crawler.fetch_text("https://example.test/unsafe"))
        assert not unsafe.ok and "private" in (unsafe.error or "").casefold()
        safe = asyncio.run(crawler.fetch_text("https://example.test/safe"))
        assert safe.ok and safe.text == "public"
        assert calls == [
            "https://example.test/unsafe",
            "https://example.test/safe",
            "https://example.test/final",
        ]
    finally:
        asyncio.run(crawler.close())


def test_sse_headers_and_frame_budget(monkeypatch, tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    KnowledgeCompiler(knowledge_dir).compile_page(
        "company", "Acme Labs", payload={"name": "Acme Labs"}, confidence=0.8
    )
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "api.db"))
    monkeypatch.setattr(settings, "knowledge_dir", str(knowledge_dir))

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("venture_intelligence.api.app.asyncio.sleep", no_sleep)
    with TestClient(api_app.app) as client:
        response = client.get("/api/v1/knowledge/stream/Acme%20Labs")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-accel-buffering"] == "no"
        assert response.text.count("event: ") <= 12
        for frame in response.text.split("\n\n"):
            assert len(frame.encode("utf-8")) <= 256_000
