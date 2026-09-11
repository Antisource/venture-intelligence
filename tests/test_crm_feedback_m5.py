"""Milestone 5 CRM feedback, lifecycle, replay, and API regressions."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from venture_intelligence.api.app import app
from venture_intelligence.config import settings
from venture_intelligence.crm.workflow import CRMWorkflow
from venture_intelligence.decisions.service import DecisionService
from venture_intelligence.pipeline.entity_resolution import EntityResolver
from venture_intelligence.pipeline.evidence_builder import EvidenceBuilder
from venture_intelligence.pipeline.notification import NotificationWorker
from venture_intelligence.pipeline.ranking_worker import RankingWorker
from venture_intelligence.pipeline.recommendation_worker import RecommendationWorker
from venture_intelligence.pipeline.signal_worker import SignalWorker
from venture_intelligence.replay.models import ReplayRequest
from venture_intelligence.replay.service import ReplayService
from venture_intelligence.shared.models import (
    Alert,
    CanonicalEvent,
    CRMActionType,
    CRMFeedbackKind,
    CRMStage,
    EntityType,
    EventType,
    Recommendation,
    RecommendationAction,
    SignalType,
    Source,
)
from venture_intelligence.storage.db import Database

UTC = timezone.utc
CUTOFF = datetime(2026, 4, 10, 12, tzinfo=UTC)


def _event(*, observed_at: datetime = CUTOFF - timedelta(days=5)) -> CanonicalEvent:
    return CanonicalEvent(
        event_id="m5-acme-event",
        event_type=EventType.COMPANY_LISTED,
        entity_type=EntityType.COMPANY,
        entity_id="directory__acme-labs",
        source=Source.DIRECTORY,
        source_url="https://example.com/acme-labs",
        occurred_at=observed_at - timedelta(days=1),
        observed_at=observed_at,
        ingested_at=observed_at,
        confidence=0.94,
        raw_payload={"name": "Acme Labs", "slug": "acme-labs"},
        metadata={"program_id": "accelerator-a", "batch_id": "A1"},
    )


def _project(database: Database) -> str:
    event = _event()
    database.write_event(event)
    entity_id = EntityResolver(database).resolve(event)
    EvidenceBuilder(database).process(event, entity_id)
    SignalWorker(database).process_entity(entity_id, as_of=CUTOFF)
    RankingWorker(database).rank_all(ranked_at=CUTOFF)
    RecommendationWorker(database).generate_all()
    return entity_id


def test_typed_action_is_append_only_idempotent_and_fully_lineaged(tmp_path: Path) -> None:
    database = Database(str(tmp_path / "feedback.db"))
    entity_id = _project(database)
    service = DecisionService(database)

    first = service.apply_action(
        entity_id,
        CRMActionType.INTERESTED,
        partner="partner@example.com",
        note="Strong founder-market fit",
        provenance={"request_id": "request-1"},
        action_id="request-1",
    )
    second = service.apply_action(
        entity_id,
        CRMActionType.INTERESTED,
        partner="partner@example.com",
        note="Strong founder-market fit",
        provenance={"request_id": "request-1"},
        action_id="request-1",
    )

    assert first.action == second.action
    assert first.action.feedback_kind is CRMFeedbackKind.HUMAN_JUDGMENT
    assert first.action.provenance["authoritative_ground_truth"] is False
    assert len(database.get_crm_events(entity_id)) == 1
    feature = next(
        item
        for item in first.decision.feedback_features
        if item.name == "human_feedback_positive_count"
    )
    signal = next(
        item
        for item in first.decision.feedback_signals
        if item.signal_type is SignalType.HUMAN_FEEDBACK_POSITIVE
    )
    ranking = first.decision.intelligence.ranking
    recommendation = first.decision.intelligence.recommendation
    assert feature.crm_action_ids == ["request-1"]
    assert feature.parameters["authoritative_ground_truth"] is False
    assert signal.crm_action_ids == ["request-1"]
    assert ranking and "request-1" in ranking.crm_action_ids
    assert recommendation and "request-1" in recommendation.crm_action_ids
    assert any(
        edge.from_type == "crm_action"
        and edge.from_id == "request-1"
        and edge.to_id == feature.feature_id
        for edge in first.decision.intelligence.lineage
    )


def test_feedback_is_bounded_and_realized_outcome_is_not_ground_truth(tmp_path: Path) -> None:
    database = Database(str(tmp_path / "bounded.db"))
    entity_id = _project(database)
    base = database.get_ranking_for_entity(entity_id)
    service = DecisionService(database)
    interested = service.apply_action(
        entity_id, CRMActionType.INTERESTED, partner="A", action_id="interested"
    )
    positive = interested.decision.intelligence.ranking
    passed = service.apply_action(
        entity_id,
        CRMActionType.PASSED,
        partner="B",
        note="Not a current fit",
        to_stage=CRMStage.PASSED,
        action_id="passed",
    )
    negative = passed.decision.intelligence.ranking
    evidence_before = len(database.get_evidence_for_entity(entity_id))
    invested = service.apply_action(
        entity_id,
        CRMActionType.INVESTED,
        partner="C",
        note="Externally verified transaction",
        action_id="invested",
    )

    assert base and positive and negative
    assert positive.composite_score > base.composite_score
    assert negative.composite_score < positive.composite_score
    assert invested.action.feedback_kind is CRMFeedbackKind.REALIZED_EXTERNAL_OUTCOME
    outcome = next(
        item
        for item in invested.decision.feedback_features
        if item.name == "realized_outcome_count"
    )
    assert outcome.parameters["authoritative_ground_truth"] is False
    assert len(database.get_evidence_for_entity(entity_id)) == evidence_before


def test_recommendation_and_alert_lifecycles_are_append_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(str(tmp_path / "lifecycle.db"))
    entity_id = _project(database)
    original = database.get_recommendation_for_entity(entity_id)
    assert original is not None

    DecisionService(database).apply_action(
        entity_id,
        CRMActionType.INTERESTED,
        partner="Partner",
        action_id="accept-rec",
    )
    original_statuses = {
        item.status.value
        for item in database.get_recommendation_lifecycle(original.recommendation_id)
    }
    assert {"created", "accepted", "superseded"} <= original_statuses

    ranking = database.get_ranking_for_entity(entity_id)
    assert ranking is not None
    contact = Recommendation(
        recommendation_id="manual-contact",
        entity_id=entity_id,
        action=RecommendationAction.CONTACT,
        reasoning="Deterministic lifecycle test",
        evidence_ids=ranking.evidence_ids,
        ranking_id=ranking.ranking_id,
        signal_ids=ranking.signal_ids,
        feature_ids=ranking.feature_ids,
        crm_action_ids=ranking.crm_action_ids,
        confidence=0.95,
        created_at=datetime.now(UTC),
    )
    database.write_recommendation(contact)
    monkeypatch.setattr(settings, "slack_webhook_url", None)
    worker = NotificationWorker(database)
    assert worker.dispatch_pending() >= 1
    alert_id = str(
        uuid.uuid5(
            uuid.UUID("f5fa6ed2-c9a5-5e73-8f62-bc3ee25aef21"),
            contact.recommendation_id,
        )
    )
    alert = database.get_alert(alert_id)
    assert alert is not None
    statuses = [item.status.value for item in database.get_alert_lifecycle(alert.alert_id)]
    assert statuses == ["created", "dispatching", "delivered"]


def test_replay_uses_only_historically_eligible_crm_actions(tmp_path: Path) -> None:
    database = Database(str(tmp_path / "replay-feedback.db"))
    entity_id = _project(database)
    workflow = CRMWorkflow(database)
    workflow.record_action(
        entity_id,
        CRMActionType.INTERESTED,
        partner="Early",
        occurred_at=CUTOFF - timedelta(days=2),
        observed_at=CUTOFF - timedelta(days=2),
        action_id="early-action",
    )
    workflow.record_action(
        entity_id,
        CRMActionType.PASSED,
        partner="Future",
        to_stage=CRMStage.PASSED,
        occurred_at=CUTOFF + timedelta(days=2),
        observed_at=CUTOFF + timedelta(days=2),
        action_id="future-action",
    )
    with database._connect() as connection:
        live_before = connection.execute("SELECT COUNT(*) FROM crm_events").fetchone()[0]

    first = ReplayService(database).run(ReplayRequest(cutoff=CUTOFF))
    second = ReplayService(database).run(ReplayRequest(cutoff=CUTOFF))

    assert first.selected_crm_action_ids == ["early-action"]
    assert first.counts.eligible_crm_actions == 1
    assert first.counts.replayed_crm_actions == 1
    assert [item["action"] for item in first.snapshot.crm_actions] == ["interested"]
    assert any(
        item["name"] == "human_feedback_positive_count"
        for item in first.snapshot.features
    )
    assert not any(
        item["name"] == "human_feedback_negative_count"
        for item in first.snapshot.features
    )
    assert first.projection_digest == second.projection_digest
    assert first.counts.crm_records == 0
    with database._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM crm_events").fetchone()[0] == live_before


def test_typed_decision_api_is_integrity_gated(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "api.db"
    monkeypatch.setattr(settings, "db_path", str(path))
    database = Database(str(path))
    entity_id = _project(database)
    recommendation = database.get_recommendation_for_entity(entity_id)
    assert recommendation is not None
    database.write_alert(
        Alert(
            alert_id="api-alert",
            recommendation_id=recommendation.recommendation_id,
            entity_id=entity_id,
            entity_name="Acme Labs",
            action=recommendation.action,
            reasoning=recommendation.reasoning,
            confidence=recommendation.confidence,
            evidence_summary=["Validated accelerator identity"],
            source_urls=["https://example.com/acme-labs"],
            crm_action_ids=recommendation.crm_action_ids,
        )
    )

    with TestClient(app) as client:
        board = client.get("/api/v1/decisions")
        legacy_board = client.get("/api/crm")
        crm_deep_link = client.get(
            "/crm", headers={"Accept": "text/html"}, follow_redirects=False
        )
        action = client.post(
            f"/api/v1/decisions/entities/{entity_id}/actions",
            json={
                "action": "watchlisted",
                "partner": "Reviewer",
                "note": "Revisit after launch",
                "action_id": "api-watchlist",
            },
        )
        detail = client.get(f"/api/v1/decisions/entities/{entity_id}")
        recommendation_lifecycle = client.post(
            "/api/v1/decisions/recommendations/"
            f"{recommendation.recommendation_id}/lifecycle",
            json={"status": "accepted", "actor": "Reviewer"},
        )
        alert_lifecycle = client.post(
            "/api/v1/decisions/alerts/api-alert/lifecycle",
            json={"status": "acknowledged", "actor": "Reviewer"},
        )
        rejected = client.post(
            "/api/v1/decisions/entities/github__twitter/actions",
            json={"action": "interested", "partner": "Reviewer"},
        )
        openapi = client.get("/openapi.json").json()

    assert board.status_code == 200
    assert board.json()["schema_version"] == "5.0"
    assert legacy_board.status_code == 200
    assert crm_deep_link.status_code == 307
    assert crm_deep_link.headers["location"] == "/#/crm"
    assert action.status_code == 200
    assert action.json()["action"]["provenance"]["authoritative_ground_truth"] is False
    assert detail.status_code == 200
    assert detail.json()["actions"][0]["crm_event_id"] == "api-watchlist"
    assert recommendation_lifecycle.status_code == 200
    assert recommendation_lifecycle.json()["status"] == "accepted"
    assert alert_lifecycle.status_code == 200
    assert alert_lifecycle.json()["status"] == "acknowledged"
    assert rejected.status_code == 404
    assert openapi["paths"]["/api/v1/decisions/entities/{entity_id}/actions"]["post"]


def test_pre_m5_database_migrates_without_losing_crm_history(tmp_path: Path) -> None:
    path = tmp_path / "pre-m5.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE crm_events (
                crm_event_id TEXT PRIMARY KEY, entity_id TEXT NOT NULL,
                action TEXT NOT NULL, from_stage TEXT NOT NULL,
                to_stage TEXT NOT NULL, note TEXT, partner TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO crm_events VALUES (?,?,?,?,?,?,?,?)",
            (
                "legacy-action", "company-acme", "stage_advanced",
                "discovery", "qualification", "Reviewed", "Partner",
                CUTOFF.isoformat(),
            ),
        )

    database = Database(str(path))
    action = database.get_crm_event("legacy-action")
    with database._connect() as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(crm_events)")
        }
        migrations = {
            row["version"] for row in connection.execute("SELECT version FROM schema_migrations")
        }

    assert action is not None
    assert action.action is CRMActionType.STAGE_CHANGED
    assert action.observed_at == CUTOFF
    assert {"feedback_kind", "provenance", "observed_at", "input_digest"} <= columns
    assert "0004_crm_feedback" in migrations
