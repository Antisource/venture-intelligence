"""Alert persistence and alert lifecycle."""

import json
from datetime import datetime

from venture_intelligence.shared.models import (
    Alert,
    AlertLifecycleEvent,
)


class AlertsMixin:
    def write_alert(self, alert: Alert) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO alerts
                  (alert_id, schema_version, alert_version,
                   recommendation_id, entity_id, entity_name,
                   action, reasoning, evidence_summary, rank, confidence,
                   source_urls, crm_action_ids, fired_at, notified_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    alert.alert_id,
                    alert.schema_version,
                    alert.alert_version,
                    alert.recommendation_id,
                    alert.entity_id,
                    alert.entity_name,
                    alert.action,
                    alert.reasoning,
                    json.dumps(alert.evidence_summary),
                    alert.rank,
                    alert.confidence,
                    json.dumps(alert.source_urls),
                    json.dumps(alert.crm_action_ids),
                    self._ts(alert.fired_at),
                    self._ts(alert.notified_at),
                ),
            )

    def get_recent_alerts(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM alerts ORDER BY fired_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_alert(self, alert_id: str) -> Alert | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM alerts WHERE alert_id=?", (alert_id,)).fetchone()
        if row is None:
            return None
        return Alert(
            alert_id=row["alert_id"],
            schema_version=row["schema_version"],
            alert_version=row["alert_version"],
            recommendation_id=row["recommendation_id"],
            entity_id=row["entity_id"],
            entity_name=row["entity_name"],
            action=row["action"],
            reasoning=row["reasoning"],
            evidence_summary=json.loads(row["evidence_summary"]),
            rank=row["rank"],
            confidence=row["confidence"],
            source_urls=json.loads(row["source_urls"]),
            crm_action_ids=json.loads(row["crm_action_ids"]),
            fired_at=self._from_ts(row["fired_at"]),
            notified_at=self._from_ts(row["notified_at"]),
        )

    def get_alerts_for_entity(
        self, entity_id: str, *, as_of: datetime | None = None
    ) -> list[Alert]:
        with self._connect() as conn:
            if as_of is None:
                rows = conn.execute(
                    "SELECT alert_id FROM alerts WHERE entity_id=? ORDER BY fired_at DESC",
                    (entity_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT alert_id FROM alerts WHERE entity_id=? AND fired_at<=? "
                    "ORDER BY fired_at DESC",
                    (entity_id, self._ts(as_of)),
                ).fetchall()
        return [item for row in rows if (item := self.get_alert(row["alert_id"]))]

    def write_alert_lifecycle(self, event: AlertLifecycleEvent) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO alert_lifecycle_events
                  (lifecycle_event_id, alert_id, recommendation_id, entity_id,
                   status, actor, reason, delivery, provenance, occurred_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event.lifecycle_event_id,
                    event.alert_id,
                    event.recommendation_id,
                    event.entity_id,
                    event.status,
                    event.actor,
                    event.reason,
                    json.dumps(event.delivery, sort_keys=True),
                    json.dumps(event.provenance, sort_keys=True),
                    self._ts(event.occurred_at),
                ),
            )

    def get_alert_lifecycle(
        self, alert_id: str, *, as_of: datetime | None = None
    ) -> list[AlertLifecycleEvent]:
        query = "SELECT * FROM alert_lifecycle_events WHERE alert_id=?"
        params: list[object] = [alert_id]
        if as_of is not None:
            query += " AND occurred_at<=?"
            params.append(self._ts(as_of))
        query += " ORDER BY occurred_at, lifecycle_event_id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            AlertLifecycleEvent(
                lifecycle_event_id=row["lifecycle_event_id"],
                alert_id=row["alert_id"],
                recommendation_id=row["recommendation_id"],
                entity_id=row["entity_id"],
                status=row["status"],
                actor=row["actor"],
                reason=row["reason"],
                delivery=json.loads(row["delivery"]),
                provenance=json.loads(row["provenance"]),
                occurred_at=self._from_ts(row["occurred_at"]),
            )
            for row in rows
        ]
