"""Milestone 2 historical replay isolation and determinism regressions."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from venture_intelligence.api.app import app
from venture_intelligence.evaluation.replay import BatchReplayer
from venture_intelligence.replay.cli import main as replay_cli
from venture_intelligence.replay.models import ReplayMode, ReplayRequest, ReplayStatus
from venture_intelligence.replay.service import ReplayContext, ReplayService
from venture_intelligence.shared.models import (
    CanonicalEvent,
    EntityType,
    EventType,
    Source,
    TemporalPrecision,
    TemporalStatus,
    TemporalValidity,
)
from venture_intelligence.storage.db import Database

UTC = timezone.utc
CUTOFF = datetime(2026, 1, 15, 12, tzinfo=UTC)


def _company_event(
    event_id: str = "event-acme",
    *,
    name: str = "Acme Labs",
    entity_id: str = "yc__acme-labs",
    occurred_at: datetime | None = None,
    observed_at: datetime | None = None,
    ingested_at: datetime | None = None,
    validity: TemporalValidity | None = None,
) -> CanonicalEvent:
    occurred = occurred_at or CUTOFF - timedelta(days=10)
    observed = observed_at or CUTOFF - timedelta(days=5)
    ingested = ingested_at or observed
    return CanonicalEvent(
        event_id=event_id,
        event_type=EventType.COMPANY_LISTED,
        entity_type=EntityType.COMPANY,
        entity_id=entity_id,
        source=Source.YC_WEBSITE,
        source_url=f"https://www.ycombinator.com/companies/{entity_id.split('__', 1)[-1]}",
        occurred_at=occurred,
        occurred_at_precision=TemporalPrecision.EXACT,
        observed_at=observed,
        ingested_at=ingested,
        validity=validity,
        confidence=0.95,
        raw_payload={"name": name, "slug": entity_id.split("__", 1)[-1], "batch": "F26"},
        metadata={"program_id": "yc", "batch": "F26"},
    )


def _database(tmp_path: Path) -> Database:
    return Database(str(tmp_path / "live.db"))


def _dump(db: Database) -> list[str]:
    with db._connect() as connection:
        return list(connection.iterdump())


def test_observed_at_is_default_cutoff_and_future_observation_cannot_leak(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    database.write_event(
        _company_event(
            "future-observation",
            occurred_at=CUTOFF - timedelta(days=30),
            observed_at=CUTOFF + timedelta(seconds=1),
            ingested_at=CUTOFF + timedelta(seconds=1),
        )
    )

    replay = ReplayService(database).run(ReplayRequest(cutoff=CUTOFF))

    assert replay.status is ReplayStatus.EMPTY
    assert replay.selected_event_ids == []
    assert replay.counts.eligible_events == 0


def test_ingested_at_is_not_the_default_historical_boundary(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.write_event(
        _company_event(
            observed_at=CUTOFF - timedelta(seconds=1),
            ingested_at=CUTOFF + timedelta(days=20),
        )
    )

    replay = ReplayService(database).run(ReplayRequest(cutoff=CUTOFF))

    assert replay.status is ReplayStatus.COMPLETED
    assert replay.counts.replayed_events == 1
    assert replay.default_historical_field == "observed_at"


def test_occurred_at_reconstruction_is_explicitly_distinct(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.write_event(
        _company_event(
            observed_at=CUTOFF + timedelta(days=2),
            ingested_at=CUTOFF + timedelta(days=2),
            occurred_at=CUTOFF - timedelta(days=10),
        )
    )

    observed = ReplayService(database).run(ReplayRequest(cutoff=CUTOFF))
    occurred = ReplayService(database).run(
        ReplayRequest(cutoff=CUTOFF, mode=ReplayMode.OCCURRED_AT)
    )

    assert observed.status is ReplayStatus.EMPTY
    assert occurred.status is ReplayStatus.COMPLETED
    assert occurred.mode is ReplayMode.OCCURRED_AT
    assert occurred.snapshot is not None
    assert occurred.snapshot.features
    assert occurred.snapshot.rankings


def test_replay_never_mutates_live_database_or_operational_state(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.write_event(_company_event())
    before = _dump(database)

    replay = ReplayService(database).run(ReplayRequest(cutoff=CUTOFF))

    assert _dump(database) == before
    assert replay.live_state_mutated is False
    assert replay.counts.alerts == 0
    assert replay.counts.crm_records == 0
    assert {"alerts", "email", "slack", "crm", "external_collection"} <= set(
        replay.side_effects_suppressed
    )


def test_identical_inputs_produce_identical_relevant_projections(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.write_event(_company_event())
    service = ReplayService(database)

    first = service.run(ReplayRequest(cutoff=CUTOFF))
    second = service.run(ReplayRequest(cutoff=CUTOFF))

    assert first.replay_id == second.replay_id
    assert first.input_digest == second.input_digest
    assert first.projection_digest == second.projection_digest
    assert first.snapshot == second.snapshot
    assert first.counts == second.counts


def test_temporal_graph_relationship_reconstructs_validity_and_provenance(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    valid_from = datetime(2025, 12, 1, tzinfo=UTC)
    valid_to = datetime(2026, 1, 1, tzinfo=UTC)
    database.write_event(
        _company_event(
            validity=TemporalValidity(
                valid_from=valid_from,
                valid_to=valid_to,
                precision=TemporalPrecision.EXACT,
                status=TemporalStatus.HISTORICAL,
            )
        )
    )

    replay = ReplayService(database).run(ReplayRequest(cutoff=CUTOFF))
    edge = replay.snapshot.graph_edges[0]

    assert edge["valid_from"] == valid_from.isoformat()
    assert edge["valid_to"] == valid_to.isoformat()
    assert edge["validity_precision"] == "exact"
    assert edge["temporal_status"] == "historical"
    assert edge["evidence_ids"] == ["event-acme"]


def test_empty_partial_and_integrity_rejected_states_are_truthful(tmp_path: Path) -> None:
    empty_database = Database(str(tmp_path / "empty.db"))
    empty = ReplayService(empty_database).run(ReplayRequest(cutoff=CUTOFF))
    assert empty.status is ReplayStatus.EMPTY
    assert empty.projection_digest

    database = _database(tmp_path)
    database.write_event(
        _company_event(name="Twitter", entity_id="yc__twitter", event_id="bad-twitter")
    )
    partial = ReplayService(database).run(ReplayRequest(cutoff=CUTOFF))

    assert partial.status is ReplayStatus.PARTIAL
    assert partial.counts.rejected_events == 1
    assert partial.counts.companies == 0
    assert partial.counts.rankings == 0
    assert partial.counts.graph_nodes == 0
    assert partial.snapshot.entities == []
    assert partial.errors[0].code == "candidate_rejected"


def test_bounded_partial_replay_preserves_valid_projection(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.write_event(_company_event(event_id="first"))
    database.write_event(
        _company_event(
            event_id="second",
            name="Beta Labs",
            entity_id="yc__beta-labs",
            observed_at=CUTOFF - timedelta(days=4),
        )
    )

    replay = ReplayService(database).run(ReplayRequest(cutoff=CUTOFF, max_events=1))

    assert replay.status is ReplayStatus.PARTIAL
    assert replay.counts.eligible_events == 2
    assert replay.counts.replayed_events == 1
    assert replay.counts.companies == 1
    assert replay.counts.rankings == 1
    assert replay.errors[0].code == "max_events_reached"


def test_replay_context_disposes_its_store(tmp_path: Path) -> None:
    database = _database(tmp_path)
    context = ReplayContext(database, ReplayRequest(cutoff=CUTOFF))
    replay_root = context.store_path.parent
    assert context.store_path.exists()
    context.close()
    assert not replay_root.exists()


def test_bounded_local_cli_outputs_auditable_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = _database(tmp_path)
    database.write_event(_company_event())

    exit_code = replay_cli(
        ["--database", str(database._path), "--until", CUTOFF.isoformat(), "--max-events", "1"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "completed"
    assert payload["mode"] == "observed_at"
    assert payload["live_state_mutated"] is False
    assert payload["counts"]["replayed_events"] == 1


def test_legacy_evaluation_boundary_cannot_mutate_through_replay(tmp_path: Path) -> None:
    replayer = BatchReplayer(_database(tmp_path))
    with pytest.raises(RuntimeError, match="deferred beyond Milestone 2"):
        replayer.replay_and_evaluate()


def test_replay_api_is_read_only_and_openapi_typed() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/replay/contract")
        blocked = client.post("/api/v1/replay", json={"cutoff": CUTOFF.isoformat()})
        openapi = client.get("/openapi.json").json()

    assert response.status_code == 200
    contract = response.json()
    assert contract["default_mode"] == "observed_at"
    assert contract["supported_modes"] == ["observed_at", "occurred_at"]
    assert contract["remote_execution_enabled"] is False
    assert "crm" in contract["side_effects_suppressed"]
    assert blocked.status_code in {404, 405}
    schema = openapi["paths"]["/api/v1/replay/contract"]["get"]["responses"]["200"]
    assert schema["content"]["application/json"]["schema"]["$ref"].endswith(
        "ReplayContractResponse"
    )
