"""Read/write service for typed, historically bounded M5 decision workflows."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from venture_intelligence.crm.workflow import CRMWorkflow
from venture_intelligence.decisions.models import (
    AlertTimeline,
    CRMActionResult,
    DecisionBoardResponse,
    DecisionEntityResponse,
    DecisionQueueItem,
    RecommendationTimeline,
)
from venture_intelligence.intelligence.service import IntelligenceService
from venture_intelligence.pipeline.ranking_worker import RankingWorker
from venture_intelligence.pipeline.recommendation_worker import RecommendationWorker
from venture_intelligence.pipeline.signal_worker import SignalWorker
from venture_intelligence.shared.models import (
    CRM_TRANSITIONS,
    AlertLifecycleEvent,
    AlertLifecycleStatus,
    CRMActionType,
    CRMStage,
    RecommendationLifecycleEvent,
    RecommendationLifecycleStatus,
    SignalType,
)
from venture_intelligence.storage.db import Database

_LIFECYCLE_NAMESPACE = uuid.UUID("f7ec4eab-8f30-55ab-80aa-f3d027d08531")
_FEEDBACK_SIGNAL_TYPES = {
    SignalType.HUMAN_FEEDBACK_POSITIVE,
    SignalType.HUMAN_FEEDBACK_NEGATIVE,
    SignalType.REALIZED_OUTCOME,
}


class DecisionService:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._crm = CRMWorkflow(database)
        self._intelligence = IntelligenceService(database)

    def board(
        self,
        *,
        as_of: datetime | None = None,
        program_id: str | None = None,
    ) -> DecisionBoardResponse:
        cutoff = _utc(as_of or datetime.now(timezone.utc))
        stages: dict[CRMStage, list[DecisionQueueItem]] = {stage: [] for stage in CRMStage}
        for stage_name, rows in self._database.get_crm_board().items():
            stage = CRMStage(stage_name)
            for row in rows:
                entity_id = str(row["entity_id"])
                intelligence = self._intelligence.entity(entity_id, as_of=cutoff)
                record = self._database.get_crm_record(entity_id)
                if intelligence is None or record is None:
                    continue
                if program_id and intelligence.entity.program_id != program_id:
                    continue
                stages[stage].append(self._queue_item(record, intelligence, cutoff))
        return DecisionBoardResponse(as_of=cutoff, stages=stages)

    def entity(
        self, entity_id: str, *, as_of: datetime | None = None
    ) -> DecisionEntityResponse | None:
        cutoff = _utc(as_of or datetime.now(timezone.utc))
        intelligence = self._intelligence.entity(entity_id, as_of=cutoff)
        record = self._database.get_crm_record(entity_id)
        if intelligence is None or record is None:
            return None
        actions = self._database.get_crm_events(entity_id, as_of=cutoff)
        feedback_features = [
            item
            for item in self._database.get_features_for_entity(entity_id, as_of=cutoff)
            if item.crm_action_ids
        ]
        feedback_signals = [
            item
            for item in self._database.get_signals_for_entity(entity_id, as_of=cutoff)
            if item.signal_type in _FEEDBACK_SIGNAL_TYPES
        ]
        recommendations = [
            RecommendationTimeline(
                recommendation=item,
                lifecycle=self._database.get_recommendation_lifecycle(
                    item.recommendation_id, as_of=cutoff
                ),
            )
            for item in self._database.get_recommendations_for_entity(entity_id, as_of=cutoff)
        ]
        alerts = [
            AlertTimeline(
                alert=item,
                lifecycle=self._database.get_alert_lifecycle(item.alert_id, as_of=cutoff),
            )
            for item in self._database.get_alerts_for_entity(entity_id, as_of=cutoff)
        ]
        return DecisionEntityResponse(
            as_of=cutoff,
            intelligence=intelligence,
            crm=record,
            allowed_transitions=CRM_TRANSITIONS[CRMStage(record.stage)],
            actions=actions,
            feedback_features=feedback_features,
            feedback_signals=feedback_signals,
            recommendations=recommendations,
            alerts=alerts,
        )

    def apply_action(
        self,
        entity_id: str,
        action: CRMActionType,
        *,
        partner: str,
        note: str | None = None,
        to_stage: CRMStage | None = None,
        occurred_at: datetime | None = None,
        provenance: dict[str, Any] | None = None,
        action_id: str | None = None,
    ) -> CRMActionResult:
        if self._intelligence.entity(entity_id) is None:
            raise LookupError("Validated canonical entity not found")
        _, crm_action = self._crm.record_action(
            entity_id,
            action,
            partner=partner,
            note=note,
            to_stage=to_stage,
            occurred_at=occurred_at,
            provenance=provenance,
            action_id=action_id,
        )
        cutoff = crm_action.observed_at
        SignalWorker(self._database).process_entity(entity_id, as_of=cutoff)
        RankingWorker(self._database).rank_all(ranked_at=cutoff)
        RecommendationWorker(self._database).generate_all()
        result = self.entity(entity_id)
        if result is None:
            raise RuntimeError("Decision projection unavailable after CRM action")
        return CRMActionResult(action=crm_action, decision=result)

    def update_recommendation(
        self,
        recommendation_id: str,
        status: RecommendationLifecycleStatus,
        *,
        actor: str,
        reason: str | None = None,
    ) -> RecommendationLifecycleEvent:
        recommendation = self._database.get_recommendation(recommendation_id)
        if recommendation is None:
            raise LookupError("Recommendation not found")
        now = datetime.now(timezone.utc)
        event = RecommendationLifecycleEvent(
            lifecycle_event_id=str(
                uuid.uuid5(
                    _LIFECYCLE_NAMESPACE,
                    f"recommendation:{recommendation_id}:{status.value}:{actor}:{now.isoformat()}",
                )
            ),
            recommendation_id=recommendation_id,
            entity_id=recommendation.entity_id,
            status=status,
            actor=actor,
            reason=reason,
            provenance={"channel": "decision_api"},
            occurred_at=now,
        )
        self._database.write_recommendation_lifecycle(event)
        return event

    def update_alert(
        self,
        alert_id: str,
        status: AlertLifecycleStatus,
        *,
        actor: str,
        reason: str | None = None,
    ) -> AlertLifecycleEvent:
        alert = self._database.get_alert(alert_id)
        if alert is None:
            raise LookupError("Alert not found")
        now = datetime.now(timezone.utc)
        event = AlertLifecycleEvent(
            lifecycle_event_id=str(
                uuid.uuid5(
                    _LIFECYCLE_NAMESPACE,
                    f"alert:{alert_id}:{status.value}:{actor}:{now.isoformat()}",
                )
            ),
            alert_id=alert_id,
            recommendation_id=alert.recommendation_id,
            entity_id=alert.entity_id,
            status=status,
            actor=actor,
            reason=reason,
            provenance={"channel": "decision_api"},
            occurred_at=now,
        )
        self._database.write_alert_lifecycle(event)
        return event

    def _queue_item(self, record, intelligence, cutoff) -> DecisionQueueItem:
        actions = self._database.get_crm_events(record.entity_id, as_of=cutoff)
        features = [item for item in intelligence.features if item.crm_action_ids]
        recommendation = intelligence.recommendation
        recommendation_lifecycle = (
            self._database.get_recommendation_lifecycle(
                recommendation.recommendation_id, as_of=cutoff
            )
            if recommendation
            else []
        )
        alerts = self._database.get_alerts_for_entity(record.entity_id, as_of=cutoff)
        latest_alert = alerts[0] if alerts else None
        alert_lifecycle = (
            self._database.get_alert_lifecycle(latest_alert.alert_id, as_of=cutoff)
            if latest_alert
            else []
        )
        return DecisionQueueItem(
            entity=intelligence.entity,
            crm=record,
            allowed_transitions=CRM_TRANSITIONS[CRMStage(record.stage)],
            action_count=len(actions),
            feedback_features=features,
            ranking=intelligence.ranking,
            recommendation=recommendation,
            recommendation_status=(
                recommendation_lifecycle[-1].status.value if recommendation_lifecycle else None
            ),
            latest_alert=latest_alert,
            alert_status=alert_lifecycle[-1].status.value if alert_lifecycle else None,
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("decision as_of must include a UTC offset")
    return value.astimezone(timezone.utc)
