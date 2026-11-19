"""Knowledge graph node and edge persistence."""

import json
from datetime import datetime, timezone
from typing import Any

from venture_intelligence.shared.models import (
    GraphEdge,
    GraphNode,
)


class GraphMixin:
    # ════════════════════════════════════════════════════════════════════════════
    # KNOWLEDGE GRAPH
    # ════════════════════════════════════════════════════════════════════════════

    def write_graph_node(self, node: GraphNode) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO graph_nodes
                  (node_id, node_type, label, properties, created_at, updated_at)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    node.node_id,
                    node.node_type,
                    node.label,
                    json.dumps(node.properties),
                    self._ts(node.created_at),
                    now,
                ),
            )

    def write_graph_edge(self, edge: GraphEdge) -> None:
        """
        Upsert an edge.  Re-observed relationships update confidence
        and evidence_ids rather than creating duplicates.
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO graph_edges
                  (edge_id, source_id, target_id, edge_type, confidence,
                   observed_at, valid_from, valid_to, validity_precision,
                   temporal_status, evidence_ids, properties)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_id, target_id, edge_type) DO UPDATE SET
                  confidence   = MAX(confidence, excluded.confidence),
                  observed_at  = excluded.observed_at,
                  valid_from   = COALESCE(excluded.valid_from, graph_edges.valid_from),
                  valid_to     = COALESCE(excluded.valid_to, graph_edges.valid_to),
                  validity_precision = CASE
                    WHEN excluded.validity_precision = 'unknown'
                    THEN graph_edges.validity_precision
                    ELSE excluded.validity_precision
                  END,
                  temporal_status = CASE
                    WHEN excluded.temporal_status = 'unknown'
                    THEN graph_edges.temporal_status
                    ELSE excluded.temporal_status
                  END,
                  evidence_ids = excluded.evidence_ids
                """,
                (
                    edge.edge_id,
                    edge.source_id,
                    edge.target_id,
                    edge.edge_type,
                    edge.confidence,
                    self._ts(edge.observed_at),
                    self._ts(edge.valid_from),
                    self._ts(edge.valid_to),
                    edge.validity_precision,
                    edge.temporal_status,
                    json.dumps(edge.evidence_ids),
                    json.dumps(edge.properties),
                ),
            )

    def get_node_neighbors(
        self, node_id: str, edge_type: str | None = None
    ) -> list[dict[str, Any]]:
        """Return all neighbors of a node, optionally filtered by edge type."""
        with self._connect() as conn:
            if edge_type:
                rows = conn.execute(
                    """
                    SELECT n.*, e.edge_id, e.edge_type,
                           e.confidence as edge_confidence, e.observed_at,
                           e.valid_from, e.valid_to, e.validity_precision,
                           e.temporal_status, e.evidence_ids
                    FROM graph_nodes n
                    JOIN graph_edges e ON e.target_id = n.node_id
                    WHERE e.source_id=? AND e.edge_type=?
                    """,
                    (node_id, edge_type),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT n.*, e.edge_id, e.edge_type,
                           e.confidence as edge_confidence, e.observed_at,
                           e.valid_from, e.valid_to, e.validity_precision,
                           e.temporal_status, e.evidence_ids
                    FROM graph_nodes n
                    JOIN graph_edges e ON e.target_id = n.node_id
                    WHERE e.source_id=?
                    """,
                    (node_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    def has_edge(self, source_id: str, target_id: str, edge_type: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM graph_edges WHERE source_id=? AND target_id=? AND edge_type=?",
                (source_id, target_id, edge_type),
            ).fetchone()
        return row is not None
