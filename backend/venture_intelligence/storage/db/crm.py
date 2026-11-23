"""CRM record and CRM event persistence."""

import json
import sqlite3
from datetime import datetime, timezone

from venture_intelligence.shared.models import (
    CRMEvent,
    CRMRecord,
    CRMStage,
)


class CRMMixin:
    # ════════════════════════════════════════════════════════════════════════════
    # CRM
    # ════════════════════════════════════════════════════════════════════════════

    def get_or_create_crm(self, entity_id: str) -> CRMRecord:
        """Return existing CRM record or create one at DISCOVERY stage."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM crm_records WHERE entity_id=?", (entity_id,)
            ).fetchone()
        if row:
            return self._row_to_crm_record(row)
        record = CRMRecord(entity_id=entity_id)
        self.write_crm(record)
        return record

    def get_crm_record(self, entity_id: str) -> CRMRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM crm_records WHERE entity_id=?", (entity_id,)
            ).fetchone()
        return self._row_to_crm_record(row) if row is not None else None

    def _row_to_crm_record(self, row: sqlite3.Row) -> CRMRecord:
        return CRMRecord(
            crm_id=row["crm_id"],
            entity_id=row["entity_id"],
            stage=row["stage"],
            notes=json.loads(row["notes"]),
            decision=row["decision"],
            created_at=self._from_ts(row["created_at"]),
            updated_at=self._from_ts(row["updated_at"]),
        )

    def write_crm(self, record: CRMRecord) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO crm_records
                  (crm_id, entity_id, stage, notes, decision, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    record.crm_id,
                    record.entity_id,
                    record.stage,
                    json.dumps(record.notes),
                    record.decision,
                    self._ts(record.created_at),
                    now,
                ),
            )

    def write_crm_event(self, event: CRMEvent) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO crm_events
                  (crm_event_id, schema_version, action_version, entity_id,
                   action, feedback_kind, from_stage, to_stage, note, partner,
                   ranking_id, provenance, occurred_at, observed_at, created_at,
                   input_digest)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event.crm_event_id,
                    event.schema_version,
                    event.action_version,
                    event.entity_id,
                    event.action,
                    event.feedback_kind,
                    event.from_stage,
                    event.to_stage,
                    event.note,
                    event.partner,
                    event.ranking_id,
                    json.dumps(event.provenance, sort_keys=True),
                    self._ts(event.occurred_at),
                    self._ts(event.observed_at),
                    self._ts(event.created_at),
                    event.input_digest,
                ),
            )

    def get_crm_events(
        self,
        entity_id: str | None = None,
        *,
        as_of: datetime | None = None,
    ) -> list[CRMEvent]:
        clauses: list[str] = []
        params: list[object] = []
        if entity_id is not None:
            clauses.append("entity_id=?")
            params.append(entity_id)
        if as_of is not None:
            clauses.append("observed_at<=?")
            params.append(self._ts(as_of))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM crm_events"
                + where
                + " ORDER BY observed_at, created_at, crm_event_id",
                params,
            ).fetchall()
        return [self._row_to_crm_event(row) for row in rows]

    def get_crm_event(self, crm_event_id: str) -> CRMEvent | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM crm_events WHERE crm_event_id=?", (crm_event_id,)
            ).fetchone()
        return self._row_to_crm_event(row) if row is not None else None

    def _row_to_crm_event(self, row: sqlite3.Row) -> CRMEvent:
        action = "stage_changed" if row["action"] == "stage_advanced" else row["action"]
        return CRMEvent(
            crm_event_id=row["crm_event_id"],
            schema_version=row["schema_version"],
            action_version=row["action_version"],
            entity_id=row["entity_id"],
            action=action,
            feedback_kind=row["feedback_kind"],
            from_stage=row["from_stage"],
            to_stage=row["to_stage"],
            note=row["note"],
            partner=row["partner"] or "system",
            ranking_id=row["ranking_id"],
            provenance=json.loads(row["provenance"]),
            occurred_at=self._from_ts(row["occurred_at"]),
            observed_at=self._from_ts(row["observed_at"]),
            created_at=self._from_ts(row["created_at"]),
            input_digest=row["input_digest"],
        )

    def get_crm_history(self, entity_id: str) -> list[dict]:
        return [item.model_dump(mode="json") for item in self.get_crm_events(entity_id)]

    def get_crm_board(self) -> dict[str, list[dict]]:
        """Return all CRM records grouped by stage (Kanban view for API)."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM crm_records ORDER BY updated_at DESC").fetchall()
        board: dict[str, list[dict]] = {s.value: [] for s in CRMStage}
        for row in rows:
            board[row["stage"]].append(dict(row))
        return board
