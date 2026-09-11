"""Bounded, integrity-gated live and isolated historical graph projections."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Iterable

from venture_intelligence.graph.models import (
    GraphProjectionSource,
    GraphProvenance,
    GraphSnapshotLimits,
    GraphSnapshotResponse,
    TemporalGraphEdge,
    TemporalGraphNode,
)
from venture_intelligence.pipeline.entity_integrity import (
    validate_materialized_identity,
    validated_entity_record,
)
from venture_intelligence.replay.models import ReplayRequest
from venture_intelligence.replay.service import ReplayService
from venture_intelligence.shared.models import (
    GraphEdgeType,
    GraphNodeType,
    TemporalPrecision,
    TemporalStatus,
    TemporalValidity,
)
from venture_intelligence.storage.db import Database

MAX_GRAPH_NODES = 400
MAX_GRAPH_EDGES = 800
MAX_GRAPH_DEPTH = 2
MAX_GRAPH_REPLAY_EVENTS = 5_000
_GRAPH_EDGE_NAMESPACE = uuid.UUID("88fd3490-1509-54a3-8706-9027a122a118")


class GraphService:
    """Project graph JSON without allowing invalid identities or unbounded payloads."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def snapshot(
        self,
        *,
        as_of: datetime | None = None,
        program_id: str | None = None,
        node_types: set[GraphNodeType] | None = None,
        min_confidence: float = 0.0,
        focus_id: str | None = None,
        depth: int = 1,
        max_nodes: int = 200,
        max_edges: int = 400,
    ) -> GraphSnapshotResponse:
        cutoff = self._cutoff(as_of)
        bounded_nodes = max(1, min(max_nodes, MAX_GRAPH_NODES))
        bounded_edges = max(1, min(max_edges, MAX_GRAPH_EDGES))
        bounded_depth = max(0, min(depth, MAX_GRAPH_DEPTH))
        replay_digest: str | None = None
        if as_of is None:
            node_rows, edge_rows = self._live_rows()
            projection_source = GraphProjectionSource.LIVE
            historical = False
        else:
            replay = ReplayService(self._database).run(
                ReplayRequest(cutoff=cutoff, max_events=MAX_GRAPH_REPLAY_EVENTS)
            )
            node_rows = replay.snapshot.graph_nodes
            edge_rows = replay.snapshot.graph_edges
            replay_digest = replay.projection_digest
            projection_source = GraphProjectionSource.ISOLATED_REPLAY
            historical = True

        decoded_nodes = [self._decode(row) for row in node_rows]
        decoded_edges = [self._decode(row) for row in edge_rows]
        safe_nodes = {
            str(row.get("node_id")): row
            for row in decoded_nodes
            if self._safe_node(row, historical=historical)
        }
        safe_edges = [
            row
            for row in decoded_edges
            if row.get("source_id") in safe_nodes
            and row.get("target_id") in safe_nodes
            and self._safe_edge(row, cutoff=cutoff, min_confidence=min_confidence)
        ]

        selected_ids = set(safe_nodes)
        if program_id:
            seeds = {
                node_id
                for node_id, row in safe_nodes.items()
                if str((row.get("properties") or {}).get("program_id") or "") == program_id
            }
            selected_ids = self._expand_ids(seeds, safe_edges, 1)
        if focus_id:
            selected_ids &= self._expand_ids({focus_id}, safe_edges, bounded_depth)
        if node_types:
            allowed_types = {item.value for item in node_types}
            selected_ids = {
                node_id
                for node_id in selected_ids
                if safe_nodes[node_id].get("node_type") in allowed_types
            }

        selected_edges = [
            row
            for row in safe_edges
            if row.get("source_id") in selected_ids and row.get("target_id") in selected_ids
        ]
        degree: dict[str, int] = defaultdict(int)
        for row in selected_edges:
            degree[str(row["source_id"])] += 1
            degree[str(row["target_id"])] += 1
        ordered_ids = sorted(
            selected_ids,
            key=lambda node_id: (
                node_id != focus_id,
                -degree[node_id],
                str(safe_nodes[node_id].get("label") or "").casefold(),
                node_id,
            ),
        )
        total_nodes = len(ordered_ids)
        total_edges = len(selected_edges)
        returned_ids = set(ordered_ids[:bounded_nodes])
        returned_edges = [
            row
            for row in selected_edges
            if row.get("source_id") in returned_ids and row.get("target_id") in returned_ids
        ][:bounded_edges]
        incident: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in returned_edges:
            incident[str(row["source_id"])].append(row)
            incident[str(row["target_id"])].append(row)
        source_urls = self._source_urls(
            evidence_id
            for row in returned_edges
            for evidence_id in self._string_list(row.get("evidence_ids"))
        )
        nodes = [
            self._node_view(safe_nodes[node_id], incident[node_id], source_urls)
            for node_id in ordered_ids[:bounded_nodes]
        ]
        edges = [self._edge_view(row, source_urls) for row in returned_edges]
        return GraphSnapshotResponse(
            as_of=cutoff,
            projection_source=projection_source,
            program_id=program_id,
            focus_id=focus_id,
            depth=bounded_depth,
            nodes=nodes,
            edges=edges,
            total_nodes=total_nodes,
            total_edges=total_edges,
            truncated=total_nodes > len(nodes) or total_edges > len(edges),
            limits=GraphSnapshotLimits(
                max_nodes=bounded_nodes,
                max_edges=bounded_edges,
                max_depth=MAX_GRAPH_DEPTH,
                max_replay_events=MAX_GRAPH_REPLAY_EVENTS,
            ),
            available_node_types=sorted(
                {node.node_type for node in nodes}, key=lambda item: item.value
            ),
            available_edge_types=sorted(
                {edge.edge_type for edge in edges}, key=lambda item: item.value
            ),
            replay_projection_digest=replay_digest,
        )

    @staticmethod
    def _cutoff(value: datetime | None) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of must include a UTC offset")
        return value.astimezone(timezone.utc)

    def _live_rows(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        with self._database._connect() as connection:
            nodes = [dict(row) for row in connection.execute("SELECT * FROM graph_nodes")]
            edges = [dict(row) for row in connection.execute("SELECT * FROM graph_edges")]
        return nodes, edges

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for field in ("properties", "evidence_ids"):
            value = result.get(field)
            if isinstance(value, str):
                try:
                    result[field] = json.loads(value)
                except json.JSONDecodeError:
                    result[field] = {} if field == "properties" else []
        return result

    def _safe_node(self, row: dict[str, Any], *, historical: bool) -> bool:
        node_id = str(row.get("node_id") or "")
        label = str(row.get("label") or "")
        try:
            GraphNodeType(str(row.get("node_type") or ""))
        except ValueError:
            return False
        if not validate_materialized_identity(label, node_id).accepted:
            return False
        if historical or row.get("node_type") not in {
            GraphNodeType.COMPANY.value,
            GraphNodeType.PERSON.value,
        }:
            return True
        entity = self._database.get_company(node_id) or self._database.get_person(node_id)
        return bool(
            entity
            and self._database.is_entity_validated(node_id)
            and validated_entity_record(entity)
        )

    @staticmethod
    def _observed_by(row: dict[str, Any], cutoff: datetime) -> bool:
        value = row.get("observed_at")
        if not value:
            return False
        observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return observed.astimezone(timezone.utc) <= cutoff

    @classmethod
    def _safe_edge(
        cls,
        row: dict[str, Any],
        *,
        cutoff: datetime,
        min_confidence: float,
    ) -> bool:
        try:
            confidence = float(row.get("confidence"))
            GraphEdgeType(str(row.get("edge_type") or ""))
            TemporalPrecision(str(row.get("validity_precision") or "unknown"))
            TemporalStatus(str(row.get("temporal_status") or "unknown"))
            return (
                0 <= confidence <= 1
                and confidence >= min_confidence
                and cls._observed_by(row, cutoff)
            )
        except (TypeError, ValueError, OverflowError):
            return False

    @staticmethod
    def _expand_ids(seeds: set[str], edges: list[dict[str, Any]], depth: int) -> set[str]:
        if not seeds:
            return set()
        adjacency: dict[str, set[str]] = defaultdict(set)
        for row in edges:
            source = str(row["source_id"])
            target = str(row["target_id"])
            adjacency[source].add(target)
            adjacency[target].add(source)
        visited = set(seeds)
        queue = deque((seed, 0) for seed in sorted(seeds))
        while queue:
            node_id, current_depth = queue.popleft()
            if current_depth >= depth:
                continue
            for neighbor in sorted(adjacency[node_id]):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, current_depth + 1))
        return visited

    def _source_urls(self, evidence_ids: Iterable[str]) -> dict[str, str]:
        ids = sorted({item for item in evidence_ids if item})[:500]
        if not ids:
            return {}
        result: dict[str, str] = {}
        with self._database._connect() as connection:
            for offset in range(0, len(ids), 100):
                chunk = ids[offset : offset + 100]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"SELECT event_id, source_url FROM events WHERE event_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                result.update(
                    {
                        str(row["event_id"]): str(row["source_url"])
                        for row in rows
                        if row["source_url"]
                    }
                )
        return result

    def _node_view(
        self,
        row: dict[str, Any],
        incident: list[dict[str, Any]],
        source_urls: dict[str, str],
    ) -> TemporalGraphNode:
        evidence_ids = sorted(
            {item for edge in incident for item in self._string_list(edge.get("evidence_ids"))}
        )[:100]
        observed = sorted(
            datetime.fromisoformat(str(edge["observed_at"]).replace("Z", "+00:00"))
            for edge in incident
            if edge.get("observed_at")
        )
        properties = self._bounded_properties(row.get("properties") or {})
        explicit = properties.get("confidence") or properties.get("identity_confidence")
        confidence_values = [float(edge.get("confidence") or 0) for edge in incident]
        confidence = float(explicit) if isinstance(explicit, (int, float)) else None
        if confidence is None and confidence_values:
            confidence = max(confidence_values)
        return TemporalGraphNode(
            node_id=str(row["node_id"]),
            node_type=GraphNodeType(str(row["node_type"])),
            label=str(row["label"]),
            confidence=confidence,
            program_id=str(properties.get("program_id") or "") or None,
            first_observed_at=observed[0] if observed else None,
            properties=properties,
            provenance=GraphProvenance(
                evidence_ids=evidence_ids,
                source_urls=[source_urls[item] for item in evidence_ids if item in source_urls][
                    :32
                ],
            ),
        )

    def _edge_view(self, row: dict[str, Any], source_urls: dict[str, str]) -> TemporalGraphEdge:
        evidence_ids = self._string_list(row.get("evidence_ids"))[:100]
        precision = TemporalPrecision(str(row.get("validity_precision") or "unknown"))
        status = TemporalStatus(str(row.get("temporal_status") or "unknown"))
        return TemporalGraphEdge(
            edge_id=str(
                row.get("edge_id")
                or uuid.uuid5(
                    _GRAPH_EDGE_NAMESPACE,
                    f"{row['source_id']}|{row['edge_type']}|{row['target_id']}",
                )
            ),
            source_id=str(row["source_id"]),
            target_id=str(row["target_id"]),
            edge_type=GraphEdgeType(str(row["edge_type"])),
            confidence=float(row.get("confidence") or 0),
            observed_at=datetime.fromisoformat(str(row["observed_at"]).replace("Z", "+00:00")),
            validity=TemporalValidity(
                valid_from=row.get("valid_from"),
                valid_to=row.get("valid_to"),
                precision=precision,
                status=status,
            ),
            temporal_status=status,
            validity_precision=precision,
            properties=self._bounded_properties(row.get("properties") or {}),
            provenance=GraphProvenance(
                evidence_ids=evidence_ids,
                source_urls=[source_urls[item] for item in evidence_ids if item in source_urls][
                    :32
                ],
            ),
        )

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        return [str(item) for item in value] if isinstance(value, list) else []

    @staticmethod
    def _bounded_properties(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, Any] = {}
        for key in sorted(value)[:24]:
            item = value[key]
            if isinstance(item, (str, int, float, bool)) or item is None:
                result[str(key)[:80]] = item[:500] if isinstance(item, str) else item
            elif isinstance(item, list):
                result[str(key)[:80]] = [str(entry)[:200] for entry in item[:12]]
        return result
