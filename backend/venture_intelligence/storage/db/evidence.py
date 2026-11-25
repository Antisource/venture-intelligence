"""Evidence chain persistence: observations, evidence, signals, hypotheses."""

import json
import sqlite3
from datetime import datetime
from typing import Any

from venture_intelligence.shared.models import (
    Evidence,
    Hypothesis,
    Observation,
    Signal,
    SignalType,
)
from venture_intelligence.storage.db._base import _json_object, _json_value, _research_published_at


class EvidenceMixin:
    # ════════════════════════════════════════════════════════════════════════════
    # EVIDENCE CHAIN  (Observation → Evidence → Signal → Hypothesis)
    # ════════════════════════════════════════════════════════════════════════════

    def write_observation(self, obs: Observation) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO observations
                  (observation_id, event_id, entity_id, source, fact,
                   value, observed_at, confidence)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    obs.observation_id,
                    obs.event_id,
                    obs.entity_id,
                    obs.source,
                    obs.fact,
                    json.dumps(obs.value) if obs.value is not None else None,
                    self._ts(obs.observed_at),
                    obs.confidence,
                ),
            )

    def write_evidence(self, ev: Evidence) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO evidence
                  (evidence_id, entity_id, claim, observation_ids,
                   confidence, observed_at)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    ev.evidence_id,
                    ev.entity_id,
                    ev.claim,
                    json.dumps(ev.observation_ids),
                    ev.confidence,
                    self._ts(ev.observed_at),
                ),
            )

    def get_evidence_for_entity(
        self,
        entity_id: str,
        *,
        as_of: datetime | None = None,
        include_unbound: bool = False,
    ) -> list[Evidence]:
        with self._connect() as conn:
            if as_of is None:
                rows = conn.execute(
                    "SELECT * FROM evidence WHERE entity_id=? ORDER BY observed_at DESC",
                    (entity_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM evidence WHERE entity_id=? AND observed_at<=? "
                    "ORDER BY observed_at DESC, evidence_id",
                    (entity_id, self._ts(as_of)),
                ).fetchall()
        evidence = [
            Evidence(
                evidence_id=r["evidence_id"],
                entity_id=r["entity_id"],
                claim=r["claim"],
                observation_ids=json.loads(r["observation_ids"]),
                confidence=r["confidence"],
                observed_at=self._from_ts(r["observed_at"]),
            )
            for r in rows
        ]
        if include_unbound:
            return evidence
        with self._connect() as conn:
            return [
                item
                for item in evidence
                if self._evidence_attribution_status(conn, item.observation_ids) == "attached"
            ]

    @staticmethod
    def _evidence_attribution_status(
        conn: sqlite3.Connection,
        observation_ids: list[str],
    ) -> str:
        """Fail closed for legacy research events blessed only at run level."""
        statuses: set[str] = set()
        for observation_id in observation_ids:
            row = conn.execute(
                """
                SELECT ev.source, ev.source_url, ev.metadata
                FROM observations o
                LEFT JOIN events ev ON ev.event_id=o.event_id
                WHERE o.observation_id=?
                """,
                (observation_id,),
            ).fetchone()
            if row is None:
                continue
            if row["source"] != "research_agent":
                statuses.add("attached")
                continue
            metadata = _json_object(row["metadata"])
            explicit = str(metadata.get("attribution_status") or "")
            if explicit in {"attached", "unbound", "contradictory"}:
                statuses.add(explicit)
                continue
            identity_urls = metadata.get("identity_evidence_urls", [])
            statuses.add(
                "attached"
                if isinstance(identity_urls, list) and row["source_url"] in identity_urls
                else "unbound"
            )
        if "contradictory" in statuses:
            return "contradictory"
        return "attached" if "attached" in statuses else "unbound"

    def get_evidence_details_for_entity(
        self,
        entity_id: str,
        *,
        as_of: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return evidence with source URLs and event provenance for API views.

        The canonical evidence table remains unchanged. This helper joins the
        immutable observation/event chain at read time so clients can inspect
        the exact public URLs supporting each claim.
        """
        from urllib.parse import urlsplit

        evidence = self.get_evidence_for_entity(
            entity_id,
            as_of=as_of,
            include_unbound=True,
        )
        details: list[dict[str, Any]] = []
        with self._connect() as conn:
            for item in evidence:
                observations: list[dict[str, Any]] = []
                source_urls: list[str] = []
                provenance: list[dict[str, Any]] = []
                primary_metadata: dict[str, Any] = {}
                primary_event_type: str | None = None
                occurred_at: str | None = None
                attribution_status = self._evidence_attribution_status(
                    conn, item.observation_ids
                )
                attribution_reason = "identity_not_anchored"
                acquisition_providers: list[str] = []
                evidence_origin_url: str | None = None
                for observation_id in item.observation_ids:
                    row = conn.execute(
                        """
                        SELECT o.observation_id, o.event_id, o.source, o.fact,
                               o.value, o.observed_at, ev.source_url,
                               ev.event_type, ev.occurred_at, ev.raw_payload, ev.metadata
                        FROM observations o
                        LEFT JOIN events ev ON ev.event_id=o.event_id
                        WHERE o.observation_id=? AND o.entity_id=?
                        """,
                        (observation_id, entity_id),
                    ).fetchone()
                    if row is None:
                        continue
                    url = row["source_url"]
                    if url and url not in source_urls:
                        source_urls.append(url)
                    metadata = {}
                    if row["metadata"]:
                        try:
                            metadata = json.loads(row["metadata"])
                        except (TypeError, ValueError):
                            metadata = {"raw": row["metadata"]}
                    raw_payload = _json_object(row["raw_payload"])
                    value = _json_value(row["value"])
                    if not observations:
                        primary_metadata = metadata
                        primary_event_type = row["event_type"]
                        occurred_at = row["occurred_at"]
                    attribution_reason = str(
                        metadata.get("attribution_reason") or attribution_reason
                    )
                    providers = metadata.get("acquisition_providers", [])
                    if isinstance(providers, list):
                        acquisition_providers = list(
                            dict.fromkeys(
                                [*acquisition_providers, *(str(value) for value in providers)]
                            )
                        )
                    research_evidence = raw_payload.get("research_evidence", {})
                    if isinstance(research_evidence, dict):
                        evidence_origin_url = str(
                            metadata.get("evidence_origin")
                            or research_evidence.get("evidence_origin_url")
                            or research_evidence.get("canonical_url")
                            or url
                            or ""
                        ) or None
                    observations.append(
                        {
                            "observation_id": row["observation_id"],
                            "event_id": row["event_id"],
                            "source": row["source"],
                            "fact": row["fact"],
                            "value": value,
                            "observed_at": row["observed_at"],
                        }
                    )
                    provenance.append(
                        {
                            "event_id": row["event_id"],
                            "source": row["source"],
                            "source_url": url,
                            "source_domain": urlsplit(url).netloc.casefold() if url else None,
                            "metadata": metadata,
                            "raw_payload": raw_payload,
                        }
                    )
                details.append(
                    {
                        "evidence_id": item.evidence_id,
                        "entity_id": item.entity_id,
                        "claim": item.claim,
                        "title": item.claim,
                        "confidence": item.confidence,
                        "observed_at": item.observed_at.isoformat(),
                        "source_domain": (
                            provenance[0].get("source_domain") if provenance else None
                        ),
                        "observation_ids": item.observation_ids,
                        "source_urls": source_urls,
                        "url": source_urls[0] if source_urls else None,
                        "canonical_url": source_urls[0] if source_urls else None,
                        "observations": observations,
                        "provenance": provenance,
                        "source": observations[0]["source"] if observations else None,
                        "event_id": observations[0]["event_id"] if observations else None,
                        "event_type": primary_event_type,
                        "program_id": primary_metadata.get("program_id"),
                        "batch_id": primary_metadata.get("batch_id")
                        or primary_metadata.get("batch"),
                        "occurred_at": occurred_at,
                        "published_at": _research_published_at(provenance),
                        "attribution_status": attribution_status,
                        "attribution_reason": attribution_reason,
                        "evidence_origin_url": evidence_origin_url
                        or (source_urls[0] if source_urls else None),
                        "acquisition_providers": acquisition_providers,
                    }
                )
        return details

    def get_feature_inputs_for_entity(
        self,
        entity_id: str,
        *,
        as_of: datetime,
    ) -> list[dict[str, Any]]:
        """Return immutable evidence/observation/event inputs for feature computation."""
        return [
            item
            for item in self.get_evidence_details_for_entity(entity_id, as_of=as_of)
            if item["attribution_status"] == "attached"
        ]

    def write_signal(self, sig: Signal) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO signals
                  (signal_id, entity_id, signal_type, value, evidence_ids,
                   feature_ids, crm_action_ids, signal_version, input_digest,
                   explanation, confidence, observed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    sig.signal_id,
                    sig.entity_id,
                    sig.signal_type,
                    sig.value,
                    json.dumps(sig.evidence_ids),
                    json.dumps(sig.feature_ids),
                    json.dumps(sig.crm_action_ids),
                    sig.signal_version,
                    sig.input_digest,
                    sig.explanation,
                    sig.confidence,
                    self._ts(sig.observed_at),
                ),
            )

    def get_signals_for_entity(
        self,
        entity_id: str,
        *,
        as_of: datetime | None = None,
        latest_per_type: bool = False,
    ) -> list[Signal]:
        with self._connect() as conn:
            if as_of is None:
                rows = conn.execute(
                    "SELECT * FROM signals WHERE entity_id=? ORDER BY observed_at DESC, signal_id",
                    (entity_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM signals WHERE entity_id=? AND observed_at<=? "
                    "ORDER BY observed_at DESC, signal_id",
                    (entity_id, self._ts(as_of)),
                ).fetchall()
        signals = [
            Signal(
                signal_id=r["signal_id"],
                signal_version=r["signal_version"],
                entity_id=r["entity_id"],
                signal_type=r["signal_type"],
                value=r["value"],
                evidence_ids=json.loads(r["evidence_ids"]),
                feature_ids=json.loads(r["feature_ids"]),
                crm_action_ids=json.loads(r["crm_action_ids"]),
                input_digest=r["input_digest"],
                explanation=r["explanation"],
                confidence=r["confidence"],
                observed_at=self._from_ts(r["observed_at"]),
            )
            for r in rows
        ]
        if not latest_per_type:
            return signals
        latest: dict[SignalType, Signal] = {}
        for signal in signals:
            latest.setdefault(signal.signal_type, signal)
        return list(latest.values())

    def write_hypothesis(self, h: Hypothesis) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO hypotheses
                  (hypothesis_id, entity_id, statement, signal_ids,
                   hypothesis_version, input_digest, probability, created_at)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    h.hypothesis_id,
                    h.entity_id,
                    h.statement,
                    json.dumps(h.signal_ids),
                    h.hypothesis_version,
                    h.input_digest,
                    h.probability,
                    self._ts(h.created_at),
                ),
            )

    def get_hypotheses_for_entity(
        self,
        entity_id: str,
        *,
        as_of: datetime | None = None,
    ) -> list[Hypothesis]:
        with self._connect() as conn:
            if as_of is None:
                rows = conn.execute(
                    "SELECT * FROM hypotheses WHERE entity_id=? "
                    "ORDER BY created_at DESC, hypothesis_id",
                    (entity_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM hypotheses WHERE entity_id=? AND created_at<=? "
                    "ORDER BY created_at DESC, hypothesis_id",
                    (entity_id, self._ts(as_of)),
                ).fetchall()
        return [
            Hypothesis(
                hypothesis_id=r["hypothesis_id"],
                hypothesis_version=r["hypothesis_version"],
                entity_id=r["entity_id"],
                statement=r["statement"],
                signal_ids=json.loads(r["signal_ids"]),
                input_digest=r["input_digest"],
                probability=r["probability"],
                created_at=self._from_ts(r["created_at"]),
            )
            for r in rows
        ]
