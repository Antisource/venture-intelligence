"""Milestone 6 program generalization and reconciliation invariants."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from venture_intelligence.api.app import app
from venture_intelligence.config import settings
from venture_intelligence.intelligence.service import IntelligenceService
from venture_intelligence.pipeline.entity_resolution import EntityResolver
from venture_intelligence.pipeline.evidence_builder import EvidenceBuilder
from venture_intelligence.pipeline.graph import GraphBuilder
from venture_intelligence.pipeline.ranking_worker import RankingWorker
from venture_intelligence.pipeline.signal_worker import SignalWorker
from venture_intelligence.programs import get_program_registry
from venture_intelligence.replay.models import ReplayRequest
from venture_intelligence.replay.service import ReplayService
from venture_intelligence.shared.models import (
    CanonicalEvent,
    Company,
    EntityType,
    EventType,
    GraphNode,
    GraphNodeType,
    SignalType,
    Source,
)
from venture_intelligence.storage.cleanup import reconcile_polluted_entities
from venture_intelligence.storage.db import Database

NOW = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)


def _listing(name: str, program_id: str, batch: str, prefix: str) -> CanonicalEvent:
    return CanonicalEvent(
        event_type=EventType.COMPANY_LISTED,
        entity_type=EntityType.COMPANY,
        entity_id=f"{prefix}__{name.casefold().replace(' ', '-')}",
        source=Source.DIRECTORY,
        source_url=f"https://example.test/{program_id}/{name}",
        occurred_at=NOW,
        observed_at=NOW,
        ingested_at=NOW,
        confidence=0.92,
        raw_payload={"name": name, "batch": batch},
        metadata={"program_id": program_id, "batch_id": batch},
    )


def _project(database: Database, event: CanonicalEvent) -> str:
    resolver = EntityResolver(database)
    entity_id = resolver.resolve(event)
    EvidenceBuilder(database).process(event, entity_id)
    GraphBuilder(database).process(event, entity_id)
    SignalWorker(database).process_entity(entity_id, as_of=NOW)
    return entity_id


def test_registry_drives_yc_and_non_yc_domain_projection(tmp_path: Path) -> None:
    database = Database(str(tmp_path / "programs.db"))
    registry = get_program_registry()
    yc = registry.get("yc_current")
    speedrun = registry.get("speedrun_current")
    assert yc is not None and speedrun is not None

    yc_id = _project(database, _listing("YC Valid Labs", yc.id, yc.batch, yc.entity_id_prefix))
    speedrun_id = _project(
        database,
        _listing("Speedrun Valid Labs", speedrun.id, speedrun.batch, speedrun.entity_id_prefix),
    )

    yc_company = database.get_company(yc_id)
    speedrun_company = database.get_company(speedrun_id)
    assert yc_company and yc_company.batch == yc.batch
    assert speedrun_company and speedrun_company.batch == speedrun.batch
    assert yc_company.properties["program_id"] == yc.id
    assert speedrun_company.properties["program_id"] == speedrun.id
    assert all(
        signal.signal_type is SignalType.PROGRAM_AFFINITY
        for entity_id in (yc_id, speedrun_id)
        for signal in database.get_signals_for_entity(entity_id)
        if signal.signal_type in {SignalType.PROGRAM_AFFINITY, SignalType.YC_LIKELIHOOD}
    )
    with database._connect() as connection:
        labels = {
            row[0]
            for row in connection.execute(
                "SELECT label FROM graph_nodes WHERE node_type='Accelerator'"
            )
        }
    assert labels == {yc.name, speedrun.name}
    RankingWorker(database).rank_all(ranked_at=NOW)
    yc_rankings = IntelligenceService(database).rankings(program_id=yc.id)
    speedrun_rankings = IntelligenceService(database).rankings(program_id=speedrun.id)
    assert [item.entity.entity_id for item in yc_rankings.items] == [yc_id]
    assert [item.entity.entity_id for item in speedrun_rankings.items] == [speedrun_id]


def test_polluted_reconciliation_is_dry_run_idempotent_and_preserves_raw_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "polluted.db"
    database = Database(str(path))
    polluted_id = "github__twitter"
    database.write_company(Company(company_id=polluted_id, canonical_name="Twitter"))
    database.write_entity_validation(polluted_id, "company", evidence_count=1)
    database.write_graph_node(
        GraphNode(node_id=polluted_id, node_type=GraphNodeType.COMPANY, label="Twitter")
    )
    raw_event = CanonicalEvent(
        event_type=EventType.COMPANY_LISTED,
        entity_type=EntityType.COMPANY,
        entity_id=polluted_id,
        source=Source.GITHUB,
        source_url="https://github.com/search?q=twitter",
        occurred_at=NOW,
        observed_at=NOW,
        ingested_at=NOW,
        raw_payload={"name": "Twitter"},
    )
    database.write_event(raw_event)
    with database._connect() as connection:
        connection.execute(
            """INSERT INTO rankings
               (ranking_id, entity_id, rank, composite_score, dimensions,
                hypothesis_ids, evidence_ids, signal_ids, feature_ids,
                crm_action_ids, ranking_version, input_digest, reasoning,
                confidence, ranked_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "polluted-ranking",
                polluted_id,
                1,
                0.99,
                "[]",
                "[]",
                "[]",
                "[]",
                "[]",
                "[]",
                "legacy-1",
                "polluted",
                "legacy pollution",
                0.99,
                NOW.isoformat(),
            ),
        )
        connection.execute(
            """INSERT INTO crm_records
               (crm_id, entity_id, stage, notes, decision, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                "polluted-crm",
                polluted_id,
                "discovery",
                "[]",
                None,
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        connection.commit()

    monkeypatch.setattr(settings, "db_path", str(path))
    with TestClient(app) as client:
        assert client.get("/api/v1/search", params={"q": "Twitter"}).json() == []
        assert client.get("/api/v1/intelligence/rankings").json()["items"] == []
        assert client.get("/api/review-queue").json()["results"] == []
        assert client.get("/api/crm").json()["discovery"] == []
        assert client.get("/api/graph").json()["nodes"] == []
        assert client.get(f"/api/v1/knowledge/{polluted_id}").status_code == 404

    dry_run = reconcile_polluted_entities(database)
    assert dry_run["mode"] == "dry_run"
    assert {item["entity_id"] for item in dry_run["canonical_entities"]} == {polluted_id}
    assert database.get_company(polluted_id) is not None

    applied = reconcile_polluted_entities(database, apply=True)
    assert applied["mode"] == "apply"
    assert applied["derived_rows"]["rankings"] == 1
    assert applied["derived_rows"]["crm_records"] == 1
    assert database.get_company(polluted_id) is None
    with database._connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_id=?", (raw_event.event_id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM quarantined_entities WHERE entity_id=?", (polluted_id,)
            ).fetchone()[0]
            == 1
        )
    assert not reconcile_polluted_entities(database)["canonical_entities"]

    replay = ReplayService(database).run(ReplayRequest(cutoff=NOW))
    assert polluted_id not in {item["entity_id"] for item in replay.snapshot.entities}
    assert polluted_id not in {item["entity_id"] for item in replay.snapshot.rankings}


def test_port_8000_root_is_react_and_legacy_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "serving.db"))
    with TestClient(app) as client:
        root = client.get("/")
        legacy = client.get("/legacy")
    assert root.status_code == 200
    assert '<div id="app"></div>' in root.text
    assert "/assets/" in root.text
    assert legacy.status_code == 404
    assert not Path("frontend/src/main.js").exists()


@pytest.mark.parametrize("path", [
    "/research?candidate=public-lead&view=evidence",
    "/research/company-a?view=network",
    "/discover?type=founders",
])
def test_clean_react_deep_links_preserve_research_and_filter_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str,
) -> None:
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "deep-links.db"))
    with TestClient(app) as client:
        response = client.get(path, headers={"Accept": "text/html"}, follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/#" + path
        assert client.get(path, headers={"Accept": "application/json"}).status_code == 404
        assert client.get("/api/missing", headers={"Accept": "text/html"}).status_code == 404
