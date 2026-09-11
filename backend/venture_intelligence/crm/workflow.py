"""
crm/workflow.py — CRM state machine (§20).

Every founder/company the system tracks gets a CRMRecord that moves through
8 stages as partner conviction grows.  Stage transitions are validated against
CRM_TRANSITIONS so invalid moves are caught early.

Key design decision: every stage change is recorded as a CRMEvent — an
immutable log entry that feeds back into the event bus.  This means:
  1. Partners have a full audit trail of every decision.
  2. The ranking worker can incorporate human feedback (PARTNER_PASSED
     reduces yc_likelihood for that entity in future runs).
  3. The system learns from outcomes over time.
"""

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from venture_intelligence.shared.models import (
    CRM_TRANSITIONS,
    CRMActionType,
    CRMEvent,
    CRMFeedbackKind,
    CRMRecord,
    CRMStage,
    RecommendationLifecycleEvent,
    RecommendationLifecycleStatus,
)
from venture_intelligence.storage.db import Database

logger = logging.getLogger(__name__)
_ACTION_NAMESPACE = uuid.UUID("40aa3bdb-a512-5af8-90b5-3258fe2b2560")
_HUMAN_ACTIONS = {
    CRMActionType.MEETING_SCHEDULED,
    CRMActionType.PASSED,
    CRMActionType.INTERESTED,
    CRMActionType.INTRODUCED,
    CRMActionType.WATCHLISTED,
    CRMActionType.FOLLOW_UP_REQUESTED,
    CRMActionType.RELATIONSHIP_STARTED,
    CRMActionType.CONFIRMED,
    CRMActionType.DISMISSED,
    CRMActionType.DUPLICATE,
    CRMActionType.IDENTITY_CORRECTED,
    CRMActionType.PRIORITY_CHANGED,
    CRMActionType.INVESTIGATION_REQUESTED,
    CRMActionType.OUTREACH,
    CRMActionType.DILIGENCE,
}


