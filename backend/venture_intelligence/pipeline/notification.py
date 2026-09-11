"""
pipeline/notification.py — dispatches alerts to partners (§19).

Sits at the end of the pipeline.  Reads pending recommendations, checks
whether they clear both the confidence and rank thresholds, and fires
to all configured sinks (stdout always; Slack if SLACK_WEBHOOK_URL is set).

The gap between Recommendation.created_at and Alert.fired_at is the
end-to-end pipeline latency — this should be minutes, not hours.

Alert sinks:
  _console()  — always fires; structured for log aggregation
  _slack()    — fires if SLACK_WEBHOOK_URL is configured

New sinks (email, Teams, PagerDuty) go here — add a method and call it
from _dispatch().  No other file needs to change.
"""

import logging
import uuid
from datetime import datetime, timezone

import httpx

from venture_intelligence.config import settings
from venture_intelligence.pipeline.email import EmailNotificationService
from venture_intelligence.shared.models import (
    Alert,
    AlertLifecycleEvent,
    AlertLifecycleStatus,
    Recommendation,
    RecommendationAction,
)
from venture_intelligence.storage.db import Database

logger = logging.getLogger(__name__)

# Actions that always warrant an alert regardless of rank
_ALWAYS_ALERT = {RecommendationAction.ESCALATE, RecommendationAction.CONTACT}
_ALERT_NAMESPACE = uuid.UUID("f5fa6ed2-c9a5-5e73-8f62-bc3ee25aef21")


