"""
tests/test_pipeline.py — integration tests for the full pipeline.

Tests the complete flow: event → validate → resolve → evidence → signal → rank.
Uses an in-memory SQLite DB so tests are fully isolated and fast.
"""

from datetime import datetime, timezone

import pytest
from venture_intelligence.monitor import pipeline_tick
from venture_intelligence.pipeline.classification_worker import ClassificationWorker
from venture_intelligence.pipeline.entity_resolution import EntityResolver
from venture_intelligence.pipeline.evidence_builder import EvidenceBuilder
from venture_intelligence.pipeline.graph import GraphBuilder
from venture_intelligence.pipeline.notification import NotificationWorker
from venture_intelligence.pipeline.ranking_worker import RankingWorker
from venture_intelligence.pipeline.recommendation_worker import RecommendationWorker
from venture_intelligence.pipeline.signal_worker import SignalWorker
from venture_intelligence.pipeline.validator import EventValidator
from venture_intelligence.shared.bus import EventBus
from venture_intelligence.shared.models import (
    CanonicalEvent,
    EntityType,
    EventType,
    SignalType,
    Source,
)
from venture_intelligence.storage.db import Database


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


@pytest.fixture
def bus(db):
    return EventBus(db)


def _event(**kwargs) -> CanonicalEvent:
    defaults = dict(
        event_type=EventType.COMPANY_LISTED,
        entity_type=EntityType.COMPANY,
        entity_id="yc__acme",
        source=Source.YC_WEBSITE,
        source_url="https://ycombinator.com/companies/acme",
        occurred_at=datetime.now(timezone.utc),
        confidence=0.97,
        raw_payload={"name": "Acme", "slug": "acme"},
    )
    defaults.update(kwargs)
    return CanonicalEvent(**defaults)


# ── Validator ─────────────────────────────────────────────────────────────────


class TestValidator:
    def test_valid_event_passes(self):
        v = EventValidator()
        ok, err = v.validate(_event())
        assert ok and err is None

    def test_bad_confidence_fails(self):
        v = EventValidator()
        ev = _event(confidence=1.5)
        ok, err = v.validate(ev)
        assert not ok and "confidence" in err

    def test_future_occurred_at_fails(self):
        from datetime import timedelta

        v = EventValidator()
        ev = _event(occurred_at=datetime.now(timezone.utc) + timedelta(hours=5))
        ok, err = v.validate(ev)
        assert not ok and "future" in err

    def test_bad_url_fails(self):
        v = EventValidator()
        ev = _event(source_url="not-a-url")
        ok, err = v.validate(ev)
        assert not ok

    def test_wrong_entity_type_fails(self):
        v = EventValidator()
        # COMPANY_LISTED must have entity_type=COMPANY
        ev = _event(entity_type=EntityType.PERSON)
        ok, err = v.validate(ev)
        assert not ok


# ── Entity Resolution ─────────────────────────────────────────────────────────


class TestEntityResolver:
    def test_creates_new_company(self, db):
        r = EntityResolver(db)
        ev = _event()
        cid = r.resolve(ev)
        assert cid is not None
        assert db.get_company(cid) is not None

    def test_resolves_same_entity_twice(self, db):
        r = EntityResolver(db)
        ev = _event()
        id1 = r.resolve(ev)
        id2 = r.resolve(ev)
        assert id1 == id2

    def test_fuzzy_name_match(self, db):
        r = EntityResolver(db)
        # First event creates "NeuralStack" — similarity to "NeuralStack Inc" is 0.846 (> 0.82)
        r.resolve(
            _event(
                entity_id="yc__neuralstack",
                raw_payload={"name": "NeuralStack", "slug": "neuralstack"},
            )
        )
        # Second event with close variant should resolve to the same entity
        r.reset_cache()
        r.resolve(
            _event(
                entity_id="linkedin_co__neuralstack-inc",
                source=Source.LINKEDIN,
                source_url="https://linkedin.com/company/neuralstack",
                raw_payload={"name": "NeuralStack Inc"},
            )
        )
        companies = db.all_companies()
        assert len(companies) == 1  # merged, not duplicated

    def test_creates_person(self, db):
        r = EntityResolver(db)
        ev = _event(
            event_type=EventType.FOUNDER_DETECTED,
            entity_type=EntityType.PERSON,
            entity_id="linkedin_ppl__john-doe",
            source=Source.LINKEDIN,
            source_url="https://linkedin.com/in/john-doe",
            raw_payload={"name": "John Doe", "headline": "CEO @ Acme | YC F26"},
        )
        pid = r.resolve(ev)
        assert db.get_person(pid) is not None


