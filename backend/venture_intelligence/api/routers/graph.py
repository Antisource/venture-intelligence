"""Temporal graph snapshot and legacy graph neighbourhood endpoints."""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from venture_intelligence.api.routers.dependencies import _get_db
from venture_intelligence.config import settings
from venture_intelligence.graph.models import GraphSnapshotResponse
from venture_intelligence.graph.service import GraphService
from venture_intelligence.pipeline.entity_integrity import is_generic_label, validated_entity_record
from venture_intelligence.programs import get_program_registry
from venture_intelligence.shared.models import GraphNodeType
from venture_intelligence.storage.db import Database

router = APIRouter(prefix="/api/v1", tags=["v1"])
legacy_router = APIRouter()


@router.get("/graph", response_model=GraphSnapshotResponse)
async def temporal_graph(
    as_of: datetime | None = None,
    program_id: str | None = None,
    node_types: str | None = None,
    min_confidence: float = Query(default=0.0, ge=0, le=1),
    focus_id: str | None = Query(default=None, max_length=200),
    depth: int = Query(default=1, ge=0, le=2),
    max_nodes: int = Query(default=200, ge=1, le=400),
    max_edges: int = Query(default=400, ge=1, le=800),
) -> GraphSnapshotResponse:
    """Return a bounded integrity-gated live or isolated historical graph."""
    if program_id and get_program_registry().get(program_id) is None:
        raise HTTPException(404, f"Unknown program id: {program_id}")
    parsed_types: set[GraphNodeType] | None = None
    if node_types:
        try:
            parsed_types = {
                GraphNodeType(item.strip()) for item in node_types.split(",") if item.strip()
            }
        except ValueError as error:
            raise HTTPException(
                422, "node_types contains an unsupported graph node type"
            ) from error
    try:
        return GraphService(Database(settings.db_path)).snapshot(
            as_of=as_of,
            program_id=program_id,
            node_types=parsed_types,
            min_confidence=min_confidence,
            focus_id=focus_id,
            depth=depth,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@legacy_router.get("/api/graph/{entity_id}")
async def graph_neighbors(entity_id: str) -> dict:
    """Return the bounded first-hop graph neighborhood for an entity."""
    return {"entity_id": entity_id, "neighbors": _get_db().get_node_neighbors(entity_id)}


@legacy_router.get("/api/graph")
async def graph_snapshot(limit: int = 100) -> dict:
    """Return a bounded graph snapshot for legacy dashboard consumers."""
    db = _get_db()
    bounded = max(1, min(limit, 500))
    with db._connect() as connection:
        nodes = connection.execute(
            "SELECT * FROM graph_nodes ORDER BY updated_at DESC LIMIT ?", (bounded,)
        ).fetchall()
        edges = connection.execute(
            "SELECT * FROM graph_edges ORDER BY observed_at DESC LIMIT ?", (bounded,)
        ).fetchall()
    safe_nodes = [
        _decode_graph_row(dict(row)) for row in nodes if _is_safe_graph_node(dict(row), db)
    ]
    node_ids = {item["node_id"] for item in safe_nodes}
    return {
        "nodes": safe_nodes,
        "edges": [
            _decode_graph_row(dict(row))
            for row in edges
            if row["source_id"] in node_ids and row["target_id"] in node_ids
        ],
    }


def _is_safe_graph_node(node: dict, db: Database) -> bool:
    label = str(node.get("label") or "")
    node_type = str(node.get("node_type") or "")
    if is_generic_label(label):
        return False
    if node_type in {"Company", "Person"}:
        entity = db.get_company(node["node_id"]) or db.get_person(node["node_id"])
        return bool(
            entity and db.is_entity_validated(node["node_id"]) and validated_entity_record(entity)
        )
    return node_type in {"Accelerator", "Community"}


def _decode_graph_row(row: dict) -> dict:
    for field in ("properties", "evidence_ids"):
        value = row.get(field)
        if isinstance(value, str):
            try:
                row[field] = json.loads(value)
            except json.JSONDecodeError:
                pass
    return row
