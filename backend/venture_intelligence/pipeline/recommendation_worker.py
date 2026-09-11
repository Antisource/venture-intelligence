"""
pipeline/recommendation_worker.py — converts rankings into partner actions (§8, §16).

Reads the latest ranking for every entity and decides what a partner should
do next.  The decision is deterministic — same inputs always produce the
same recommendation — so partners can trace any action back to specific signals.

Decision rules (in priority order):

  CONTACT (rank ≤ 20, composite ≥ 0.70, confidence ≥ 0.70)
    → "Reach out now — strong signals across multiple dimensions"

  ESCALATE (rank ≤ 10, composite ≥ 0.80)
    → "Bring to weekly partner meeting immediately"

  MONITOR (rank ≤ 100, composite ≥ 0.40)
    → "Keep watching — signals present but not yet strong enough to act"

  IGNORE (everything else)
    → Entity is tracked but below our investment thesis thresholds

CRM record is created or updated automatically on every recommendation so
the Kanban board stays current without manual partner action.
"""

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone

from venture_intelligence.config import settings
from venture_intelligence.crm.workflow import CRMWorkflow
from venture_intelligence.shared.models import (
    FounderRanking,
    Recommendation,
    RecommendationAction,
    RecommendationLifecycleEvent,
    RecommendationLifecycleStatus,
)
from venture_intelligence.storage.db import Database

logger = logging.getLogger(__name__)
RECOMMENDATION_VERSION = "m5-recommendation-1"
_RECOMMENDATION_NAMESPACE = uuid.UUID("8c863a87-fe5a-51f6-bad3-d13aab7aa279")


class RecommendationWorker:
    """
    Reads rankings, writes Recommendations, and triggers CRM record creation.
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        self._crm = CRMWorkflow(db)

    def generate_all(self) -> list[Recommendation]:
        """
        Generate recommendations for every entity with a current ranking.
        Idempotent — safe to call multiple times per poll cycle.
        """
        rankings = self._db.get_rankings(limit=500)
        recs: list[Recommendation] = []

        for ranking in rankings:
            previous = self._db.get_recommendation_for_entity(ranking.entity_id)
            rec = self.decide(
                ranking,
                supersedes_recommendation_id=(previous.recommendation_id if previous else None),
            )
            self._db.write_recommendation(rec)
            self._record_created(rec)
            if previous and previous.recommendation_id != rec.recommendation_id:
                self._db.write_recommendation_lifecycle(
                    RecommendationLifecycleEvent(
                        lifecycle_event_id=str(
                            uuid.uuid5(
                                _RECOMMENDATION_NAMESPACE,
                                f"superseded:{previous.recommendation_id}:{rec.recommendation_id}",
                            )
                        ),
                        recommendation_id=previous.recommendation_id,
                        entity_id=previous.entity_id,
                        status=RecommendationLifecycleStatus.SUPERSEDED,
                        actor="recommendation_worker",
                        reason=f"Superseded by {rec.recommendation_id}",
                        provenance={"replacement_recommendation_id": rec.recommendation_id},
                        occurred_at=rec.created_at,
                    )
                )
            recs.append(rec)

            # Ensure a CRM record exists — system auto-creates at DISCOVERY stage
            self._crm.ensure_discovery(ranking.entity_id)

        logger.info("recommendations generated: %d", len(recs))
        return recs

    # ── Decision logic ────────────────────────────────────────────────────────

    def decide(
        self,
        ranking: FounderRanking,
        *,
        created_at: datetime | None = None,
        recommendation_id: str | None = None,
        supersedes_recommendation_id: str | None = None,
    ) -> Recommendation:
        """Build a recommendation without persisting it or touching CRM."""

        action, reasoning = self._apply_rules(ranking)

        semantics = {
            "entity_id": ranking.entity_id,
            "ranking_id": ranking.ranking_id,
            "action": action.value,
            "evidence_ids": sorted(ranking.evidence_ids),
            "signal_ids": sorted(ranking.signal_ids),
            "feature_ids": sorted(ranking.feature_ids),
            "crm_action_ids": sorted(ranking.crm_action_ids),
            "recommendation_version": RECOMMENDATION_VERSION,
        }
        digest = hashlib.sha256(
            json.dumps(semantics, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        identity = {
            "recommendation_id": recommendation_id
            or str(uuid.uuid5(_RECOMMENDATION_NAMESPACE, digest))
        }
        return Recommendation(
            **identity,
            recommendation_version=RECOMMENDATION_VERSION,
            entity_id=ranking.entity_id,
            action=action,
            reasoning=reasoning,
            evidence_ids=ranking.evidence_ids,
            ranking_id=ranking.ranking_id,
            signal_ids=ranking.signal_ids,
            feature_ids=ranking.feature_ids,
            crm_action_ids=ranking.crm_action_ids,
            input_digest=digest,
            supersedes_recommendation_id=supersedes_recommendation_id,
            confidence=ranking.confidence,
            created_at=created_at or datetime.now(timezone.utc),
        )

    def _decide(self, ranking: FounderRanking) -> Recommendation:
        """Backward-compatible alias for the pure decision boundary."""
        return self.decide(ranking)

    def _record_created(self, recommendation: Recommendation) -> None:
        self._db.write_recommendation_lifecycle(
            RecommendationLifecycleEvent(
                lifecycle_event_id=str(
                    uuid.uuid5(
                        _RECOMMENDATION_NAMESPACE,
                        f"created:{recommendation.recommendation_id}",
                    )
                ),
                recommendation_id=recommendation.recommendation_id,
                entity_id=recommendation.entity_id,
                status=RecommendationLifecycleStatus.CREATED,
                actor="recommendation_worker",
                provenance={
                    "ranking_id": recommendation.ranking_id,
                    "input_digest": recommendation.input_digest,
                },
                occurred_at=recommendation.created_at,
            )
        )

    def _apply_rules(self, ranking: FounderRanking) -> tuple[RecommendationAction, str]:
        """
        Return (action, reasoning) by evaluating rules top-to-bottom.
        First matching rule wins.
        """
        rank = ranking.rank
        score = ranking.composite_score
        conf = ranking.confidence

        # ESCALATE — top-10 with very strong signals
        if rank <= 10 and score >= 0.80 and conf >= 0.70:
            return (
                RecommendationAction.ESCALATE,
                f"Rank #{rank}, composite {score:.2f} — bring to partner meeting immediately. "
                f"{ranking.reasoning}",
            )

        # CONTACT — top-20 with good signals
        if rank <= 20 and score >= 0.70 and conf >= settings.alert_min_confidence:
            return (
                RecommendationAction.CONTACT,
                f"Rank #{rank}, composite {score:.2f} — strong signals, reach out now. "
                f"{ranking.reasoning}",
            )

        # MONITOR — within top-N and some signal present
        if rank <= settings.alert_top_n_rank and score >= 0.40:
            return (
                RecommendationAction.MONITOR,
                f"Rank #{rank}, composite {score:.2f} — signals present, monitor for developments. "
                f"{ranking.reasoning}",
            )

        # IGNORE — below thresholds
        return (
            RecommendationAction.IGNORE,
            f"Rank #{rank}, composite {score:.2f} — below investment thesis thresholds.",
        )
