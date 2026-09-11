"""Focused regression coverage for the Milestone 1 backend foundation."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from venture_intelligence.api.app import app
from venture_intelligence.config import settings
from venture_intelligence.connectors.factory import build_connectors
from venture_intelligence.pipeline.graph import GraphBuilder
from venture_intelligence.programs import ProgramConfig, ProgramRegistry
from venture_intelligence.research.provider_policy import (
    BUILTIN_PROVIDER_POLICIES,
    CONNECTOR_POLICIES,
    EvidenceRole,
    SourceOperationStatus,
)
from venture_intelligence.research.sources import SearchResponse, SearchResult
from venture_intelligence.shared.models import (
    CanonicalEvent,
    Company,
    EntityType,
    EventType,
    GraphEdge,
    GraphEdgeType,
    Person,
    ResearchSourceStatus,
    Source,
    TemporalPrecision,
    TemporalStatus,
    TemporalValidity,
)
from venture_intelligence.storage.db import Database


def _program_registry() -> ProgramRegistry:
    return ProgramRegistry(
        [
            ProgramConfig(
                id="test_program",
                name="Test Accelerator",
                batch="T01",
                detection_strategy="directory",
                directory_url="https://accelerator.test/companies",
                base_confidence=0.9,
                keywords=["Test Accelerator"],
                entity_id_prefix="test",
            )
        ]
    )


def _company_event(
    *,
    occurred_at: datetime,
    observed_at: datetime,
    validity: TemporalValidity | None = None,
) -> CanonicalEvent:
    return CanonicalEvent(
        event_type=EventType.COMPANY_LISTED,
        entity_type=EntityType.COMPANY,
        entity_id="test__acme-labs",
        source=Source.YC_WEBSITE,
        source_url="https://accelerator.test/companies/acme-labs",
        occurred_at=occurred_at,
        occurred_at_precision=TemporalPrecision.EXACT,
        observed_at=observed_at,
        ingested_at=observed_at + timedelta(seconds=1),
        validity=validity,
        confidence=0.91,
        raw_payload={"name": "Acme Labs", "batch": "T01"},
        metadata={"program_id": "test_program"},
    )


def test_command_search_returns_programs_and_only_validated_entities(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "search.db"
    monkeypatch.setattr(settings, "db_path", str(database_path))
    database = Database(str(database_path))
    database.write_person(
        Person(
            person_id="person__ada-lovelace",
            canonical_name="Ada Lovelace",
            identity_confidence=0.94,
        )
    )
    database.write_entity_validation(
        "person__ada-lovelace",
        "person",
        evidence_count=2,
    )
    database.write_company(Company(company_id="company__twitter", canonical_name="Twitter"))
    database.write_entity_validation("company__twitter", "company", evidence_count=2)
    database.write_company(Company(company_id="company__unverified", canonical_name="Ada Systems"))

    with TestClient(app) as client:
        program_response = client.get("/api/v1/search", params={"q": "YC"})
        entity_response = client.get("/api/v1/search", params={"q": "Ada"})
        generic_response = client.get("/api/v1/search", params={"q": "Twitter"})

    assert program_response.status_code == 200
    assert any(item["kind"] == "program" for item in program_response.json())
    assert entity_response.status_code == 200
    assert [item["id"] for item in entity_response.json()] == ["person__ada-lovelace"]
    assert generic_response.status_code == 200
    assert generic_response.json() == []


def test_fresh_database_has_temporal_foundation(tmp_path: Path) -> None:
    database = Database(str(tmp_path / "fresh.db"))
    with database._connect() as connection:
        event_columns = {row["name"] for row in connection.execute("PRAGMA table_info(events)")}
        edge_columns = {row["name"] for row in connection.execute("PRAGMA table_info(graph_edges)")}
        migration = connection.execute(
            "SELECT version FROM schema_migrations WHERE version=?",
            ("0001_temporal_foundation",),
        ).fetchone()
    assert {"occurred_at_precision", "validity"} <= event_columns
    assert {"validity_precision", "temporal_status"} <= edge_columns
    assert migration["version"] == "0001_temporal_foundation"


def test_previous_schema_is_migrated_additively(tmp_path: Path) -> None:
    database_path = tmp_path / "previous.db"
    now = "2026-01-01T00:00:00+00:00"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL,
                event_type TEXT NOT NULL, entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL, source TEXT NOT NULL,
                source_url TEXT NOT NULL, occurred_at TEXT NOT NULL,
                observed_at TEXT NOT NULL, ingested_at TEXT NOT NULL,
                processed_at TEXT, ranked_at TEXT, notified_at TEXT,
                confidence REAL NOT NULL, raw_payload TEXT NOT NULL,
                metadata TEXT NOT NULL
            );
            CREATE TABLE graph_edges (
                edge_id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
                target_id TEXT NOT NULL, edge_type TEXT NOT NULL,
                confidence REAL NOT NULL, observed_at TEXT NOT NULL,
                valid_from TEXT, valid_to TEXT, evidence_ids TEXT NOT NULL,
                properties TEXT NOT NULL,
                UNIQUE(source_id, target_id, edge_type)
            );
            """
        )
        connection.execute(
            """
            INSERT INTO events
              (event_id, schema_version, event_type, entity_type, entity_id,
               source, source_url, occurred_at, observed_at, ingested_at,
               confidence, raw_payload, metadata)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "legacy-event",
                "1.0",
                "repo_created",
                "repository",
                "github__acme__repo",
                "github",
                "https://github.com/acme/repo",
                now,
                now,
                now,
                0.8,
                "{}",
                "{}",
            ),
        )

    database = Database(str(database_path))
    with database._connect() as connection:
        row = connection.execute(
            """
            SELECT event_id, schema_version, occurred_at_precision, validity
            FROM events WHERE event_id=?
            """,
            ("legacy-event",),
        ).fetchone()
        edge_columns = {
            item["name"] for item in connection.execute("PRAGMA table_info(graph_edges)")
        }
        migrations = connection.execute("SELECT version FROM schema_migrations").fetchall()

    assert dict(row) == {
        "event_id": "legacy-event",
        "schema_version": "1.0",
        "occurred_at_precision": "unknown",
        "validity": None,
    }
    assert {"validity_precision", "temporal_status"} <= edge_columns
    assert [item["version"] for item in migrations] == [
        "0001_temporal_foundation",
        "0002_historical_evaluation",
        "0003_feature_lineage",
        "0004_crm_feedback",
        "0005_batch_discovery",
    ]


def test_graph_observation_time_does_not_invent_validity(tmp_path: Path) -> None:
    database = Database(str(tmp_path / "graph.db"))
    occurred_at = datetime(2025, 12, 1, tzinfo=timezone.utc)
    observed_at = datetime(2026, 1, 15, tzinfo=timezone.utc)
    GraphBuilder(database, _program_registry()).process(
        _company_event(occurred_at=occurred_at, observed_at=observed_at),
        "company__acme-labs",
    )

    with database._connect() as connection:
        edge = connection.execute(
            "SELECT * FROM graph_edges WHERE source_id=?",
            ("company__acme-labs",),
        ).fetchone()

    assert edge["observed_at"] == observed_at.isoformat()
    assert edge["observed_at"] != occurred_at.isoformat()
    assert edge["valid_from"] is None
    assert edge["valid_to"] is None
    assert edge["validity_precision"] == "unknown"
    assert edge["temporal_status"] == "unknown"


def test_graph_temporal_precision_and_status_persist(tmp_path: Path) -> None:
    database = Database(str(tmp_path / "edge.db"))
    observed_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
    valid_from = datetime(2025, 6, 1, tzinfo=timezone.utc)
    valid_to = datetime(2025, 12, 31, tzinfo=timezone.utc)
    database.write_graph_edge(
        GraphEdge(
            source_id="person__ada",
            target_id="company__acme",
            edge_type=GraphEdgeType.FOUNDED,
            observed_at=observed_at,
            valid_from=valid_from,
            valid_to=valid_to,
            validity_precision=TemporalPrecision.DAY,
            temporal_status=TemporalStatus.HISTORICAL,
            evidence_ids=["evidence-1"],
        )
    )

    with database._connect() as connection:
        edge = connection.execute(
            "SELECT * FROM graph_edges WHERE source_id=?",
            ("person__ada",),
        ).fetchone()

    assert edge["observed_at"] == observed_at.isoformat()
    assert edge["valid_from"] == valid_from.isoformat()
    assert edge["valid_to"] == valid_to.isoformat()
    assert edge["validity_precision"] == "day"
    assert edge["temporal_status"] == "historical"


@pytest.mark.parametrize(
    ("legacy_status", "has_results", "error", "expected"),
    [
        (ResearchSourceStatus.AVAILABLE, True, None, SourceOperationStatus.SUCCESS),
        (ResearchSourceStatus.AVAILABLE, False, None, SourceOperationStatus.EMPTY),
        (
            ResearchSourceStatus.BUDGET_EXHAUSTED,
            True,
            "budget exhausted",
            SourceOperationStatus.PARTIAL,
        ),
        (
            ResearchSourceStatus.BUDGET_EXHAUSTED,
            False,
            "budget exhausted",
            SourceOperationStatus.BUDGET_EXHAUSTED,
        ),
        (
            ResearchSourceStatus.FAILED,
            False,
            "request timed out",
            SourceOperationStatus.TIMEOUT,
        ),
    ],
)
def test_search_response_normalizes_source_operation_status(
    legacy_status: ResearchSourceStatus,
    has_results: bool,
    error: str | None,
    expected: SourceOperationStatus,
) -> None:
    results = [SearchResult(url="https://example.test", title="Evidence")] if has_results else []
    response = SearchResponse(
        query="acme",
        status=legacy_status,
        results=results,
        retrieved_at="2026-01-01T00:00:00+00:00",
        error=error,
    )
    assert response.operation_status is expected


def test_source_policy_and_temporal_contract_endpoints() -> None:
    with TestClient(app) as client:
        sources_response = client.get("/api/v1/sources")
        temporal_response = client.get("/api/v1/system/temporal-contract")

    assert sources_response.status_code == 200
    sources = sources_response.json()
    assert sources["direct_social_access_enabled"] is False
    policies = {item["provider_id"]: item for item in sources["providers"]}
    connectors = {item["provider_id"]: item for item in sources["connectors"]}
    inventory = {item["connector"]: item for item in sources["inventory"]}
    assert policies["arxiv"]["access_method"] == "public_api"
    assert policies["public_program_osint"]["evidence_role"] == "discovery"
    assert set(policies["public_program_osint"]["restricted_hosts"]) >= {
        "linkedin.com",
        "x.com",
        "instagram.com",
    }
    assert connectors["linkedin"]["access_mode"] == "permission_required"
    assert connectors["linkedin"]["default_enabled"] is False
    assert connectors["social"]["evidence_role"] == "discovery"
    assert set(connectors["social"]["restricted_hosts"]) >= {
        "linkedin.com",
        "x.com",
        "instagram.com",
    }
    assert inventory["product_hunt"]["access_mode"] == "AUTHORIZED_OFFICIAL_API"
    assert inventory["product_hunt"]["configuration_state"] in {
        "CONFIGURED",
        "USER_CONFIGURATION_REQUIRED",
    }
    assert inventory["discord"]["access_mode"] == "PUBLIC_EMBED"
    assert inventory["social"]["access_mode"] == "RESTRICTED_DIRECT_ACCESS_DISABLED"

    assert temporal_response.status_code == 200
    temporal = temporal_response.json()
    assert temporal["default_historical_cutoff_field"] == "observed_at"
    assert temporal["replay_available"] is True
    assert {item["field"] for item in temporal["fields"]} == {
        "occurred_at",
        "observed_at",
        "ingested_at",
        "validity",
    }


def test_every_registered_provider_and_connector_has_explicit_policy() -> None:
    provider_ids = [item.provider_id for item in BUILTIN_PROVIDER_POLICIES]
    assert len(provider_ids) == len(set(provider_ids))
    assert all(item.capabilities for item in BUILTIN_PROVIDER_POLICIES)
    assert all(item.evidence_role in EvidenceRole for item in BUILTIN_PROVIDER_POLICIES)

    registry = _program_registry()
    connectors = build_connectors(registry)
    try:
        connector_names = {connector.source_name for connector in connectors}
    finally:
        for connector in connectors:
            crawler = getattr(connector, "crawler", None)
            client = getattr(crawler, "_client", None)
            if client is not None:
                client.close()
    connector_policies = {item.provider_id: item for item in CONNECTOR_POLICIES}
    assert connector_names == set(connector_policies)
    assert all(item.capabilities for item in connector_policies.values())
    assert all(item.evidence_role in EvidenceRole for item in connector_policies.values())


def test_research_run_source_status_is_normalized_in_openapi() -> None:
    schema = app.openapi()
    research_source = schema["components"]["schemas"]["ResearchSource"]
    normalized = research_source["properties"]["normalized_status"]
    assert normalized["$ref"].endswith("/SourceOperationStatus")
    assert "normalized_status" in research_source["required"]

    source_response = schema["components"]["schemas"]["SourcePolicyResponse"]
    assert {"providers", "connectors", "inventory"} <= set(source_response["required"])
