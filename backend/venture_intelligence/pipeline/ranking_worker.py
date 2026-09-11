"""
pipeline/ranking_worker.py — multi-dimensional founder ranking engine (§18).

Takes signals computed by SignalWorker and maps them onto the 9 ranking
dimensions defined in §18.  Produces a FounderRanking per entity, then
assigns a global rank (1 = best) ordered by composite_score.

Signal → dimension mapping:
  program_affinity → strategic_fit, timing
  technical_depth  → technical_depth
  research_founder → research_output
  hiring_velocity  → hiring_velocity
  open_source_leader → community_reputation, technical_depth (averaged)
  repeat_founder   → founder_experience
  stealth_startup  → timing (pre-announcement bonus)

Dimensions without a direct signal default to 0.0.
Partners can tune DIMENSION_WEIGHTS in models.py to reflect MFC's thesis.
"""

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone

from venture_intelligence.pipeline.entity_integrity import validated_entity_record
from venture_intelligence.shared.models import (
    DIMENSION_WEIGHTS,
    FounderRanking,
    RankingDimension,
    Signal,
    SignalType,
)
from venture_intelligence.storage.db import Database

logger = logging.getLogger(__name__)

RANKING_VERSION = "m5-ranking-1"
_RANKING_NAMESPACE = uuid.UUID("d91ba55b-fcb2-5997-b84f-a0a3e47c75d6")


