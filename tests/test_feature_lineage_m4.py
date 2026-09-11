"""Milestone 4 deterministic feature and intelligence-lineage regressions."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from venture_intelligence.api.app import app
from venture_intelligence.config import settings
from venture_intelligence.pipeline.entity_resolution import EntityResolver
from venture_intelligence.pipeline.evidence_builder import EvidenceBuilder
from venture_intelligence.pipeline.feature_worker import FeatureWorker
from venture_intelligence.pipeline.ranking_worker import RankingWorker
from venture_intelligence.pipeline.signal_worker import SignalWorker
from venture_intelligence.replay.models import ReplayRequest
from venture_intelligence.replay.service import ReplayService
from venture_intelligence.shared.models import (
    CanonicalEvent,
    EntityType,
    EventType,
    Feature,
    SignalType,
    Source,
)
from venture_intelligence.storage.db import Database

UTC = timezone.utc
CUTOFF = datetime(2026, 2, 1, 12, tzinfo=UTC)


def _event(
    event_id: str,
    *,
    name: str = "Acme Labs",
    source: Source = Source.YC_WEBSITE,
    event_type: EventType = EventType.COMPANY_LISTED,
    observed_at: datetime = CUTOFF,
    raw_payload: dict | None = None,
    metadata: dict | None = None,
) -> CanonicalEvent:
    slug = name.casefold().replace(" ", "-")
    return CanonicalEvent(
        event_id=event_id,
        event_type=event_type,
        entity_type=(
            EntityType.REPOSITORY if event_type is EventType.REPO_CREATED else EntityType.COMPANY
        ),
        entity_id=f"{source.value}__{slug}",
        source=source,
        source_url=(
            f"https://github.com/{slug}/product"
            if source is Source.GITHUB
            else f"https://example.com/{slug}"
        ),
        occurred_at=observed_at - timedelta(days=1),
        observed_at=observed_at,
        ingested_at=observed_at,
        confidence=0.92,
        raw_payload=raw_payload or {"name": name, "slug": slug},
        metadata=metadata or {},
    )


def _project(database: Database, event: CanonicalEvent) -> str:
    database.write_event(event)
    entity_id = EntityResolver(database).resolve(event)
    EvidenceBuilder(database).process(event, entity_id)
    return entity_id


def test_feature_computation_is_versioned_deterministic_and_lineaged(
    tmp_path: Path,
) -> None:
    database = Database(str(tmp_path / "feature.db"))
    entity_id = _project(
        database,
        _event(
            "repo-acme",
            source=Source.GITHUB,
            event_type=EventType.REPO_CREATED,
            raw_payload={"name": "Acme Labs", "owner": "acme", "repo": "core", "stars": 144},
        ),
    )
    worker = FeatureWorker(database)

    first = worker.process_entity(entity_id, as_of=CUTOFF)
    second = worker.process_entity(entity_id, as_of=CUTOFF)
    stars = next(item for item in first if item.name == "github_stars")

    assert [item.model_dump() for item in first] == [item.model_dump() for item in second]
    assert stars.value == 144
    assert stars.feature_version == "m4-feature-1"
    assert stars.formula == "sum(repository_star_observation_values)"
    assert stars.evidence_ids and stars.observation_ids and stars.input_digest


def test_features_and_signals_respect_observed_at_cutoff(tmp_path: Path) -> None:
    database = Database(str(tmp_path / "historical.db"))
    entity_id = _project(database, _event("listed", observed_at=CUTOFF - timedelta(days=2)))
    future = _event(
        "future-repo",
        source=Source.GITHUB,
        event_type=EventType.REPO_CREATED,
        observed_at=CUTOFF + timedelta(seconds=1),
        raw_payload={"name": "Acme Labs", "owner": "acme", "repo": "future", "stars": 900},
    )
    database.write_event(future)
    EvidenceBuilder(database).process(future, entity_id)

    features = FeatureWorker(database).process_entity(entity_id, as_of=CUTOFF)
    signals = SignalWorker(database).process_entity(entity_id, as_of=CUTOFF)

    assert "github_stars" not in {item.name for item in features}
    assert SignalType.OPEN_SOURCE_LEADER not in {item.signal_type for item in signals}
    assert all(item.as_of <= CUTOFF for item in features)


def test_repository_features_are_entity_scoped_not_global(tmp_path: Path) -> None:
    database = Database(str(tmp_path / "scope.db"))
    first_id = _project(
        database,
        _event(
            "repo-first",
            name="First Labs",
            source=Source.GITHUB,
            event_type=EventType.REPO_CREATED,
            raw_payload={"name": "First Labs", "owner": "first", "repo": "core", "stars": 10},
        ),
    )
    second_id = _project(
        database,
        _event(
            "repo-second",
            name="Second Labs",
            source=Source.GITHUB,
            event_type=EventType.REPO_CREATED,
            raw_payload={"name": "Second Labs", "owner": "second", "repo": "core", "stars": 5000},
        ),
    )

    first = {
        item.name: item for item in FeatureWorker(database).process_entity(first_id, as_of=CUTOFF)
    }
    second = {
        item.name: item for item in FeatureWorker(database).process_entity(second_id, as_of=CUTOFF)
    }

    assert first["github_stars"].value == 10
    assert second["github_stars"].value == 5000


def test_program_feature_is_generic_and_preserves_program_metadata(tmp_path: Path) -> None:
    database = Database(str(tmp_path / "program.db"))
    entity_id = _project(
        database,
        _event(
            "speedrun-company",
            name="Nova Engine",
            source=Source.DIRECTORY,
            metadata={"program_id": "speedrun_current", "batch_id": "SR007"},
        ),
    )
    features = FeatureWorker(database).process_entity(entity_id, as_of=CUTOFF)
    signals = SignalWorker(database).process_entity(entity_id, as_of=CUTOFF)

    membership = next(item for item in features if item.name == "program_membership_confidence")
    signal = next(item for item in signals if item.signal_type is SignalType.PROGRAM_AFFINITY)
    assert membership.program_id == "speedrun_current"
    assert membership.batch_id == "SR007"
    assert signal.feature_ids == [membership.feature_id]
    assert "accelerator or program" in signal.explanation
    assert "YC F26" not in signal.explanation


def test_complete_lineage_and_typed_api_are_integrity_gated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "api.db"
    monkeypatch.setattr(settings, "db_path", str(path))
    database = Database(str(path))
    entity_id = _project(database, _event("listed-api"))
    SignalWorker(database).process_entity(entity_id, as_of=CUTOFF)
    RankingWorker(database).rank_all(ranked_at=CUTOFF)

    with TestClient(app) as client:
        listing = client.get(
            "/api/v1/intelligence/rankings",
            params={"as_of": CUTOFF.isoformat()},
        )
        detail = client.get(
            f"/api/v1/intelligence/entities/{entity_id}",
            params={"as_of": CUTOFF.isoformat()},
        )
        rejected = client.get("/api/v1/intelligence/entities/yc__twitter")
        founder_route = client.get(f"/founders/{entity_id}", headers={"Accept": "text/html"})
        company_route = client.get(f"/companies/{entity_id}", headers={"Accept": "text/html"})
        legacy_json = client.get("/api/founders", headers={"Accept": "application/json"})
        openapi = client.get("/openapi.json").json()

    assert listing.status_code == 200
    assert listing.json()["items"][0]["entity"]["validation_status"] == "validated"
    assert detail.status_code == 200
    payload = detail.json()
    relations = {item["relation"] for item in payload["lineage"]}
    assert {"supports", "computes", "normalizes", "scores"} <= relations
    assert payload["ranking"]["feature_ids"]
    assert payload["ranking"]["signal_ids"]
    assert rejected.status_code == 404
    assert founder_route.status_code == 200
    assert founder_route.headers["content-type"].startswith("text/html")
    assert company_route.status_code == 200
    assert company_route.headers["content-type"].startswith("text/html")
    assert legacy_json.status_code == 200
    assert legacy_json.headers["content-type"].startswith("application/json")
    assert openapi["paths"]["/api/v1/intelligence/entities/{entity_id}"]["get"]


def test_replay_snapshot_contains_deterministic_feature_lineage(tmp_path: Path) -> None:
    database = Database(str(tmp_path / "replay.db"))
    database.write_event(_event("replay-company", observed_at=CUTOFF - timedelta(days=1)))
    service = ReplayService(database)

    first = service.run(ReplayRequest(cutoff=CUTOFF))
    second = service.run(ReplayRequest(cutoff=CUTOFF))

    assert first.snapshot.features
    assert first.snapshot.features == second.snapshot.features
    assert first.projection_digest == second.projection_digest
    assert first.counts.features == len(first.snapshot.features)
    assert first.schema_metadata["feature_version"] == "m4-feature-1"


def test_pre_m4_feature_table_migrates_without_losing_version_history(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-feature.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE features (
                feature_id TEXT PRIMARY KEY,
                entity_id TEXT NOT NULL,
                name TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT,
                source TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                confidence REAL NOT NULL,
                UNIQUE(entity_id, name)
            )
            """
        )
        connection.execute(
            "INSERT INTO features VALUES (?,?,?,?,?,?,?,?)",
            (
                "legacy",
                "company-acme",
                "github_stars",
                10,
                "count",
                "github",
                CUTOFF.isoformat(),
                0.8,
            ),
        )

    database = Database(str(path))
    database.write_feature(
        Feature(
            feature_id="m4-new",
            entity_id="company-acme",
            name="github_stars",
            value=20,
            unit="count",
            source=Source.GITHUB,
            observed_at=CUTOFF + timedelta(days=1),
            as_of=CUTOFF + timedelta(days=1),
            computed_at=CUTOFF + timedelta(days=1),
            feature_version="m4-feature-1",
            confidence=0.9,
        )
    )

    features = database.get_features_for_entity("company-acme")
    assert {item.feature_id for item in features} == {"legacy", "m4-new"}
    assert {item.feature_version for item in features} == {"legacy-1", "m4-feature-1"}