# ── Evidence Builder ──────────────────────────────────────────────────────────


class TestEvidenceBuilder:
    def test_builds_observation_and_evidence(self, db):
        resolver = EntityResolver(db)
        builder = EvidenceBuilder(db)
        ev = _event()
        cid = resolver.resolve(ev)
        builder.process(ev, cid)

        evidence = db.get_evidence_for_entity(cid)
        assert len(evidence) >= 1
        assert any(e.claim == "Has confirmed program membership" for e in evidence)

    def test_commit_velocity_produces_building_claim(self, db):
        resolver = EntityResolver(db)
        builder = EvidenceBuilder(db)
        ev = _event(
            event_type=EventType.COMMIT_VELOCITY_HIGH,
            entity_type=EntityType.REPOSITORY,
            entity_id="github__johndoe__acme-core",
            source=Source.GITHUB,
            source_url="https://github.com/johndoe/acme-core",
            raw_payload={"owner": "johndoe", "repo": "acme-core", "commits_last_7d": 47},
        )
        cid = resolver.resolve(ev)
        builder.process(ev, cid)
        evidence = db.get_evidence_for_entity(cid)
        claims = [e.claim for e in evidence]
        assert "Is building actively" in claims


# ── Signal Worker ─────────────────────────────────────────────────────────────


class TestSignalWorker:
    def _setup_yc_entity(self, db) -> str:
        """Create a company with confirmed YC evidence and return its canonical ID."""
        resolver = EntityResolver(db)
        builder = EvidenceBuilder(db)
        ev = _event()
        cid = resolver.resolve(ev)
        builder.process(ev, cid)
        return cid

    def test_program_affinity_is_high_for_direct_listing(self, db):
        cid = self._setup_yc_entity(db)
        worker = SignalWorker(db)
        sigs = worker.process_entity(cid)
        program_signal = next(
            (s for s in sigs if s.signal_type == SignalType.PROGRAM_AFFINITY), None
        )
        assert program_signal is not None
        assert program_signal.value >= 0.95

    def test_technical_depth_from_repo(self, db):
        resolver = EntityResolver(db)
        builder = EvidenceBuilder(db)
        ev = _event(
            event_type=EventType.REPO_CREATED,
            entity_type=EntityType.REPOSITORY,
            entity_id="github__founder__repo",
            source=Source.GITHUB,
            source_url="https://github.com/founder/repo",
            raw_payload={"owner": "founder", "repo": "repo", "stars": 50},
        )
        cid = resolver.resolve(ev)
        builder.process(ev, cid)
        sigs = SignalWorker(db).process_entity(cid)
        tech = next((s for s in sigs if s.signal_type == SignalType.TECHNICAL_DEPTH), None)
        assert tech is not None
        assert tech.value > 0

    def test_no_signals_for_entity_with_no_evidence(self, db):
        sigs = SignalWorker(db).process_entity("ghost__entity")
        assert sigs == []


# ── Ranking Worker ────────────────────────────────────────────────────────────


