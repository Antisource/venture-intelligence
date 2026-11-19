"""Candidate rejections, identity validation, entity links, persons and companies."""

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from venture_intelligence.shared.models import (
    Company,
    Person,
)


class EntitiesMixin:
    # ════════════════════════════════════════════════════════════════════════════
    # CANDIDATE REJECTIONS + CANONICAL IDENTITY VALIDATION
    # ════════════════════════════════════════════════════════════════════════════

    def write_candidate_rejection(
        self,
        *,
        candidate_label: str,
        entity_type: str,
        source: str,
        source_url: str,
        query: str | None,
        reason: str,
        confidence: float,
        provenance: dict[str, Any],
        raw_candidate: dict[str, Any],
        rejection_id: str | None = None,
    ) -> None:
        import uuid

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO candidate_rejections
                  (rejection_id, candidate_label, entity_type, source, source_url,
                   query, reason, confidence, provenance, raw_candidate, rejected_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    rejection_id or str(uuid.uuid4()),
                    candidate_label,
                    entity_type,
                    source,
                    source_url,
                    query,
                    reason,
                    confidence,
                    json.dumps(provenance),
                    json.dumps(raw_candidate),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def write_entity_validation(
        self,
        entity_id: str,
        entity_type: str,
        *,
        status: str = "validated",
        reason: str | None = None,
        evidence_count: int = 0,
        provenance: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO entity_validations
                  (entity_id, entity_type, status, reason, evidence_count, provenance, validated_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    entity_id,
                    entity_type,
                    status,
                    reason,
                    evidence_count,
                    json.dumps(provenance or {}),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def is_entity_validated(self, entity_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM entity_validations WHERE entity_id=?", (entity_id,)
            ).fetchone()
            if not row or row["status"] != "validated":
                return False
            company = conn.execute(
                "SELECT canonical_name FROM companies WHERE company_id=?", (entity_id,)
            ).fetchone()
            if company and not self._company_identity_provenance_is_consistent(
                conn, entity_id, str(company["canonical_name"])
            ):
                return False
        return True

    @staticmethod
    def _company_identity_provenance_is_consistent(
        conn: sqlite3.Connection,
        entity_id: str,
        canonical_name: str,
    ) -> bool:
        """Fail closed for legacy cross-entity projection pollution.

        Immutable source events remain untouched. This catches two historical
        failure modes at the read boundary: treating a shared directory
        collection URL as one company identity, and retyping a person-triggered
        corroboration lookup as a company.
        """

        def identity(value: object) -> str:
            return "".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))

        rows = conn.execute(
            """
            SELECT e.source, e.raw_payload, e.metadata
            FROM entity_links l
            JOIN events e ON e.entity_id=l.source_id
            WHERE l.canonical_id=? AND l.entity_kind='company'
            """,
            (entity_id,),
        ).fetchall()
        expected = identity(canonical_name)
        for event in rows:
            try:
                payload = json.loads(event["raw_payload"] or "{}")
                metadata = json.loads(event["metadata"] or "{}")
            except (TypeError, json.JSONDecodeError):
                return False
            if event["source"] == "directory" and identity(payload.get("name")) != expected:
                return False
            parent_id = metadata.get("triggered_by")
            if parent_id:
                parent = conn.execute(
                    "SELECT entity_type FROM events WHERE event_id=?", (str(parent_id),)
                ).fetchone()
                if parent and parent["entity_type"] == "person":
                    return False
        return True

    def get_candidate_rejections(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM candidate_rejections ORDER BY rejected_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def search_validated_entities(
        self,
        query: str,
        *,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        """Return only canonical entities that passed the integrity gate."""
        term = f"%{query.strip().casefold()}%"
        bounded = max(1, min(limit, 50))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.person_id AS entity_id, 'person' AS entity_type,
                       p.canonical_name AS label,
                       p.identity_confidence AS confidence,
                       p.batch AS context, p.updated_at
                FROM persons p
                JOIN entity_validations v ON v.entity_id=p.person_id
                WHERE v.status='validated'
                  AND LOWER(p.canonical_name) LIKE ?
                UNION ALL
                SELECT c.company_id AS entity_id, 'company' AS entity_type,
                       c.canonical_name AS label,
                       c.identity_confidence AS confidence,
                       c.batch AS context, c.updated_at
                FROM companies c
                JOIN entity_validations v ON v.entity_id=c.company_id
                WHERE v.status='validated'
                  AND LOWER(c.canonical_name) LIKE ?
                ORDER BY confidence DESC, label ASC
                LIMIT ?
                """,
                (term, term, bounded),
            ).fetchall()
        return [dict(row) for row in rows if self.is_entity_validated(str(row["entity_id"]))]

    # ════════════════════════════════════════════════════════════════════════════
    # ENTITY LINKS
    # ════════════════════════════════════════════════════════════════════════════

    def write_entity_link(
        self,
        canonical_id: str,
        entity_kind: str,
        source: str,
        source_id: str,
        confidence: float,
    ) -> None:
        """Map a source-specific id (e.g. 'yc__stripe') to a canonical entity id."""
        import uuid

        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO entity_links
                  (link_id, canonical_id, entity_kind, source, source_id,
                   confidence, linked_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (str(uuid.uuid4()), canonical_id, entity_kind, source, source_id, confidence, now),
            )

    def resolve_entity_id(self, source_id: str) -> str | None:
        """Return canonical_id for a source-specific entity_id, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT canonical_id FROM entity_links WHERE source_id=?",
                (source_id,),
            ).fetchone()
        return row["canonical_id"] if row else None

    # ════════════════════════════════════════════════════════════════════════════
    # PERSONS
    # ════════════════════════════════════════════════════════════════════════════

    def write_person(self, person: Person) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO persons
                  (person_id, canonical_name, aliases, linkedin_url,
                   github_username, twitter_handle, current_title,
                   current_company_id, batch, identity_confidence,
                   first_seen_at, updated_at, properties)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    person.person_id,
                    person.canonical_name,
                    json.dumps(person.aliases),
                    person.linkedin_url,
                    person.github_username,
                    person.twitter_handle,
                    person.current_title,
                    person.current_company_id,
                    person.batch,
                    person.identity_confidence,
                    self._ts(person.first_seen_at),
                    now,
                    json.dumps(person.properties),
                ),
            )

    def get_person(self, person_id: str) -> Person | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM persons WHERE person_id=?", (person_id,)).fetchone()
        return self._row_to_person(row) if row else None

    def find_person_by_name(self, name: str) -> list[Person]:
        """Exact canonical_name match — fuzzy matching is in entity_resolution.py."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM persons WHERE canonical_name=?", (name,)).fetchall()
        return [self._row_to_person(r) for r in rows]

    def all_persons(self) -> list[Person]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM persons").fetchall()
        return [self._row_to_person(r) for r in rows]

    def _row_to_person(self, row: sqlite3.Row) -> Person:
        return Person(
            person_id=row["person_id"],
            canonical_name=row["canonical_name"],
            aliases=json.loads(row["aliases"]),
            linkedin_url=row["linkedin_url"],
            github_username=row["github_username"],
            twitter_handle=row["twitter_handle"],
            current_title=row["current_title"],
            current_company_id=row["current_company_id"],
            batch=row["batch"],
            identity_confidence=row["identity_confidence"],
            first_seen_at=self._from_ts(row["first_seen_at"]),
            updated_at=self._from_ts(row["updated_at"]),
            properties=json.loads(row["properties"]),
        )

    # ════════════════════════════════════════════════════════════════════════════
    # COMPANIES
    # ════════════════════════════════════════════════════════════════════════════

    def write_company(self, company: Company) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO companies
                  (company_id, canonical_name, aliases, slug, yc_url,
                   website, description, batch, tags, founder_ids,
                   identity_confidence, first_seen_at, updated_at, properties)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    company.company_id,
                    company.canonical_name,
                    json.dumps(company.aliases),
                    company.slug,
                    company.yc_url,
                    company.website,
                    company.description,
                    company.batch,
                    json.dumps(company.tags),
                    json.dumps(company.founder_ids),
                    company.identity_confidence,
                    self._ts(company.first_seen_at),
                    now,
                    json.dumps(company.properties),
                ),
            )

    def get_company(self, company_id: str) -> Company | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM companies WHERE company_id=?", (company_id,)
            ).fetchone()
        return self._row_to_company(row) if row else None

    def find_company_by_name(self, name: str) -> list[Company]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM companies WHERE canonical_name=?", (name,)
            ).fetchall()
        return [self._row_to_company(r) for r in rows]

    def all_companies(self) -> list[Company]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM companies").fetchall()
        return [self._row_to_company(r) for r in rows]

    def _row_to_company(self, row: sqlite3.Row) -> Company:
        return Company(
            company_id=row["company_id"],
            canonical_name=row["canonical_name"],
            aliases=json.loads(row["aliases"]),
            slug=row["slug"],
            yc_url=row["yc_url"],
            website=row["website"],
            description=row["description"],
            batch=row["batch"],
            tags=json.loads(row["tags"]),
            founder_ids=json.loads(row["founder_ids"]),
            identity_confidence=row["identity_confidence"],
            first_seen_at=self._from_ts(row["first_seen_at"]),
            updated_at=self._from_ts(row["updated_at"]),
            properties=json.loads(row["properties"]),
        )