class RankingWorker:
    """Computes and persists FounderRanking for every tracked entity."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def rank_all(
        self,
        *,
        ranked_at: datetime | None = None,
        input_as_of: datetime | None = None,
    ) -> list[FounderRanking]:
        """
        Recompute rankings for every entity that has at least one signal.

        Rankings are a full rewrite on each call — composite scores change
        as new signals arrive so stale ranks are worse than no ranks.
        Returns the list of rankings written (oldest first by rank).
        """
        # Gather all distinct entity IDs that have signals
        now = _aware_utc(ranked_at or datetime.now(timezone.utc))
        evidence_cutoff = _aware_utc(input_as_of) if input_as_of is not None else now
        entity_ids = self._all_ranked_entity_ids(now)
        if not entity_ids:
            return []

        scored: list[tuple[str, float, list[RankingDimension]]] = []

        for entity_id in entity_ids:
            if not self._is_rankable(entity_id, evidence_cutoff):
                continue
            signals = self._db.get_signals_for_entity(entity_id, as_of=now, latest_per_type=True)
            if not signals:
                continue
            dimensions = self._compute_dimensions(signals)
            composite = self._composite_score(dimensions)
            scored.append((entity_id, composite, dimensions))

        # Sort descending by composite score; assign rank 1..N
        scored.sort(key=lambda item: (-item[1], item[0]))

        rankings: list[FounderRanking] = []
        for rank, (entity_id, composite, dimensions) in enumerate(scored, start=1):
            signals = self._db.get_signals_for_entity(entity_id, as_of=now, latest_per_type=True)
            hypotheses = self._db.get_hypotheses_for_entity(entity_id, as_of=now)
            evidence = self._db.get_evidence_for_entity(
                entity_id,
                as_of=evidence_cutoff,
            )
            confidence = self._overall_confidence(signals)
            reasoning = self._build_reasoning(entity_id, dimensions, hypotheses)
            signal_ids = sorted(signal.signal_id for signal in signals)
            feature_ids = sorted(
                {feature_id for signal in signals for feature_id in signal.feature_ids}
            )
            crm_action_ids = sorted(
                {action_id for signal in signals for action_id in signal.crm_action_ids}
            )
            semantics = {
                "entity_id": entity_id,
                "rank": rank,
                "composite_score": round(composite, 4),
                "dimensions": [dimension.model_dump(mode="json") for dimension in dimensions],
                "hypothesis_ids": sorted(h.hypothesis_id for h in hypotheses),
                "signal_ids": signal_ids,
                "feature_ids": feature_ids,
                "crm_action_ids": crm_action_ids,
                "as_of": now.isoformat(),
                "ranking_version": RANKING_VERSION,
            }
            input_digest = _digest(semantics)

            ranking = FounderRanking(
                ranking_id=str(uuid.uuid5(_RANKING_NAMESPACE, input_digest)),
                ranking_version=RANKING_VERSION,
                entity_id=entity_id,
                rank=rank,
                composite_score=round(composite, 4),
                dimensions=dimensions,
                hypothesis_ids=[h.hypothesis_id for h in hypotheses],
                evidence_ids=[e.evidence_id for e in evidence],
                signal_ids=signal_ids,
                feature_ids=feature_ids,
                crm_action_ids=crm_action_ids,
                input_digest=input_digest,
                reasoning=reasoning,
                confidence=confidence,
                ranked_at=now,
            )
            self._db.write_ranking(ranking)
            rankings.append(ranking)

        logger.info("ranking complete: %d entities ranked", len(rankings))
        return rankings

    # ── Dimension computation ─────────────────────────────────────────────────

    def _compute_dimensions(self, signals: list[Signal]) -> list[RankingDimension]:
        """
        Map signals onto the 9 dimensions, then build RankingDimension records.

        Dimensions are computed as weighted contributions from relevant signals.
        A dimension with no contributing signal defaults to 0.0.
        """
        sig = {s.signal_type: s for s in signals}

        def _val(st: SignalType) -> float:
            return sig[st].value if st in sig else 0.0

        def _expl(st: SignalType, fallback: str) -> str:
            return sig[st].explanation if st in sig else fallback

        # Pre-M6 snapshots may contain yc_likelihood. It remains read-only
        # compatibility data; new signal computation emits program_affinity.
        program_kind = (
            SignalType.PROGRAM_AFFINITY
            if SignalType.PROGRAM_AFFINITY in sig
            else SignalType.YC_LIKELIHOOD
        )

        # Each dimension value is derived from one or more signals
        raw_dims: dict[str, tuple[float, str, tuple[SignalType, ...]]] = {
            "technical_depth": (
                min(
                    (
                        _val(SignalType.TECHNICAL_DEPTH) * 0.7
                        + _val(SignalType.OPEN_SOURCE_LEADER) * 0.3
                    ),
                    1.0,
                ),
                _expl(SignalType.TECHNICAL_DEPTH, "No repository signals"),
                (SignalType.TECHNICAL_DEPTH, SignalType.OPEN_SOURCE_LEADER),
            ),
            "research_output": (
                _val(SignalType.RESEARCH_FOUNDER),
                _expl(SignalType.RESEARCH_FOUNDER, "No research publications"),
                (SignalType.RESEARCH_FOUNDER,),
            ),
            "hiring_velocity": (
                _val(SignalType.HIRING_VELOCITY),
                _expl(SignalType.HIRING_VELOCITY, "No hiring signals"),
                (SignalType.HIRING_VELOCITY,),
            ),
            "network_quality": (
                min(
                    _val(SignalType.OPEN_SOURCE_LEADER) * 0.5
                    + _val(SignalType.REPEAT_FOUNDER) * 0.5,
                    1.0,
                ),
                "Inferred from open source community and prior founder network",
                (SignalType.OPEN_SOURCE_LEADER, SignalType.REPEAT_FOUNDER),
            ),
            "founder_experience": (
                _val(SignalType.REPEAT_FOUNDER),
                _expl(SignalType.REPEAT_FOUNDER, "First-time founder or prior not detected"),
                (SignalType.REPEAT_FOUNDER,),
            ),
            "product_momentum": (
                min(
                    _val(SignalType.TECHNICAL_DEPTH) * 0.6 + _val(SignalType.HIRING_VELOCITY) * 0.4,
                    1.0,
                ),
                "Derived from commit velocity and hiring pace",
                (SignalType.TECHNICAL_DEPTH, SignalType.HIRING_VELOCITY),
            ),
            "community_reputation": (
                _val(SignalType.OPEN_SOURCE_LEADER),
                _expl(SignalType.OPEN_SOURCE_LEADER, "No community signals"),
                (SignalType.OPEN_SOURCE_LEADER,),
            ),
            "strategic_fit": (
                max(
                    0.0,
                    min(
                        _val(program_kind) * 0.75
                        + _val(SignalType.HUMAN_FEEDBACK_POSITIVE) * 0.15
                        + _val(SignalType.REALIZED_OUTCOME) * 0.10
                        - _val(SignalType.HUMAN_FEEDBACK_NEGATIVE) * 0.20,
                        1.0,
                    ),
                ),
                "Program evidence with bounded, explicitly non-ground-truth CRM feedback",
                (
                    program_kind,
                    SignalType.HUMAN_FEEDBACK_POSITIVE,
                    SignalType.HUMAN_FEEDBACK_NEGATIVE,
                    SignalType.REALIZED_OUTCOME,
                ),
            ),
            "timing": (
                min(
                    _val(program_kind) * 0.5 + _val(SignalType.STEALTH_STARTUP) * 0.5,
                    1.0,
                ),
                "Pre-announcement timing bonus"
                if _val(SignalType.STEALTH_STARTUP) > 0
                else "Standard timing",
                (program_kind, SignalType.STEALTH_STARTUP),
            ),
        }

        return [
            RankingDimension(
                name=name,
                score=round(value, 3),
                weight=DIMENSION_WEIGHTS[name],
                evidence=explanation,
                signal_ids=sorted(sig[kind].signal_id for kind in contributors if kind in sig),
                feature_ids=sorted(
                    {
                        feature_id
                        for kind in contributors
                        if kind in sig
                        for feature_id in sig[kind].feature_ids
                    }
                ),
            )
            for name, (value, explanation, contributors) in raw_dims.items()
        ]

    def _composite_score(self, dimensions: list[RankingDimension]) -> float:
        """Weighted sum: Σ(score_i × weight_i)."""
        return sum(d.score * d.weight for d in dimensions)

    def _overall_confidence(self, signals: list[Signal]) -> float:
        """Average confidence across all signals (penalises entities with few signals)."""
        if not signals:
            return 0.0
        return round(sum(s.confidence for s in signals) / len(signals), 3)

    def _build_reasoning(self, entity_id, dimensions, hypotheses) -> str:
        """
        Produce a 2–3 sentence summary a partner can read at a glance.
        Uses the highest-scoring dimension and the strongest hypothesis.
        """
        top_dim = max(dimensions, key=lambda d: d.score, default=None)
        top_hypo = hypotheses[0] if hypotheses else None

        parts: list[str] = []
        if top_dim and top_dim.score > 0:
            parts.append(
                f"Strongest signal: {top_dim.name.replace('_', ' ')} "
                f"(score {top_dim.score:.2f}). {top_dim.evidence}."
            )
        if top_hypo:
            parts.append(top_hypo.statement)
        if not parts:
            parts.append("Insufficient signals to generate reasoning.")
        return " ".join(parts)

    def _all_ranked_entity_ids(self, as_of: datetime) -> list[str]:
        """Return only validated canonical person/company IDs with signals."""
        with self._db._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT s.entity_id FROM signals s
                JOIN entity_validations v ON v.entity_id = s.entity_id
                WHERE v.status='validated' AND s.observed_at<=?
                """,
                (as_of.isoformat(),),
            ).fetchall()
        return [r["entity_id"] for r in rows]

    def _is_rankable(self, entity_id: str, as_of: datetime) -> bool:
        """Final integrity gate: rankings cannot legitimize raw/source IDs."""
        if not self._db.is_entity_validated(entity_id):
            return False
        entity = self._db.get_company(entity_id) or self._db.get_person(entity_id)
        if entity is None or not validated_entity_record(entity):
            return False
        return bool(self._db.get_evidence_for_entity(entity_id, as_of=as_of))


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("ranking as_of must include a UTC offset")
    return value.astimezone(timezone.utc)


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()
