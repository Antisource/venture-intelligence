"""Versioned feature persistence."""

import json
import sqlite3
from datetime import datetime

from venture_intelligence.shared.models import (
    Feature,
)


class FeaturesMixin:
    # ════════════════════════════════════════════════════════════════════════════
    # FEATURES  (§8 — raw measurable values)
    # ════════════════════════════════════════════════════════════════════════════

    def write_feature(self, feature: Feature) -> None:
        """Persist one deterministic, versioned feature measurement."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO features
                  (feature_id, schema_version, feature_version, entity_id, name,
                   value, unit, source, observed_at, as_of, computed_at,
                   evidence_ids, observation_ids, crm_action_ids, formula,
                   parameters, input_digest, program_id, batch_id, confidence)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    feature.feature_id,
                    feature.schema_version,
                    feature.feature_version,
                    feature.entity_id,
                    feature.name,
                    feature.value,
                    feature.unit,
                    feature.source.value,
                    self._ts(feature.observed_at),
                    self._ts(feature.as_of),
                    self._ts(feature.computed_at),
                    json.dumps(feature.evidence_ids),
                    json.dumps(feature.observation_ids),
                    json.dumps(feature.crm_action_ids),
                    feature.formula,
                    json.dumps(feature.parameters, sort_keys=True),
                    feature.input_digest,
                    feature.program_id,
                    feature.batch_id,
                    feature.confidence,
                ),
            )

    def get_features_for_entity(
        self,
        entity_id: str,
        *,
        as_of: datetime | None = None,
        latest_per_name: bool = False,
    ) -> list[Feature]:
        with self._connect() as conn:
            if as_of is None:
                rows = conn.execute(
                    "SELECT * FROM features WHERE entity_id=? "
                    "ORDER BY as_of DESC, name, feature_id",
                    (entity_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM features WHERE entity_id=? AND as_of<=? "
                    "ORDER BY as_of DESC, name, feature_id",
                    (entity_id, self._ts(as_of)),
                ).fetchall()
        features = [self._row_to_feature(row) for row in rows]
        if not latest_per_name:
            return features
        latest: dict[str, Feature] = {}
        for feature in features:
            latest.setdefault(feature.name, feature)
        return sorted(latest.values(), key=lambda item: item.name)

    def _row_to_feature(self, row: sqlite3.Row) -> Feature:
        return Feature(
            feature_id=row["feature_id"],
            schema_version=row["schema_version"],
            feature_version=row["feature_version"],
            entity_id=row["entity_id"],
            name=row["name"],
            value=row["value"],
            unit=row["unit"],
            source=row["source"],
            observed_at=self._from_ts(row["observed_at"]),
            as_of=self._from_ts(row["as_of"]),
            computed_at=self._from_ts(row["computed_at"]),
            evidence_ids=json.loads(row["evidence_ids"]),
            observation_ids=json.loads(row["observation_ids"]),
            crm_action_ids=json.loads(row["crm_action_ids"]),
            formula=row["formula"],
            parameters=json.loads(row["parameters"]),
            input_digest=row["input_digest"],
            program_id=row["program_id"],
            batch_id=row["batch_id"],
            confidence=row["confidence"],
        )

    def _ensure_features_table(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS features (
                    feature_id  TEXT PRIMARY KEY,
                    entity_id   TEXT NOT NULL,
                    name        TEXT NOT NULL,
                    value       REAL NOT NULL,
                    unit        TEXT,
                    source      TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    confidence  REAL NOT NULL,
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_features_entity ON features(entity_id)")