class TestRankingWorker:
    def test_rank_all_produces_rankings(self, db):
        # Setup two entities with different signal strengths
        resolver = EntityResolver(db)
        builder = EvidenceBuilder(db)
        signal = SignalWorker(db)

        ev1 = _event(entity_id="yc__strong", raw_payload={"name": "Strong Co", "slug": "strong"})
        ev2 = _event(
            entity_id="linkedin_co__weak",
            source=Source.LINKEDIN,
            source_url="https://linkedin.com/company/weak",
            confidence=0.65,
            raw_payload={"name": "Weak Co", "slug": "weak"},
        )
        for ev in [ev1, ev2]:
            cid = resolver.resolve(ev)
            builder.process(ev, cid)
            signal.process_entity(cid)

        rankings = RankingWorker(db).rank_all()
        assert len(rankings) >= 2
        # Rankings are sorted: rank 1 should have higher composite_score than rank 2
        assert rankings[0].composite_score >= rankings[1].composite_score
        assert rankings[0].rank == 1

    def test_rank_is_globally_ordered(self, db):
        resolver = EntityResolver(db)
        builder = EvidenceBuilder(db)
        signal = SignalWorker(db)

        for i in range(5):
            ev = _event(
                entity_id=f"yc__co{i}",
                raw_payload={"name": f"Venture {i}", "slug": f"co{i}"},
            )
            cid = resolver.resolve(ev)
            builder.process(ev, cid)
            signal.process_entity(cid)

        rankings = RankingWorker(db).rank_all()
        ranks = [r.rank for r in rankings]
        assert ranks == sorted(ranks)


# ── Classification Worker ─────────────────────────────────────────────────────


class TestClassificationWorker:
    def test_classifies_founder_by_title(self, db):
        from venture_intelligence.shared.models import Person

        person = Person(
            canonical_name="Jane Doe",
            current_title="Co-Founder & CEO at Acme",
        )
        db.write_person(person)
        clf = ClassificationWorker(db).classify_entity(person.person_id)
        assert clf is not None
        from venture_intelligence.shared.models import PersonType

        assert clf.person_type == PersonType.FOUNDER
        assert clf.confidence >= 0.80

    def test_classifies_researcher_by_title(self, db):
        from venture_intelligence.shared.models import Person, PersonType

        person = Person(canonical_name="Dr. Smith", current_title="PhD Researcher at MIT")
        db.write_person(person)
        clf = ClassificationWorker(db).classify_entity(person.person_id)
        assert clf.person_type == PersonType.RESEARCHER

    def test_returns_none_for_company(self, db):
        from venture_intelligence.shared.models import Company

        company = Company(canonical_name="Acme Corp")
        db.write_company(company)
        clf = ClassificationWorker(db).classify_entity(company.company_id)
        assert clf is None


# ── CRM Workflow ──────────────────────────────────────────────────────────────


class TestCRMWorkflow:
    def test_valid_transition(self, db):
        from venture_intelligence.crm.workflow import CRMWorkflow
        from venture_intelligence.shared.models import CRMStage

        crm = CRMWorkflow(db)
        record = crm.ensure_discovery("entity__test")
        assert record.stage == CRMStage.DISCOVERY
        updated = crm.advance_stage("entity__test", CRMStage.QUALIFICATION, partner="R")
        assert updated.stage == CRMStage.QUALIFICATION

    def test_invalid_transition_raises(self, db):
        from venture_intelligence.crm.workflow import CRMWorkflow
        from venture_intelligence.shared.models import CRMStage

        crm = CRMWorkflow(db)
        crm.ensure_discovery("entity__test2")
        with pytest.raises(ValueError, match="Invalid CRM transition"):
            crm.advance_stage("entity__test2", CRMStage.PORTFOLIO)

    def test_add_note(self, db):
        from venture_intelligence.crm.workflow import CRMWorkflow

        crm = CRMWorkflow(db)
        crm.ensure_discovery("entity__note")
        record = crm.add_note("entity__note", "Met at YC demo day", partner="Smaiyl")
        assert any("Met at YC demo day" in n for n in record.notes)

    def test_audit_trail(self, db):
        from venture_intelligence.crm.workflow import CRMWorkflow
        from venture_intelligence.shared.models import CRMStage

        crm = CRMWorkflow(db)
        crm.ensure_discovery("entity__audit")
        crm.advance_stage("entity__audit", CRMStage.QUALIFICATION)
        history = crm.get_history("entity__audit")
        assert len(history) >= 1
        assert history[0]["from_stage"] == CRMStage.DISCOVERY.value


# ── Event Bus ─────────────────────────────────────────────────────────────────


