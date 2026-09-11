from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from venture_intelligence.api.app import app
from venture_intelligence.config import settings
from venture_intelligence.knowledge import (
    ClaimStatus,
    KnowledgeClaim,
    KnowledgeCompiler,
    normalize_evidence,
)
from venture_intelligence.pipeline.email import EmailNotificationService, validate_email
from venture_intelligence.research.agent import ResearchAgent
from venture_intelligence.research.sources import SearchResponse, SearchResult
from venture_intelligence.shared.models import (
    ResearchSourceStatus,
    ResearchTarget,
    ResearchTargetType,
)


def _evidence(evidence_id: str = "ev-1", *, historical: bool = False) -> dict:
    return {
        "evidence_id": evidence_id,
        "url": "https://example.com/paper",
        "canonical_url": "https://example.com/paper",
        "title": "Public record",
        "source_domain": "web.archive.org" if historical else "example.com",
        "retrieved_at": "2026-08-17T00:00:00+00:00",
        "temporal_status": "historical" if historical else "current",
    }


def test_json_to_markdown_is_deterministic_and_incremental(tmp_path: Path):
    compiler = KnowledgeCompiler(tmp_path / "knowledge")
    page = compiler.compile_page(
        "company",
        "Acme Labs",
        {"identity": {"name": "Acme Labs"}},
        claims=[
            KnowledgeClaim(
                claim_id="c1", statement="Founded publicly", evidence_ids=["ev-1"], confidence=0.8
            )
        ],
        evidence=[_evidence()],
        confidence=0.8,
    )
    first_md = compiler.markdown_path("company", "Acme Labs").read_text()
    compiler.compile_page("company", "Acme Labs", evidence=[_evidence("ev-2")], confidence=0.9)
    second = compiler.load("company", "Acme Labs")
    assert second and len(second.evidence) == 2 and second.revisions
    assert first_md == compiler.render_markdown(page)


def test_historical_and_broken_reference_validation(tmp_path: Path):
    compiler = KnowledgeCompiler(tmp_path / "knowledge")
    compiler.compile_page(
        "founder",
        "Jane Doe",
        claims=[
            KnowledgeClaim(
                claim_id="historical",
                statement="Old role",
                status=ClaimStatus.HISTORICAL,
                evidence_ids=["ev-1"],
            ),
            KnowledgeClaim(claim_id="unsupported", statement="Unproven"),
        ],
        evidence=[_evidence(historical=True)],
    )
    report = compiler.validate()
    assert report["unsupported"] and report["valid"] is False


def test_generic_terms_are_rejected(tmp_path: Path):
    with pytest.raises(ValueError):
        KnowledgeCompiler(tmp_path / "knowledge").compile_page("company", "twitter")


def test_evidence_normalization_exposes_category_and_confidence_basis():
    item = normalize_evidence(
        {
            "evidence_id": "ev-1",
            "source_domain": "arxiv.org",
            "title": "Paper publication",
            "confidence": 0.8,
            "provenance": {"provider": "arxiv.py"},
        }
    )
    assert item["category"] == "technical_publication"
    assert item["confidence_pct"] == 80
    assert item["confidence_basis"]["source_tier"] == "first_party"


def test_knowledge_polling_metadata_tracks_revision(tmp_path: Path):
    compiler = KnowledgeCompiler(tmp_path / "knowledge")
    compiler.compile_page("company", "Acme Labs", evidence=[_evidence()])
    compiler.compile_page("company", "Acme Labs", evidence=[_evidence("ev-2")])
    page = compiler.find("Acme Labs")
    metadata = compiler.polling_metadata(page)
    assert metadata["status"] == "available"
    assert metadata["revision_count"] == 1
    assert metadata["revision_id"]
    assert metadata["etag"]


def test_email_validation_and_disabled_suppression(tmp_path: Path):
    assert validate_email("person@example.com")
    assert not validate_email("not-an-email")
    service = EmailNotificationService(tmp_path / "email.json")
    result = service.send("a1", "Subject", "Body")
    assert result["status"] == "suppressed"


def test_email_success_duplicate_and_test(tmp_path: Path):
    sent = []
    service = EmailNotificationService(tmp_path / "email.json", smtp_sender=sent.append)
    service.configure("person@example.com", preferences={"enabled": True})
    assert service.send("a1", "Subject", "Body")["status"] == "sent"
    assert service.send("a1", "Subject", "Body")["status"] == "suppressed"
    assert service.send_test()["status"] == "sent"
    data = json.loads((tmp_path / "email.json").read_text())
    assert any(item["status"] == "pending" for item in data["deliveries"])
    assert len(sent) == 2