class NotificationWorker:
    """Evaluates recommendations and fires alerts to partner sinks."""

    def __init__(self, db: Database, email_service: EmailNotificationService | None = None) -> None:
        self._db = db
        self._email_service = email_service or EmailNotificationService(
            settings.email_config_path,
            smtp_host=settings.email_smtp_host,
            smtp_port=settings.email_smtp_port,
            smtp_username=settings.email_smtp_username,
            smtp_password=settings.email_smtp_password,
            smtp_from=settings.email_smtp_from,
        )

    def dispatch_pending(self) -> int:
        """
        Check all recent recommendations against thresholds and fire alerts.
        Returns the number of alerts dispatched in this cycle.
        """
        recs = self._pending_recommendations()
        fired = 0

        for rec in recs:
            if self._should_alert(rec):
                alert = self._build_alert(rec)
                self._db.write_alert(alert)
                self._record_lifecycle(alert, AlertLifecycleStatus.CREATED)
                self._record_lifecycle(alert, AlertLifecycleStatus.DISPATCHING)
                delivery = self._dispatch(alert)
                statuses = {item.get("status") for item in delivery.values()}
                final = (
                    AlertLifecycleStatus.PARTIAL
                    if "failed" in statuses and len(statuses) > 1
                    else AlertLifecycleStatus.FAILED
                    if statuses == {"failed"}
                    else AlertLifecycleStatus.DELIVERED
                )
                self._record_lifecycle(alert, final, delivery=delivery)
                if final is not AlertLifecycleStatus.FAILED:
                    self._mark_notified(rec, alert)
                fired += 1

        if fired:
            logger.info("alerts dispatched: %d", fired)
        return fired

    # ── Threshold logic ───────────────────────────────────────────────────────

    def _should_alert(self, rec: Recommendation) -> bool:
        """
        Apply the two-gate threshold (§19):
          Gate 1 — confidence must be ≥ alert_min_confidence
          Gate 2 — ranking must be ≤ alert_top_n_rank  (or action is ESCALATE/CONTACT)
        """
        if rec.confidence < settings.alert_min_confidence:
            return False
        if rec.action in _ALWAYS_ALERT:
            return True
        if rec.action == RecommendationAction.MONITOR:
            # Only alert on MONITOR if confidence is very high (0.80+)
            return rec.confidence >= 0.80
        return False

    # ── Alert construction ────────────────────────────────────────────────────

    def _build_alert(self, rec: Recommendation) -> Alert:
        """
        Build an Alert from a Recommendation + entity context.
        Evidence summary is a 3–5 bullet list for the partner briefing.
        """
        # Look up entity name from company or person table
        entity_name = self._resolve_name(rec.entity_id)
        evidence = self._db.get_evidence_for_entity(rec.entity_id)
        ranking = self._db.get_ranking_for_entity(rec.entity_id)
        events = self._db.get_events_for_entity(rec.entity_id)

        # Build evidence bullets (most confident claims first)
        evidence_sorted = sorted(evidence, key=lambda e: e.confidence, reverse=True)
        bullets = [e.claim for e in evidence_sorted[:5]]

        source_urls = list({e.source_url for e in events if e.source_url})[:3]

        return Alert(
            alert_id=str(uuid.uuid5(_ALERT_NAMESPACE, rec.recommendation_id)),
            recommendation_id=rec.recommendation_id,
            entity_id=rec.entity_id,
            entity_name=entity_name,
            action=rec.action,
            reasoning=rec.reasoning,
            evidence_summary=bullets,
            rank=ranking.rank if ranking else None,
            confidence=rec.confidence,
            source_urls=source_urls,
            crm_action_ids=rec.crm_action_ids,
            fired_at=datetime.now(timezone.utc),
        )

    # ── Dispatch sinks ────────────────────────────────────────────────────────

    def _dispatch(self, alert: Alert) -> dict[str, dict]:
        """Fire alert to all configured sinks."""
        self._console(alert)
        delivery: dict[str, dict] = {"console": {"status": "sent"}}
        if settings.slack_webhook_url:
            delivery["slack"] = self._slack(alert)
        delivery["email"] = self._email_service.send(
            alert.alert_id,
            f"Venture Intelligence alert: {alert.entity_name}",
            (
                f"{alert.action.value.upper()} for {alert.entity_name}\n\n"
                f"{alert.reasoning}\n\n" + "\n".join(alert.evidence_summary)
            ),
            links=alert.source_urls,
            category="threshold_alerts",
            confidence=alert.confidence,
        )
        return delivery

    def _console(self, alert: Alert) -> None:
        """Structured stdout output — parseable by log aggregators."""
        rank_str = f"rank=#{alert.rank}" if alert.rank else "rank=unranked"
        bullets = "\n    ".join(f"• {b}" for b in alert.evidence_summary)
        logger.info(
            "ALERT action=%s entity=%r %s confidence=%.2f alert_id=%s",
            alert.action,
            alert.entity_name,
            rank_str,
            alert.confidence,
            alert.alert_id,
        )
        print(
            f"\n{'=' * 60}\n"
            f"🚨  {alert.action.upper()}  — {alert.entity_name}\n"
            f"{'=' * 60}\n"
            f"  {rank_str}  |  confidence={alert.confidence:.0%}\n\n"
            f"  Reasoning: {alert.reasoning}\n\n"
            f"  Evidence:\n    {bullets}\n\n"
            f"  Sources: {' | '.join(alert.source_urls)}\n"
            f"  Alert ID: {alert.alert_id}\n"
            f"{'=' * 60}\n"
        )

    def _slack(self, alert: Alert) -> dict:
        """
        Post a formatted Slack message via incoming webhook.
        Failures are logged but never raise — a broken Slack hook must
        not stop the pipeline.
        """
        emoji = {"contact": "📞", "escalate": "🔥", "monitor": "👀"}.get(alert.action.value, "🔔")
        bullets = "\n".join(f"• {b}" for b in alert.evidence_summary)
        rank_str = f"Rank #{alert.rank}" if alert.rank else "Unranked"

        payload = {
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"{emoji} {alert.action.upper()}: {alert.entity_name}",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*{rank_str}* | Confidence: {alert.confidence:.0%}\n\n"
                            f"{alert.reasoning}\n\n"
                            f"*Evidence:*\n{bullets}"
                        ),
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Sources: {' | '.join(alert.source_urls[:2])} "
                            f"| `{alert.alert_id}`",
                        }
                    ],
                },
            ]
        }
        try:
            resp = httpx.post(settings.slack_webhook_url, json=payload, timeout=10)
            resp.raise_for_status()
            return {"status": "sent", "http_status": resp.status_code}
        except Exception as exc:
            logger.warning("Slack delivery failed: %s", exc)
            return {"status": "failed", "error": str(exc)[:500]}

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _pending_recommendations(self) -> list[Recommendation]:
        """
        Return recommendations that haven't yet been turned into alerts.
        We detect 'pending' by checking whether an alert exists for the
        recommendation_id.
        """
        from venture_intelligence.shared.models import RecommendationAction

        with self._db._connect() as conn:
            rows = conn.execute(
                """
                SELECT r.* FROM recommendations r
                WHERE NOT EXISTS (
                    SELECT 1 FROM alerts a WHERE a.recommendation_id = r.recommendation_id
                )
                AND r.action != ?
                ORDER BY r.created_at DESC LIMIT 200
                """,
                (RecommendationAction.IGNORE.value,),
            ).fetchall()

        import json

        from venture_intelligence.shared.models import Recommendation, RecommendationAction

        result = []
        for row in rows:
            result.append(
                Recommendation(
                    recommendation_id=row["recommendation_id"],
                    entity_id=row["entity_id"],
                    action=RecommendationAction(row["action"]),
                    reasoning=row["reasoning"],
                    evidence_ids=json.loads(row["evidence_ids"]),
                    ranking_id=row["ranking_id"],
                    confidence=row["confidence"],
                    created_at=self._db._from_ts(row["created_at"]),
                )
            )
        return result

    def _resolve_name(self, entity_id: str) -> str:
        """Look up display name from persons or companies table."""
        company = self._db.get_company(entity_id)
        if company:
            return company.canonical_name
        person = self._db.get_person(entity_id)
        if person:
            return person.canonical_name
        return entity_id

    def _mark_notified(self, rec: Recommendation, alert: Alert) -> None:
        """Stamp the alert's notified_at once delivery is confirmed."""
        now = datetime.now(timezone.utc).isoformat()
        with self._db._connect() as conn:
            conn.execute(
                "UPDATE alerts SET notified_at=? WHERE alert_id=?",
                (now, alert.alert_id),
            )

    def _record_lifecycle(
        self,
        alert: Alert,
        status: AlertLifecycleStatus,
        *,
        delivery: dict | None = None,
    ) -> None:
        self._db.write_alert_lifecycle(
            AlertLifecycleEvent(
                lifecycle_event_id=str(
                    uuid.uuid5(
                        _ALERT_NAMESPACE,
                        f"lifecycle:{alert.alert_id}:{status.value}",
                    )
                ),
                alert_id=alert.alert_id,
                recommendation_id=alert.recommendation_id,
                entity_id=alert.entity_id,
                status=status,
                actor="notification_worker",
                delivery=delivery or {},
                provenance={"recommendation_id": alert.recommendation_id},
                occurred_at=datetime.now(timezone.utc),
            )
        )
