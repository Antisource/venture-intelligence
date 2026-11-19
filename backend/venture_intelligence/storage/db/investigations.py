"""Investigations, classifications and entity freshness queries."""

import json
from datetime import datetime, timezone


class InvestigationsMixin:
    # ════════════════════════════════════════════════════════════════════════════
    # INVESTIGATIONS  (§8 — human review objects)
    # ════════════════════════════════════════════════════════════════════════════

    def write_investigation(self, inv) -> None:
        self._ensure_investigation_table()
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO investigations
                  (investigation_id, entity_id, status, lead_partner, notes,
                   evidence_ids, open_questions, decision, decision_reasoning,
                   opened_at, concluded_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    inv.investigation_id,
                    inv.entity_id,
                    inv.status,
                    inv.lead_partner,
                    json.dumps(inv.notes),
                    json.dumps(inv.evidence_ids),
                    json.dumps(inv.open_questions),
                    inv.decision,
                    inv.decision_reasoning,
                    self._ts(inv.opened_at),
                    self._ts(inv.concluded_at),
                    now,
                ),
            )

    def get_investigation(self, entity_id: str) -> dict | None:
        self._ensure_investigation_table()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM investigations WHERE entity_id=? ORDER BY opened_at DESC LIMIT 1",
                (entity_id,),
            ).fetchone()
        return dict(row) if row else None

    def _ensure_investigation_table(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS investigations (
                    investigation_id   TEXT PRIMARY KEY,
                    entity_id          TEXT NOT NULL,
                    status             TEXT NOT NULL DEFAULT 'open',
                    lead_partner       TEXT,
                    notes              TEXT NOT NULL DEFAULT '[]',
                    evidence_ids       TEXT NOT NULL DEFAULT '[]',
                    open_questions     TEXT NOT NULL DEFAULT '[]',
                    decision           TEXT,
                    decision_reasoning TEXT,
                    opened_at          TEXT NOT NULL,
                    concluded_at       TEXT,
                    updated_at         TEXT NOT NULL
                )
            """)

    # ════════════════════════════════════════════════════════════════════════════
    # CLASSIFICATIONS  (§16)
    # ════════════════════════════════════════════════════════════════════════════

    def write_classification(self, clf) -> None:
        self._ensure_classification_table()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO classifications
                  (classification_id, entity_id, person_type, confidence,
                   evidence_ids, classified_at)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    clf.classification_id,
                    clf.entity_id,
                    clf.person_type,
                    clf.confidence,
                    json.dumps(clf.evidence_ids),
                    self._ts(clf.classified_at),
                ),
            )

    def get_classification(self, entity_id: str) -> dict | None:
        self._ensure_classification_table()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM classifications WHERE entity_id=? "
                "ORDER BY classified_at DESC LIMIT 1",
                (entity_id,),
            ).fetchone()
        return dict(row) if row else None

    def _ensure_classification_table(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS classifications (
                    classification_id TEXT PRIMARY KEY,
                    entity_id         TEXT NOT NULL,
                    person_type       TEXT NOT NULL,
                    confidence        REAL NOT NULL,
                    evidence_ids      TEXT NOT NULL DEFAULT '[]',
                    classified_at     TEXT NOT NULL
                )
            """)

    def known_count(self) -> int:
        """Total distinct entities tracked (persons + companies)."""
        with self._connect() as conn:
            p = conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
            c = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        return p + c

    def get_all_entity_ids(self) -> list[str]:
        """Return all entity IDs that have at least one event."""
        with self._connect() as conn:
            rows = conn.execute("SELECT DISTINCT entity_id FROM events").fetchall()
        return [r["entity_id"] for r in rows]

    def get_stale_entity_ids(self, older_than_hours: int = 12) -> list[str]:
        """Return entity IDs whose most recent event is older than N hours."""
        cutoff = datetime.now(timezone.utc).replace(microsecond=0)
        from datetime import timedelta

        cutoff = cutoff - timedelta(hours=older_than_hours)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT entity_id, MAX(ingested_at) as last_seen
                FROM events
                GROUP BY entity_id
                HAVING last_seen < ?
                ORDER BY last_seen ASC
                """,
                (cutoff.isoformat(),),
            ).fetchall()
        return [r["entity_id"] for r in rows]
