"""Milestone 3 isolated historical evaluation regressions."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from venture_intelligence.api.app import app
from venture_intelligence.config import settings
from venture_intelligence.evaluation.cli import main as evaluation_cli
from venture_intelligence.evaluation.models import (
    EvaluationRequest,
    EvaluationStatus,
    EvaluationTargetLevel,
    GroundTruthImportRequest,
    GroundTruthRecord,
    MetricName,
    SourceAblationSpec,
)
from venture_intelligence.evaluation.service import EvaluationService
from venture_intelligence.replay.models import ReplayMode
from venture_intelligence.shared.models import CanonicalEvent, EntityType, EventType, Source
from venture_intelligence.storage.db import Database

UTC = timezone.utc
CUTOFF = datetime(2026, 1, 15, 12, tzinfo=UTC)
OUTCOME = datetime(2026, 2, 1, 12, tzinfo=UTC)


def _event(
    event_id: str,
    name: str,
    entity_id: str,
    *,
    observed_at: datetime,
    source: Source = Source.YC_WEBSITE,
    entity_type: EntityType = EntityType.COMPANY,
) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=event_id,
        event_type=(
            EventType.COMPANY_LISTED
            if entity_type is EntityType.COMPANY
            else EventType.FOUNDER_DETECTED
        ),
        entity_type=entity_type,
        entity_id=entity_id,
        source=source,
        source_url=f"https://example.com/{event_id}",
        occurred_at=observed_at - timedelta(days=1),
        observed_at=observed_at,
        ingested_at=observed_at + timedelta(days=30),
        confidence=0.95,
        raw_payload={
            "name": name,
            "batch": "S26",
            **(
                {"profile_url": f"https://linkedin.com/in/{event_id}", "company": "Acme Labs"}
                if entity_type is EntityType.PERSON
                else {}
            ),
        },
        metadata={"program_id": "yc_current", "batch": "S26"},
    )


def _ground_truth(*records: GroundTruthRecord) -> GroundTruthImportRequest:
    return GroundTruthImportRequest(
        program_id="yc_current",
        batch_id="S26",
        target_level=EvaluationTargetLevel.COMPANY,
        outcome_timestamp=OUTCOME,
        source_url="https://www.ycombinator.com/companies",
        records=list(records),
        metadata={"publisher": "Y Combinator"},
    )


def _record(name: str, *, positive: bool = True) -> GroundTruthRecord:
    return GroundTruthRecord(
        canonical_name=name,
        entity_type=EntityType.COMPANY,
        is_positive=positive,
        source_url="https://www.ycombinator.com/companies",
        provenance={"publisher": "Y Combinator", "retrieved_at": OUTCOME.isoformat()},
    )


def _service(tmp_path: Path) -> tuple[Database, EvaluationService]:
    database = Database(str(tmp_path / "evaluation.db"))
    return database, EvaluationService(database)


def _metric(run: object, name: MetricName) -> object:
    return next(metric for metric in run.metrics if metric.name is name)


def test_ground_truth_validation_is_typed_exact_and_integrity_gated() -> None:
    with pytest.raises(ValueError, match="generic"):
        _record("Twitter")
    with pytest.raises(ValueError, match="entity_type"):
        GroundTruthImportRequest(
            **_ground_truth().model_dump(exclude={"target_level", "records"}),
            target_level=EvaluationTargetLevel.PERSON,
            records=[_record("Acme Labs")],
        )
    with pytest.raises(ValueError, match="duplicate"):
        _ground_truth(
            GroundTruthRecord(
                **_record("Acme Labs").model_dump(exclude={"aliases"}),
                aliases=["Beta Labs"],
            ),
            _record("Beta Labs"),
        )
    with pytest.raises(ValueError, match="UTC offset"):
        GroundTruthImportRequest(
            **_ground_truth().model_dump(exclude={"outcome_timestamp"}),
            outcome_timestamp=datetime(2026, 2, 1),
        )


def test_precision_recall_lead_time_false_positive_and_missed_are_reproducible(
    tmp_path: Path,
) -> None:
    database, service = _service(tmp_path)
    database.write_event(
        _event("acme", "Acme Labs", "yc__acme", observed_at=CUTOFF - timedelta(days=5))
    )
    database.write_event(
        _event("beta", "Beta Systems", "yc__beta", observed_at=CUTOFF - timedelta(days=3))
    )
    dataset = service.import_ground_truth(
        _ground_truth(_record("Acme Labs"), _record("Missing Labs"))
    )
    request = EvaluationRequest(ground_truth_dataset_id=dataset.dataset_id, cutoff=CUTOFF)

    first = service.run(request)
    second = service.run(request)

    assert first.evaluation_run_id == second.evaluation_run_id
    assert first.report_digest == second.report_digest
    assert first.snapshot.replay_projection_digest == second.snapshot.replay_projection_digest
    assert _metric(first, MetricName.PRECISION_AT_10).value == 0.1
    assert _metric(first, MetricName.RECALL).value == 0.5
    assert _metric(first, MetricName.FALSE_POSITIVES_AT_25).value == 1
    assert _metric(first, MetricName.MISSED_ENTITIES).value == 1
    assert _metric(first, MetricName.AVERAGE_LEAD_TIME_DAYS).value == 22
    assert first.false_positives[0].canonical_name == "Beta Systems"
    assert first.missed_entities[0].category == "not_discovered"
    assert first.live_state_mutated is False


def test_future_observation_cannot_leak_and_ingested_at_is_not_cutoff(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    database.write_event(
        _event(
            "included",
            "Acme Labs",
            "yc__acme",
            observed_at=CUTOFF - timedelta(seconds=1),
        )
    )
    database.write_event(
        _event(
            "future",
            "Future Labs",
            "yc__future",
            observed_at=CUTOFF + timedelta(seconds=1),
        )
    )
    dataset = service.import_ground_truth(
        _ground_truth(_record("Acme Labs"), _record("Future Labs"))
    )

    run = service.run(EvaluationRequest(ground_truth_dataset_id=dataset.dataset_id, cutoff=CUTOFF))

    assert run.snapshot.replay_result.selected_event_ids == ["included"]
    assert run.snapshot.replay_result.default_historical_field == "observed_at"
    assert run.missed_entities[0].canonical_name == "Future Labs"


def test_evaluation_does_not_mutate_operational_state(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    database.write_event(
        _event("acme", "Acme Labs", "yc__acme", observed_at=CUTOFF - timedelta(days=1))
    )
    with database._connect() as connection:
        before = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("events", "companies", "persons", "rankings", "alerts", "crm_records")
        }
    dataset = service.import_ground_truth(_ground_truth(_record("Acme Labs")))

    run = service.run(EvaluationRequest(ground_truth_dataset_id=dataset.dataset_id, cutoff=CUTOFF))

    with database._connect() as connection:
        after = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
    assert after == before
    assert run.snapshot.replay_result.counts.alerts == 0
    assert run.snapshot.replay_result.counts.crm_records == 0
    assert {"email", "slack", "crm", "external_collection"} <= set(run.side_effects_suppressed)


def test_source_ablation_is_bounded_and_uses_isolated_replay(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    database.write_event(
        _event("acme", "Acme Labs", "yc__acme", observed_at=CUTOFF - timedelta(days=2))
    )
    dataset = service.import_ground_truth(_ground_truth(_record("Acme Labs")))

    run = service.run(
        EvaluationRequest(
            ground_truth_dataset_id=dataset.dataset_id,
            cutoff=CUTOFF,
            ablations=[
                SourceAblationSpec(
                    ablation_id="without_yc",
                    excluded_sources=[Source.YC_WEBSITE],
                )
            ],
        )
    )

    ablation = run.ablations[0]
    assert ablation.excluded_sources == [Source.YC_WEBSITE]
    assert ablation.deltas_from_baseline[MetricName.RECALL] == -1
    assert ablation.replay_id != run.snapshot.replay_result.replay_id


def test_empty_batch_and_person_level_are_unambiguous(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    empty = service.import_ground_truth(_ground_truth())
    empty_run = service.run(
        EvaluationRequest(ground_truth_dataset_id=empty.dataset_id, cutoff=CUTOFF)
    )
    assert empty_run.status is EvaluationStatus.EMPTY
    assert _metric(empty_run, MetricName.RECALL).denominator == 0

    person_request = GroundTruthImportRequest(
        program_id="yc_current",
        batch_id="S26",
        target_level=EvaluationTargetLevel.PERSON,
        outcome_timestamp=OUTCOME,
        source_url="https://example.com/people",
        records=[
            GroundTruthRecord(
                canonical_name="Ada Lovelace",
                entity_type=EntityType.PERSON,
                source_url="https://example.com/ada",
                provenance={"publisher": "Example"},
            )
        ],
    )
    person = service.import_ground_truth(person_request)
    assert person.target_level is EvaluationTargetLevel.PERSON


def test_company_and_person_rankings_are_evaluated_separately(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    database.write_event(
        _event("acme", "Acme Labs", "yc__acme", observed_at=CUTOFF - timedelta(days=2))
    )
    database.write_event(
        _event(
            "ada",
            "Ada Lovelace",
            "yc__ada-lovelace",
            observed_at=CUTOFF - timedelta(days=1),
            source=Source.LINKEDIN,
            entity_type=EntityType.PERSON,
        )
    )
    company_dataset = service.import_ground_truth(_ground_truth(_record("Acme Labs")))
    person_dataset = service.import_ground_truth(
        GroundTruthImportRequest(
            program_id="yc_current",
            batch_id="S26",
            target_level=EvaluationTargetLevel.PERSON,
            outcome_timestamp=OUTCOME,
            source_url="https://example.com/founders",
            records=[
                GroundTruthRecord(
                    canonical_name="Ada Lovelace",
                    entity_type=EntityType.PERSON,
                    source_url="https://example.com/ada",
                    provenance={"publisher": "Example"},
                )
            ],
        )
    )

    company_run = service.run(
        EvaluationRequest(ground_truth_dataset_id=company_dataset.dataset_id, cutoff=CUTOFF)
    )
    person_run = service.run(
        EvaluationRequest(ground_truth_dataset_id=person_dataset.dataset_id, cutoff=CUTOFF)
    )
    types = {
        item["entity_id"]: item["entity_type"]
        for item in company_run.snapshot.replay_result.snapshot.entities
    }

    assert all(
        types[entity_id] == "company" for entity_id in company_run.snapshot.ranked_entity_ids
    )
    assert all(types[entity_id] == "person" for entity_id in person_run.snapshot.ranked_entity_ids)
    assert _metric(company_run, MetricName.RECALL).value == 1
    assert _metric(person_run, MetricName.RECALL).value == 1


def test_additive_migration_from_pre_m3_database(tmp_path: Path) -> None:
    path = tmp_path / "m2.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_migrations VALUES ('0001_temporal_foundation', ?) ",
            (CUTOFF.isoformat(),),
        )

    database = Database(str(path))

    with database._connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        versions = {
            row["version"] for row in connection.execute("SELECT version FROM schema_migrations")
        }
    assert {"ground_truth_datasets", "evaluation_runs_v2"} <= tables
    assert "0002_historical_evaluation" in versions
    assert "0003_feature_lineage" in versions


def test_local_cli_imports_and_runs_auditable_evaluation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = Database(str(tmp_path / "cli.db"))
    ground_truth_file = tmp_path / "ground-truth.json"
    ground_truth_file.write_text(_ground_truth().model_dump_json(), encoding="utf-8")
    assert (
        evaluation_cli(
            ["--database", str(database._path), "import", "--file", str(ground_truth_file)]
        )
        == 0
    )
    dataset = json.loads(capsys.readouterr().out)

    assert (
        evaluation_cli(
            [
                "--database",
                str(database._path),
                "run",
                "--ground-truth",
                dataset["dataset_id"],
                "--cutoff",
                CUTOFF.isoformat(),
                "--mode",
                ReplayMode.OBSERVED_AT.value,
            ]
        )
        == 0
    )
    run = json.loads(capsys.readouterr().out)
    assert run["snapshot"]["replay_result"]["live_state_mutated"] is False
    assert run["ground_truth_digest"] == dataset["digest"]


def test_evaluation_api_is_typed_read_only_and_reports_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "api.db"
    monkeypatch.setattr(settings, "db_path", str(path))
    database = Database(str(path))
    dataset = EvaluationService(database).import_ground_truth(_ground_truth())
    run = EvaluationService(database).run(
        EvaluationRequest(ground_truth_dataset_id=dataset.dataset_id, cutoff=CUTOFF)
    )

    with TestClient(app) as client:
        contract = client.get("/api/v1/evaluations/contract")
        listing = client.get("/api/v1/evaluations")
        detail = client.get(f"/api/v1/evaluations/{run.evaluation_run_id}")
        blocked = client.post("/api/v1/evaluations", json={})
        schema = client.get("/openapi.json").json()

    assert contract.status_code == 200
    assert contract.json()["remote_execution_enabled"] is False
    assert listing.json()["evaluations"][0]["report_digest"] == run.report_digest
    assert detail.json()["provenance"]["ground_truth_dataset_id"] == dataset.dataset_id
    assert blocked.status_code in {404, 405}
    response = schema["paths"]["/api/v1/evaluations/{evaluation_run_id}"]["get"]
    assert response["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "EvaluationRun"
    )