class TestEventBus:
    def test_publish_consume_ack(self, db, bus):
        ev = _event()
        bus.publish(ev)
        pending = bus.consume("validator")
        assert any(e.event_id == ev.event_id for e in pending)
        bus.ack(ev.event_id, "validator")
        assert not any(e.event_id == ev.event_id for e in bus.consume("validator"))

    def test_nack_keeps_event_failed(self, db, bus):
        ev = _event(entity_id="yc__nack_test")
        bus.publish(ev)
        bus.nack(ev.event_id, "validator", "test error")
        # Failed events don't reappear in pending
        pending = bus.consume("validator")
        assert not any(e.event_id == ev.event_id for e in pending)

    def test_each_worker_gets_own_queue(self, db, bus):
        ev = _event(entity_id="yc__multiworker")
        bus.publish(ev)
        # All workers should see the event independently
        for worker in ["validator", "resolver", "graph_builder"]:
            pending = bus.consume(worker)
            assert any(e.event_id == ev.event_id for e in pending), f"{worker} missing event"


def test_generic_candidates_are_rejected_before_eventbus_persistence(db, bus):
    """Regression: search/platform labels cannot reach any worker queue."""
    for label in ("twitter", "keyframes", "linkedin", "instagram", "github", "arxiv"):
        event = _event(entity_id=f"search__{label}", raw_payload={"name": label})
        assert bus.publish(event) is False
        assert db.get_events_for_entity(event.entity_id) == []
        assert db.queue_status(event.event_id, "resolver") is None
    rejected = db.get_candidate_rejections(limit=10)
    assert {item["candidate_label"] for item in rejected} >= {
        "twitter",
        "keyframes",
        "linkedin",
        "instagram",
        "github",
        "arxiv",
    }


def test_database_quarantines_pre_gate_legacy_events(tmp_path):
    path = tmp_path / "legacy.db"
    db = Database(str(path))
    legacy = _event(entity_id="yc__twitter", raw_payload={"name": "twitter"})
    db.write_event(legacy)  # Simulates an event written by the pre-gate release.
    reopened = Database(str(path))
    assert reopened.queue_status(legacy.event_id, "resolver") == "failed"
    assert any(
        item["rejection_id"] == f"legacy:{legacy.event_id}"
        for item in reopened.get_candidate_rejections()
    )


@pytest.mark.asyncio
async def test_validator_failure_blocks_downstream_workers(db, bus):
    ev = _event(entity_type=EntityType.PERSON)
    assert bus.publish(ev) is False
    await pipeline_tick(
        bus,
        db,
        EventValidator(),
        EntityResolver(db),
        GraphBuilder(db),
        EvidenceBuilder(db),
        SignalWorker(db),
        RankingWorker(db),
        RecommendationWorker(db),
        NotificationWorker(db),
    )
    assert db.queue_status(ev.event_id, "validator") is None
    assert db.queue_status(ev.event_id, "resolver") is None
    rejected = db.get_candidate_rejections()
    assert rejected[0]["candidate_label"] == "Acme"
    assert rejected[0]["reason"] == "insufficient_person_identity_evidence"
    assert db.all_companies() == []


# ── Configuration-driven connectors ───────────────────────────────────────────


class TestConfigurationDrivenConnectors:
    def test_registry_resolves_case_insensitive_keywords(self):
        from venture_intelligence.programs import ProgramRegistry

        registry = ProgramRegistry.load()
        assert registry.resolve_program_for_text("Founder in YC s26") is not None
        assert registry.by_batch("sr007").id == "speedrun_current"

    def test_directory_connector_has_no_targeted_lookup(self):
        from venture_intelligence.connectors.directory import DirectoryConnector
        from venture_intelligence.programs import ProgramRegistry

        assert not DirectoryConnector(ProgramRegistry.load()).can_investigate(_event())

    def test_triangulation_does_not_investigate_its_own_event(self):
        from venture_intelligence.connectors.triangulation import TriangulationConnector
        from venture_intelligence.programs import ProgramRegistry

        event = _event(source=Source.TRIANGULATION, raw_payload={"name": "Acme"})
        assert not TriangulationConnector(ProgramRegistry.load()).can_investigate(event)
