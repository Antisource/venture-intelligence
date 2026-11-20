"""Ranking and recommendation persistence, including recommendation lifecycle."""

import json
import sqlite3
from datetime import datetime

from venture_intelligence.shared.models import (
    FounderRanking,
    Recommendation,
    RecommendationLifecycleEvent,
)


class RankingsMixin:
    # ════════════════════════════════════════════════════════════════════════════
    # RANKINGS
    # ════════════════════════════════════════════════════════════════════════════

    def write_ranking(self, ranking: FounderRanking) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO rankings
                  (ranking_id, entity_id, rank, composite_score, dimensions,
                   hypothesis_ids, evidence_ids, signal_ids, feature_ids,
                   crm_action_ids, ranking_version, input_digest, reasoning,
                   confidence, ranked_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    ranking.ranking_id,
                    ranking.entity_id,
                    ranking.rank,
                    ranking.composite_score,
                    json.dumps([d.model_dump() for d in ranking.dimensions]),
                    json.dumps(ranking.hypothesis_ids),
                    json.dumps(ranking.evidence_ids),
                    json.dumps(ranking.signal_ids),
                    json.dumps(ranking.feature_ids),
                    json.dumps(ranking.crm_action_ids),
                    ranking.ranking_version,
                    ranking.input_digest,
                    ranking.reasoning,
                    ranking.confidence,
                    self._ts(ranking.ranked_at),
                ),
            )

    def get_rankings(
        self,
        limit: int = 100,
        *,
        as_of: datetime | None = None,
    ) -> list[FounderRanking]:
        """Return top-N entities ordered by composite_score descending."""
        with self._connect() as conn:
            cutoff_clause = "" if as_of is None else "WHERE ranked_at<=?"
            params: tuple[object, ...] = () if as_of is None else (self._ts(as_of),)
            rows = conn.execute(
                f"""
                SELECT * FROM (
                    SELECT r.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY entity_id
                               ORDER BY ranked_at DESC, ranking_id DESC
                           ) AS latest_ordinal
                    FROM rankings r {cutoff_clause}
                ) latest
                WHERE latest_ordinal=1
                ORDER BY composite_score DESC, entity_id ASC LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return [self._row_to_ranking(r) for r in rows]

    def get_ranking_for_entity(
        self,
        entity_id: str,
        *,
        as_of: datetime | None = None,
    ) -> FounderRanking | None:
        with self._connect() as conn:
            if as_of is None:
                row = conn.execute(
                    "SELECT * FROM rankings WHERE entity_id=? ORDER BY ranked_at DESC LIMIT 1",
                    (entity_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM rankings WHERE entity_id=? AND ranked_at<=? "
                    "ORDER BY ranked_at DESC LIMIT 1",
                    (entity_id, self._ts(as_of)),
                ).fetchone()
        return self._row_to_ranking(row) if row else None

    def get_ranking_history(
        self,
        entity_id: str,
        *,
        as_of: datetime | None = None,
        limit: int = 20,
    ) -> list[FounderRanking]:
        with self._connect() as conn:
            if as_of is None:
                rows = conn.execute(
                    "SELECT * FROM rankings WHERE entity_id=? "
                    "ORDER BY ranked_at DESC, ranking_id LIMIT ?",
                    (entity_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM rankings WHERE entity_id=? AND ranked_at<=? "
                    "ORDER BY ranked_at DESC, ranking_id LIMIT ?",
                    (entity_id, self._ts(as_of), limit),
                ).fetchall()
        return [self._row_to_ranking(row) for row in rows]

    def _row_to_ranking(self, row: sqlite3.Row) -> FounderRanking:
        from venture_intelligence.shared.models import RankingDimension

        return FounderRanking(
            ranking_id=row["ranking_id"],
            entity_id=row["entity_id"],
            rank=row["rank"],
            composite_score=row["composite_score"],
            dimensions=[RankingDimension(**d) for d in json.loads(row["dimensions"])],
            hypothesis_ids=json.loads(row["hypothesis_ids"]),
            evidence_ids=json.loads(row["evidence_ids"]),
            signal_ids=json.loads(row["signal_ids"]),
            feature_ids=json.loads(row["feature_ids"]),
            crm_action_ids=json.loads(row["crm_action_ids"]),
            ranking_version=row["ranking_version"],
            input_digest=row["input_digest"],
            reasoning=row["reasoning"],
            confidence=row["confidence"],
            ranked_at=self._from_ts(row["ranked_at"]),
        )

    # ════════════════════════════════════════════════════════════════════════════
    # RECOMMENDATIONS + ALERTS
    # ════════════════════════════════════════════════════════════════════════════

    def write_recommendation(self, rec: Recommendation) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO recommendations
                  (recommendation_id, schema_version, recommendation_version,
                   entity_id, action, reasoning, evidence_ids, ranking_id,
                   signal_ids, feature_ids, crm_action_ids, input_digest,
                   supersedes_recommendation_id, confidence, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    rec.recommendation_id,
                    rec.schema_version,
                    rec.recommendation_version,
                    rec.entity_id,
                    rec.action,
                    rec.reasoning,
                    json.dumps(rec.evidence_ids),
                    rec.ranking_id,
                    json.dumps(rec.signal_ids),
                    json.dumps(rec.feature_ids),
                    json.dumps(rec.crm_action_ids),
                    rec.input_digest,
                    rec.supersedes_recommendation_id,
                    rec.confidence,
                    self._ts(rec.created_at),
                ),
            )

    def get_recommendation_for_entity(
        self,
        entity_id: str,
        *,
        as_of: datetime | None = None,
    ) -> Recommendation | None:
        with self._connect() as conn:
            if as_of is None:
                row = conn.execute(
                    "SELECT * FROM recommendations WHERE entity_id=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (entity_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM recommendations WHERE entity_id=? AND created_at<=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (entity_id, self._ts(as_of)),
                ).fetchone()
        if row is None:
            return None
        return self._row_to_recommendation(row)

    def get_recommendation(self, recommendation_id: str) -> Recommendation | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM recommendations WHERE recommendation_id=?",
                (recommendation_id,),
            ).fetchone()
        return self._row_to_recommendation(row) if row is not None else None

    def get_recommendations_for_entity(
        self,
        entity_id: str,
        *,
        as_of: datetime | None = None,
    ) -> list[Recommendation]:
        with self._connect() as conn:
            if as_of is None:
                rows = conn.execute(
                    "SELECT * FROM recommendations WHERE entity_id=? "
                    "ORDER BY created_at DESC, recommendation_id",
                    (entity_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM recommendations WHERE entity_id=? AND created_at<=? "
                    "ORDER BY created_at DESC, recommendation_id",
                    (entity_id, self._ts(as_of)),
                ).fetchall()
        return [self._row_to_recommendation(row) for row in rows]

    def _row_to_recommendation(self, row: sqlite3.Row) -> Recommendation:
        return Recommendation(
            recommendation_id=row["recommendation_id"],
            schema_version=row["schema_version"],
            recommendation_version=row["recommendation_version"],
            entity_id=row["entity_id"],
            action=row["action"],
            reasoning=row["reasoning"],
            evidence_ids=json.loads(row["evidence_ids"]),
            ranking_id=row["ranking_id"],
            signal_ids=json.loads(row["signal_ids"]),
            feature_ids=json.loads(row["feature_ids"]),
            crm_action_ids=json.loads(row["crm_action_ids"]),
            input_digest=row["input_digest"],
            supersedes_recommendation_id=row["supersedes_recommendation_id"],
            confidence=row["confidence"],
            created_at=self._from_ts(row["created_at"]),
        )

    def write_recommendation_lifecycle(self, event: RecommendationLifecycleEvent) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO recommendation_lifecycle_events
                  (lifecycle_event_id, recommendation_id, entity_id, status,
                   actor, reason, provenance, occurred_at)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    event.lifecycle_event_id,
                    event.recommendation_id,
                    event.entity_id,
                    event.status,
                    event.actor,
                    event.reason,
                    json.dumps(event.provenance, sort_keys=True),
                    self._ts(event.occurred_at),
                ),
            )

    def get_recommendation_lifecycle(
        self,
        recommendation_id: str,
        *,
        as_of: datetime | None = None,
    ) -> list[RecommendationLifecycleEvent]:
        query = "SELECT * FROM recommendation_lifecycle_events WHERE recommendation_id=?"
        params: list[object] = [recommendation_id]
        if as_of is not None:
            query += " AND occurred_at<=?"
            params.append(self._ts(as_of))
        query += " ORDER BY occurred_at, lifecycle_event_id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            RecommendationLifecycleEvent(
                lifecycle_event_id=row["lifecycle_event_id"],
                recommendation_id=row["recommendation_id"],
                entity_id=row["entity_id"],
                status=row["status"],
                actor=row["actor"],
                reason=row["reason"],
                provenance=json.loads(row["provenance"]),
                occurred_at=self._from_ts(row["occurred_at"]),
            )
            for row in rows
        ]
