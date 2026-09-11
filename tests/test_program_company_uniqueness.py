"""Release regression: program company projections are canonical and cohort-bound."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

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
from venture_intelligence.shared.models import CanonicalEvent, EntityType, EventType, Source
from venture_intelligence.storage.cleanup import reconcile_directory_company_links
from venture_intelligence.storage.db import Database

NOW = datetime(2026, 1, 10, 10, tzinfo=timezone.utc)
SHARED_DIRECTORY_URL = "https://directory.example/api/companies"


def _company(
    *,
    source_id: str,
    name: str,
    program_id: str,
    batch_id: str,
    observed_minute: int = 0,
) -> CanonicalEvent:
    observed_at = NOW.replace(minute=observed_minute)
    return CanonicalEvent(
        event_id=str(uuid4()),
        event_type=EventType.COMPANY_LISTED,
        entity_type=EntityType.COMPANY,
        entity_id=source_id,
        source=Source.DIRECTORY,
        source_url=SHARED_DIRECTORY_URL,
        occurred_at=observed_at,
        observed_at=observed_at,
        ingested_at=observed_at,
        confidence=0.94,
        raw_payload={"name": name, "batch": batch_id, "website": f"https://{source_id}.test"},
        metadata={
            "program_id": program_id,
            "batch_id": batch_id,
            "identity_evidence": "explicit_public_company_record",
        },
    )


def _founder(company: CanonicalEvent, *, source_id: str, name: str) -> CanonicalEvent:
    profile_url = f"https://linkedin.com/in/{source_id}"
    claim = {
        "full_name": name,
        "title": "Founder",
        "linkedin_url": profile_url,
        "_claim_container": "founder_set",
    }
    return CanonicalEvent(
        event_id=str(uuid4()),
        event_type=EventType.FOUNDER_DETECTED,
        entity_type=EntityType.PERSON,
        entity_id=source_id,
        source=Source.DIRECTORY,
        source_url=company.source_url,
        occurred_at=company.occurred_at,
        observed_at=company.observed_at,
        ingested_at=company.ingested_at,
        confidence=0.92,
        raw_payload={
            "name": name,
            "title": "Founder",
            "profile_url": profile_url,
            "company_name": company.raw_payload["name"],
            "company_source_id": company.entity_id,
            "company_source_url": company.source_url,
            "founder_claim": claim,
        },
        metadata={
            "program_id": company.metadata["program_id"],
            "batch_id": company.metadata["batch_id"],
            "identity_validated": True,
            "identity_evidence": "explicit_public_founder_record",
            "identity_evidence_urls": [company.source_url],
            "attribution_status": "attached",
            "parent_event_id": company.event_id,
        },
    )


def _project(database: Database, *events: CanonicalEvent) -> None:
    resolver = EntityResolver(database)
    for event in events:
        database.write_event(event)
        canonical_id = resolver.resolve(event)
        EvidenceBuilder(database).process(event, canonical_id)
        GraphBuilder(database).process(event, canonical_id)
        resolver.reset_cache()


def test_directory_link_fast_path_repairs_legacy_collection_url_collapse(tmp_path) -> None:
    """A stale source link may not override the explicit directory company identity."""
    registry = get_program_registry()
    program = registry.get("speedrun_current")
    assert program is not None
    database = Database(tmp_path / "collapsed.db")
    motherboard = _company(
        source_id="speedrun__motherboard",
        name="Motherboard",
        program_id=program.id,
        batch_id=program.batch,
    )
    orbits = _company(
        source_id="speedrun__orbits",
        name="Orbits",
        program_id=program.id,
        batch_id=program.batch,
    )
    resolver = EntityResolver(database)
    motherboard_id = resolver.resolve(motherboard)
    database.write_entity_link(
        motherboard_id,
        "company",
        Source.DIRECTORY.value,
        orbits.entity_id,
        0.94,
    )

    orbits_id = resolver.resolve(orbits)

    assert orbits_id != motherboard_id
    assert database.resolve_entity_id(orbits.entity_id) == orbits_id
    assert {item.canonical_name for item in database.all_companies()} == {
        "Motherboard",
        "Orbits",
    }


def test_directory_record_reuses_exact_registry_source_identity_not_name_alone(tmp_path) -> None:
    program = get_program_registry().get("speedrun_current")
    assert program is not None
    database = Database(tmp_path / "source-identity.db")
    provisional = CanonicalEvent(
        event_type=EventType.COMPANY_LISTED,
        entity_type=EntityType.COMPANY,
        entity_id="speedrun__orbits",
        source=Source.TRIANGULATION,
        source_url="https://speedrun.example/feed",
        occurred_at=NOW,
        observed_at=NOW,
        ingested_at=NOW,
        confidence=0.82,
        raw_payload={"name": "Orbits", "program_id": program.id, "batch": program.batch},
        metadata={
            "program_id": program.id,
            "batch_id": program.batch,
            "evidence_tier": "corroborated_public_source",
        },
    )
    directory = _company(
        source_id="speedrun__orbits__a7b536de-fc02-5151-9267-a227385fc3eb",
        name="Orbits",
        program_id=program.id,
        batch_id=program.batch,
    )
    resolver = EntityResolver(database)
    provisional_id = resolver.resolve(provisional)
    resolver.reset_cache()

    assert resolver.resolve(directory) == provisional_id
    assert len(database.all_companies()) == 1
    assert database.get_company(provisional_id).website == directory.raw_payload["website"]


def test_program_company_read_model_is_unique_and_current_cohort_only(
    tmp_path,
    monkeypatch,
) -> None:
    registry = get_program_registry()
    yc = registry.get("yc_current")
    speedrun = registry.get("speedrun_current")
    assert yc is not None and speedrun is not None
    database = Database(tmp_path / "programs.db")

    yc_one = _company(
        source_id="yc__cedar",
        name="Cedar Labs",
        program_id=yc.id,
        batch_id=yc.batch,
    )
    yc_two = _company(
        source_id="yc__spruce",
        name="Spruce Labs",
        program_id=yc.id,
        batch_id=yc.batch,
    )
    yc_history = _company(
        source_id="yc__archive",
        name="Archive Labs",
        program_id=yc.id,
        batch_id="S26",
    )
    sr_one = _company(
        source_id="speedrun__orbits",
        name="Orbits",
        program_id=speedrun.id,
        batch_id=speedrun.batch,
    )
    sr_two = _company(
        source_id="speedrun__mirai",
        name="Mirai",
        program_id=speedrun.id,
        batch_id=speedrun.batch,
    )
    sr_history = _company(
        source_id="speedrun__archive",
        name="Old Speedrun Co",
        program_id=speedrun.id,
        batch_id="SR006",
    )
    events = [
        yc_one,
        _company(
            source_id=yc_one.entity_id,
            name="Cedar Labs",
            program_id=yc.id,
            batch_id=yc.batch,
            observed_minute=1,
        ),
        _founder(yc_one, source_id="yc__jane-cedar", name="Jane Cedar"),
        _founder(yc_one, source_id="yc__sam-cedar", name="Sam Cedar"),
        yc_two,
        yc_history,
        sr_one,
        _company(
            source_id=sr_one.entity_id,
            name="Orbits",
            program_id=speedrun.id,
            batch_id=speedrun.batch,
            observed_minute=1,
        ),
        _founder(sr_one, source_id="speedrun__ora-founder", name="Ora Patel"),
        _founder(sr_one, source_id="speedrun__obi-founder", name="Obi Chen"),
        sr_two,
        sr_history,
    ]
    _project(database, *events)
    for company in database.all_companies():
        SignalWorker(database).process_entity(company.company_id, as_of=NOW)
    for person in database.all_persons():
        SignalWorker(database).process_entity(person.person_id, as_of=NOW)
    RankingWorker(database).rank_all(ranked_at=NOW)

    # Two latest records with the same timestamp must still project one canonical row.
    cedar_id = database.resolve_entity_id(yc_one.entity_id)
    assert cedar_id is not None
    latest = database.get_ranking_for_entity(cedar_id)
    assert latest is not None
    database.write_ranking(latest.model_copy(update={"ranking_id": "same-time-tie"}))

    service = IntelligenceService(database)
    yc_items = service.rankings(entity_type="company", program_id=yc.id).items
    sr_items = service.rankings(entity_type="company", program_id=speedrun.id).items
    all_items = service.rankings(entity_type="company").items

    assert {item.entity.canonical_name for item in yc_items} == {"Cedar Labs", "Spruce Labs"}
    assert {item.entity.canonical_name for item in sr_items} == {"Orbits", "Mirai"}
    assert len({item.entity.entity_id for item in yc_items}) == len(yc_items) == 2
    assert len({item.entity.entity_id for item in sr_items}) == len(sr_items) == 2
    assert len({item.entity.entity_id for item in all_items}) == len(all_items)
    assert all(item.entity.entity_type == "company" for item in [*yc_items, *sr_items])
    cedar = next(item for item in yc_items if item.entity.canonical_name == "Cedar Labs")
    orbits = next(item for item in sr_items if item.entity.canonical_name == "Orbits")
    assert len(cedar.entity.founder_ids) == 2
    assert len(orbits.entity.founder_ids) == 2
    assert len(database.get_evidence_details_for_entity(cedar.entity.entity_id)) >= 2
    assert len(database.get_evidence_details_for_entity(orbits.entity.entity_id)) >= 2

    monkeypatch.setattr(settings, "db_path", str(tmp_path / "programs.db"))
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/intelligence/rankings",
            params={"entity_type": "company", "program_id": speedrun.id},
        )
    assert response.status_code == 200
    payload = response.json()
    ids = [item["entity"]["entity_id"] for item in payload["items"]]
    assert len(ids) == len(set(ids)) == 2
    assert payload["batch_id"] == speedrun.batch


def test_program_ranking_limit_is_applied_after_program_filtering(tmp_path) -> None:
    registry = get_program_registry()
    yc = registry.get("yc_current")
    speedrun = registry.get("speedrun_current")
    assert yc is not None and speedrun is not None
    database = Database(tmp_path / "program-ranking-limit.db")
    yc_company = _company(
        source_id="yc__target",
        name="Target Labs",
        program_id=yc.id,
        batch_id=yc.batch,
    )
    speedrun_company = _company(
        source_id="speedrun__higher-score",
        name="Higher Score Labs",
        program_id=speedrun.id,
        batch_id=speedrun.batch,
    )
    _project(database, yc_company, speedrun_company)
    for company in database.all_companies():
        SignalWorker(database).process_entity(company.company_id, as_of=NOW)
    RankingWorker(database).rank_all(ranked_at=NOW)

    yc_id = database.resolve_entity_id(yc_company.entity_id)
    speedrun_id = database.resolve_entity_id(speedrun_company.entity_id)
    assert yc_id is not None and speedrun_id is not None
    yc_ranking = database.get_ranking_for_entity(yc_id)
    speedrun_ranking = database.get_ranking_for_entity(speedrun_id)
    assert yc_ranking is not None and speedrun_ranking is not None
    database.write_ranking(
        yc_ranking.model_copy(
            update={"ranking_id": "yc-low-score", "composite_score": 0.1}
        )
    )
    database.write_ranking(
        speedrun_ranking.model_copy(
            update={"ranking_id": "speedrun-high-score", "composite_score": 0.9}
        )
    )

    result = IntelligenceService(database).rankings(program_id=yc.id, limit=1)

    assert [item.entity.canonical_name for item in result.items] == ["Target Labs"]


def test_directory_company_reconciliation_is_dry_run_first_and_preserves_raw_events(
    tmp_path,
) -> None:
    program = get_program_registry().get("speedrun_current")
    assert program is not None
    database = Database(tmp_path / "legacy.db")
    motherboard = _company(
        source_id="speedrun__motherboard",
        name="Motherboard",
        program_id=program.id,
        batch_id=program.batch,
    )
    orbits = _company(
        source_id="speedrun__orbits",
        name="Orbits",
        program_id=program.id,
        batch_id=program.batch,
    )
    founder = _founder(orbits, source_id="speedrun__alex-rivera", name="Alex Rivera")
    _project(database, motherboard)
    motherboard_id = database.resolve_entity_id(motherboard.entity_id)
    assert motherboard_id is not None
    database.write_event(orbits)
    database.write_entity_link(
        motherboard_id,
        "company",
        Source.DIRECTORY.value,
        orbits.entity_id,
        0.94,
    )
    EvidenceBuilder(database).process(orbits, motherboard_id)
    _project(database, founder)
    founder_id = database.resolve_entity_id(founder.entity_id)
    assert founder_id is not None
    with database._connect() as connection:
        raw_count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        wrong_edge = connection.execute(
            "SELECT target_id FROM graph_edges WHERE source_id=? AND edge_type='FOUNDED'",
            (founder_id,),
        ).fetchone()
    assert wrong_edge[0] == motherboard_id

    dry_run = reconcile_directory_company_links(database)
    assert len(dry_run["mismatches"]) == 1
    assert database.resolve_entity_id(orbits.entity_id) == motherboard_id

    applied = reconcile_directory_company_links(database, apply=True)
    assert len(applied["repaired"]) == 1
    orbits_id = database.resolve_entity_id(orbits.entity_id)
    assert orbits_id is not None and orbits_id != motherboard_id
    with database._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == raw_count
        targets = {
            row[0]
            for row in connection.execute(
                "SELECT target_id FROM graph_edges WHERE source_id=? AND edge_type='FOUNDED'",
                (founder_id,),
            )
        }
    assert targets == {orbits_id}
    assert database.get_company(motherboard_id).founder_ids == []
    assert database.get_company(orbits_id).founder_ids == [founder_id]
    assert reconcile_directory_company_links(database)["mismatches"] == []