def test_email_refused_recipient_is_never_recorded_as_sent(tmp_path: Path):
    service = EmailNotificationService(
        tmp_path / "email.json",
        smtp_sender=lambda _message: {"recipient@example.com": (550, b"rejected")},
        max_retries=0,
    )
    service.configure("recipient@example.com", preferences={"enabled": True})

    result = service.send("refused", "Subject", "Body")

    assert result["status"] == "failed"
    assert "refused" in result["error"].casefold()
    data = json.loads((tmp_path / "email.json").read_text())
    assert any(item["status"] == "pending" for item in data["deliveries"])
    assert all(item["status"] != "sent" for item in data["deliveries"])


def test_email_retry_exhaustion_is_logged(tmp_path: Path):
    def fail(_message):
        raise RuntimeError("offline")

    service = EmailNotificationService(
        tmp_path / "email.json", smtp_sender=fail, max_retries=1, backoff_seconds=0
    )
    service.configure("person@example.com", preferences={"enabled": True})
    result = service.send("a1", "Subject", "Body")
    assert result["status"] == "failed" and result["attempts"] == 2


def test_email_category_confidence_and_frequency_filters(tmp_path: Path):
    service = EmailNotificationService(tmp_path / "email.json", smtp_sender=lambda _: None)
    service.configure(
        "person@example.com",
        preferences={
            "enabled": True,
            "new_founder_discovery": False,
            "minimum_confidence": 0.8,
            "frequency": "daily",
        },
    )
    assert (
        service.send(
            "founder", "Subject", "Body", category="new_founder_discovery", confidence=0.99
        )["status"]
        == "suppressed"
    )
    assert service.send("weak", "Subject", "Body", confidence=0.5)["status"] == "suppressed"
    assert service.send("digest", "Subject", "Body", confidence=0.9)["status"] == "queued"


@pytest.mark.asyncio
async def test_research_run_compiles_incremental_knowledge(tmp_path: Path):
    class Provider:
        domain = "example.com"

        async def search(self, query: str, max_results: int) -> SearchResponse:
            return SearchResponse(
                query=query,
                status=ResearchSourceStatus.AVAILABLE,
                results=[
                    SearchResult(
                        url="https://example.com/a",
                        title="Acme fact",
                        snippet="public",
                        source_domain=self.domain,
                    )
                ],
                retrieved_at="2026-08-17T00:00:00+00:00",
            )

        async def close(self) -> None:
            return None

    compiler = KnowledgeCompiler(tmp_path / "knowledge")
    agent = ResearchAgent(Provider(), knowledge_compiler=compiler)
    run = await agent.run(ResearchTarget(name="Acme Labs", type=ResearchTargetType.COMPANY))
    await agent.close()
    assert run.evidence
    assert compiler.load("company", "Acme Labs") is None
    candidate = compiler.load("investigation", "candidate-acme-labs")
    assert candidate is not None
    assert candidate.identity["validation_state"] == "unverified"


def test_knowledge_and_email_api_surfaces(tmp_path: Path, monkeypatch):
    # The API must not exercise the user's durable notification settings while
    # running tests. Keep this endpoint contract test fully isolated.
    monkeypatch.setattr(settings, "email_config_path", str(tmp_path / "email.json"))
    with TestClient(app) as client:
        assert client.get("/api/knowledge/validate").status_code == 200
        assert client.get("/api/settings/email").status_code == 200
        assert (
            client.put(
                "/api/settings/email", json={"destination": "bad", "enabled": True}
            ).status_code
            == 422
        )
        response = client.put("/api/settings/email", json={"destination": None, "enabled": False})
        assert response.status_code == 200 and response.json()["destination"] is None
        assert client.post("/api/settings/email/test").status_code == 200


def test_knowledge_sse_and_polling_snapshot(tmp_path: Path, monkeypatch):
    knowledge_dir = tmp_path / "knowledge"
    KnowledgeCompiler(knowledge_dir).compile_page(
        "company", "Acme Labs", evidence=[_evidence()], confidence=0.8
    )
    monkeypatch.setattr(settings, "knowledge_dir", str(knowledge_dir))

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("venture_intelligence.api.app.asyncio.sleep", no_sleep)
    with TestClient(app) as client:
        response = client.get("/api/v1/knowledge/stream/Acme%20Labs")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "event: knowledge.updated" in response.text
        assert '"revision"' in response.text
        snapshot = client.get("/api/v1/knowledge/Acme%20Labs")
        assert snapshot.status_code == 200
        assert snapshot.json()["revision"]["status"] == "available"
