from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from venture_intelligence.api.app import app
from venture_intelligence.config import settings
from venture_intelligence.graph.models import GraphProjectionSource
from venture_intelligence.graph.service import GraphService
from venture_intelligence.shared.models import (
    CanonicalEvent,
    Company,
    EntityType,
    EventType,
    GraphEdge,
    GraphEdgeType,
    GraphNode,
    GraphNodeType,
    Source,
    TemporalPrecision,
    TemporalStatus,
)
from venture_intelligence.storage.db import Database

UTC = timezone.utc
CUTOFF = datetime(2026, 1, 15, 12, tzinfo=UTC)


def _listing(name: str, event_id: str, observed_at: datetime) -> CanonicalEvent:
    slug = name.casefold().replace(" ", "-")
    return CanonicalEvent(
        event_id=event_id,
        event_type=EventType.COMPANY_LISTED,
        entity_type=EntityType.COMPANY,
        entity_id=f"yc__{slug}",
        source=Source.DIRECTORY,
        source_url=f"https://www.ycombinator.com/companies/{slug}",
        occurred_at=observed_at - timedelta(days=1),
        observed_at=observed_at,
        ingested_at=observed_at + timedelta(days=30),
        confidence=0.96,
        raw_payload={"name": name, "slug": slug, "batch": "S26"},
        metadata={"program_id": "yc_current", "batch": "S26"},
    )


def _live_graph(database: Database) -> None:
    database.write_company(
        Company(
            company_id="company__acme",
            canonical_name="Acme Labs",
            website="https://acme.example",
            batch="S26",
            identity_confidence=0.96,
            properties={"program_id": "yc_current"},
        )
    )
    database.write_entity_validation("company__acme", "company", evidence_count=1)
    database.write_event(_listing("Acme Labs", "event-acme", CUTOFF))
    database.write_graph_node(
        GraphNode(
            node_id="company__acme",
            node_type=GraphNodeType.COMPANY,
            label="Acme Labs",
            properties={"program_id": "yc_current", "identity_confidence": 0.96},
        )
    )
    database.write_graph_node(
        GraphNode(
            node_id="accelerator__yc",
            node_type=GraphNodeType.ACCELERATOR,
            label="Y Combinator",
            properties={"program_id": "yc_current"},
        )
    )
    database.write_graph_edge(
        GraphEdge(
            edge_id="edge-acme-yc",
            source_id="company__acme",
            target_id="accelerator__yc",
            edge_type=GraphEdgeType.ACCEPTED_BY,
            confidence=0.96,
            observed_at=CUTOFF,
            valid_from=CUTOFF - timedelta(days=15),
            validity_precision=TemporalPrecision.EXACT,
            temporal_status=TemporalStatus.CURRENT,
            evidence_ids=["event-acme"],
        )
    )
    with database._connect() as connection:
        connection.execute(
            """
            INSERT INTO graph_nodes
              (node_id, node_type, label, properties, created_at, updated_at)
            VALUES (?,?,?,?,?,?)
            """,
            (
                "github__twitter",
                "Company",
                "Twitter",
                "{}",
                CUTOFF.isoformat(),
                CUTOFF.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO graph_edges
              (edge_id, source_id, target_id, edge_type, confidence, observed_at,
               valid_from, valid_to, validity_precision, temporal_status,
               evidence_ids, properties)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "malformed-edge",
                "company__acme",
                "accelerator__yc",
                "NOT_A_RELATIONSHIP",
                "unbounded",
                "not-a-timestamp",
                None,
                None,
                "invented",
                "current",
                "[]",
                "{}",
            ),
        )


def _projection_counts(database: Database) -> tuple[int, int, int]:
    with database._connect() as connection:
        return tuple(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("companies", "graph_nodes", "graph_edges")
        )


def test_live_graph_contract_is_bounded_typed_and_integrity_gated(tmp_path: Path) -> None:
    database = Database(str(tmp_path / "graph.db"))
    _live_graph(database)

    snapshot = GraphService(database).snapshot(max_nodes=1, max_edges=5)

    assert snapshot.schema_version == "7.0"
    assert snapshot.projection_source is GraphProjectionSource.LIVE
    assert snapshot.historical_cutoff_field == "observed_at"
    assert snapshot.total_nodes == 2
    assert snapshot.truncated is True
    assert all(node.node_id != "github__twitter" for node in snapshot.nodes)
    assert snapshot.limits.max_nodes == 1

    complete = GraphService(database).snapshot(program_id="yc_current")
    assert {node.label for node in complete.nodes} == {"Acme Labs", "Y Combinator"}
    assert complete.edges[0].validity.precision is TemporalPrecision.EXACT
    assert complete.edges[0].temporal_status is TemporalStatus.CURRENT
    assert complete.edges[0].provenance.evidence_ids == ["event-acme"]
    assert complete.edges[0].provenance.source_urls == [
        "https://www.ycombinator.com/companies/acme-labs"
    ]
    assert all(edge.edge_id != "malformed-edge" for edge in complete.edges)


def test_historical_graph_uses_isolated_observed_at_replay(tmp_path: Path) -> None:
    database = Database(str(tmp_path / "history.db"))
    database.write_event(_listing("Early Labs", "early", CUTOFF - timedelta(days=2)))
    database.write_event(_listing("Late Labs", "late", CUTOFF + timedelta(days=2)))
    database.write_event(_listing("Twitter", "generic", CUTOFF - timedelta(days=3)))
    before = _projection_counts(database)

    snapshot = GraphService(database).snapshot(as_of=CUTOFF)

    assert snapshot.projection_source is GraphProjectionSource.ISOLATED_REPLAY
    assert snapshot.replay_projection_digest
    assert any(node.label == "Early Labs" for node in snapshot.nodes)
    assert all(node.label != "Late Labs" for node in snapshot.nodes)
    assert all(node.label.casefold() != "twitter" for node in snapshot.nodes)
    assert _projection_counts(database) == before


def test_versioned_graph_api_validates_bounds_and_program(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "api-graph.db"
    database = Database(str(path))
    _live_graph(database)
    monkeypatch.setattr(settings, "db_path", str(path))

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/graph",
            params={"program_id": "yc_current", "max_nodes": 20, "max_edges": 30},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["schema_version"] == "7.0"
        assert payload["projection_source"] == "live"
        assert payload["nodes"]
        assert client.get("/api/v1/graph", params={"max_nodes": 401}).status_code == 422
        assert (
            client.get("/api/v1/graph", params={"program_id": "not-configured"}).status_code == 404
        )