class CRMWorkflow:
    """
    Manages CRM records and stage transitions.

    All mutation goes through advance_stage() or add_note() — never directly
    to the DB — so the audit trail (crm_events table) is always complete.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    # ── Auto-creation ─────────────────────────────────────────────────────────

    def ensure_discovery(self, entity_id: str) -> CRMRecord:
        """
        Get or create a CRM record at DISCOVERY stage.
        Called automatically by the RecommendationWorker for every ranked entity
        so partners always see a complete Kanban board.
        """
        return self._db.get_or_create_crm(entity_id)

    # ── State machine ─────────────────────────────────────────────────────────

    def record_action(
        self,
        entity_id: str,
        action: CRMActionType,
        *,
        partner: str,
        note: str | None = None,
        to_stage: CRMStage | None = None,
        occurred_at: datetime | None = None,
        observed_at: datetime | None = None,
        provenance: dict[str, Any] | None = None,
        action_id: str | None = None,
    ) -> tuple[CRMRecord, CRMEvent]:
        """Append a typed action, then update the materialized board state."""
        actor = partner.strip()
        if not actor or len(actor) > 200:
            raise ValueError("partner must contain 1-200 characters")
        if note is not None and len(note) > 4_000:
            raise ValueError("note must not exceed 4000 characters")

        record = self._db.get_or_create_crm(entity_id)
        if action_id:
            existing = self._db.get_crm_event(action_id)
            if existing is not None:
                if existing.entity_id != entity_id or existing.action is not action:
                    raise ValueError("action_id already belongs to a different CRM action")
                return record, existing
        current = CRMStage(record.stage)
        target = to_stage or current
        if target != current and target not in CRM_TRANSITIONS.get(current, []):
            raise ValueError(
                f"Invalid CRM transition: {current.value} → {target.value}. "
                f"Allowed: {[stage.value for stage in CRM_TRANSITIONS.get(current, [])]}"
            )

        happened = _utc(occurred_at or datetime.now(timezone.utc))
        seen = _utc(observed_at or datetime.now(timezone.utc))
        if seen < happened:
            raise ValueError("observed_at cannot precede occurred_at for a CRM action")
        feedback_kind = _feedback_kind(action)
        ranking = self._db.get_ranking_for_entity(entity_id)
        action_provenance = {
            "origin": "crm",
            "authority": (
                "human_input" if feedback_kind is not CRMFeedbackKind.WORKFLOW else "workflow"
            ),
            "authoritative_ground_truth": False,
            "ranking_context": (
                {
                    "ranking_id": ranking.ranking_id,
                    "ranking_version": ranking.ranking_version,
                    "rank": ranking.rank,
                    "composite_score": ranking.composite_score,
                    "confidence": ranking.confidence,
                    "input_digest": ranking.input_digest,
                }
                if ranking
                else None
            ),
            "evidence_state": (
                {
                    "evidence_ids": ranking.evidence_ids,
                    "feature_ids": ranking.feature_ids,
                    "signal_ids": ranking.signal_ids,
                    "hypothesis_ids": ranking.hypothesis_ids,
                    "captured_at": seen.isoformat(),
                }
                if ranking
                else {"captured_at": seen.isoformat(), "ranking_unavailable": True}
            ),
            **(provenance or {}),
        }
        semantics = {
            "entity_id": entity_id,
            "action": action.value,
            "feedback_kind": feedback_kind.value,
            "from_stage": current.value,
            "to_stage": target.value,
            "note": note,
            "partner": actor,
            "ranking_id": ranking.ranking_id if ranking else None,
            "occurred_at": happened.isoformat(),
            "observed_at": seen.isoformat(),
            "provenance": action_provenance,
        }
        digest = _digest(semantics)
        crm_event = CRMEvent(
            crm_event_id=action_id or str(uuid.uuid5(_ACTION_NAMESPACE, digest)),
            entity_id=entity_id,
            action=action,
            feedback_kind=feedback_kind,
            from_stage=current,
            to_stage=target,
            note=note,
            partner=actor,
            ranking_id=ranking.ranking_id if ranking else None,
            provenance=action_provenance,
            occurred_at=happened,
            observed_at=seen,
            created_at=datetime.now(timezone.utc),
            input_digest=digest,
        )
        self._db.write_crm_event(crm_event)

        record.stage = target
        record.updated_at = datetime.now(timezone.utc)
        if note:
            record.notes.append(f"[{actor}] {note}")
        if action is CRMActionType.PASSED:
            record.decision = note or "Passed"
        self._db.write_crm(record)
        self._reflect_recommendation(crm_event)
        logger.info(
            "CRM action: entity=%s action=%s %s→%s by=%s",
            entity_id,
            action.value,
            current.value,
            target.value,
            actor,
        )
        return record, crm_event

    def advance_stage(
        self,
        entity_id: str,
        to_stage: CRMStage,
        partner: str | None = None,
        note: str | None = None,
    ) -> CRMRecord:
        """
        Move an entity to a new CRM stage.

        Raises ValueError if the transition is not permitted by CRM_TRANSITIONS.
        Every successful transition writes a CRMEvent for the audit log.
        """
        record, _ = self.record_action(
            entity_id,
            CRMActionType.STAGE_CHANGED,
            partner=partner or "system",
            note=note,
            to_stage=to_stage,
            provenance={"channel": "legacy_compatibility"},
        )
        return record

    def add_note(
        self,
        entity_id: str,
        note: str,
        partner: str | None = None,
    ) -> CRMRecord:
        """
        Append a free-text note without changing stage.
        Useful for recording a meeting summary or a piece of diligence.
        """
        record, _ = self.record_action(
            entity_id,
            CRMActionType.NOTE_ADDED,
            partner=partner or "unknown",
            note=note,
            provenance={"channel": "legacy_compatibility"},
        )
        return record

    def record_decision(
        self,
        entity_id: str,
        decision: str,
        partner: str | None = None,
    ) -> CRMRecord:
        """
        Record a final decision (e.g. "Too early", "Wrong sector") when
        moving to PASSED stage.
        """
        record, _ = self.record_action(
            entity_id,
            CRMActionType.PASSED,
            partner=partner or "unknown",
            note=decision,
            to_stage=CRMStage.PASSED,
        )
        return record

    def get_board(self) -> dict[str, list[dict]]:
        """Return all CRM records grouped by stage — used by the Kanban API endpoint."""
        return self._db.get_crm_board()

    def get_history(self, entity_id: str) -> list[dict]:
        """Return the full audit trail for one entity."""
        return self._db.get_crm_history(entity_id)

    def _reflect_recommendation(self, action: CRMEvent) -> None:
        recommendation = self._db.get_recommendation_for_entity(action.entity_id)
        if recommendation is None:
            return
        status = {
            CRMActionType.PASSED: RecommendationLifecycleStatus.DISMISSED,
            CRMActionType.INTERESTED: RecommendationLifecycleStatus.ACCEPTED,
            CRMActionType.MEETING_SCHEDULED: RecommendationLifecycleStatus.ACCEPTED,
            CRMActionType.INTRODUCED: RecommendationLifecycleStatus.ACCEPTED,
            CRMActionType.INVESTED: RecommendationLifecycleStatus.COMPLETED,
        }.get(action.action)
        if status is None:
            return
        self._db.write_recommendation_lifecycle(
            RecommendationLifecycleEvent(
                lifecycle_event_id=str(
                    uuid.uuid5(
                        _ACTION_NAMESPACE,
                        (
                            f"recommendation:{recommendation.recommendation_id}:"
                            f"{action.crm_event_id}:{status.value}"
                        ),
                    )
                ),
                recommendation_id=recommendation.recommendation_id,
                entity_id=action.entity_id,
                status=status,
                actor=action.partner,
                reason=action.note,
                provenance={"crm_action_id": action.crm_event_id},
                occurred_at=action.observed_at,
            )
        )


def _feedback_kind(action: CRMActionType) -> CRMFeedbackKind:
    if action is CRMActionType.INVESTED:
        return CRMFeedbackKind.REALIZED_EXTERNAL_OUTCOME
    if action in _HUMAN_ACTIONS:
        return CRMFeedbackKind.HUMAN_JUDGMENT
    return CRMFeedbackKind.WORKFLOW


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("CRM action timestamps must include a UTC offset")
    return value.astimezone(timezone.utc)


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()
