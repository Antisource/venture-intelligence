"""
storage/db.py — SQLite database layer.

Responsibilities:
  1. DDL — create all tables on first run, migrate schema if needed
  2. Write operations — every domain object has one write method
  3. Read operations — everything the pipeline workers need to query

Design principles:
  • Events table is append-only — no UPDATE or DELETE ever
  • All timestamps stored as ISO-8601 UTC strings
  • JSON columns store Pydantic .model_dump_json() output
  • Every foreign-key relationship is enforced by Python, not SQLite
    constraints (kept simple for V1 SQLite — add FK pragma in V2)

§5 note: This is the V1 local-first database.  The interface is designed
to be replaced by PostgreSQL + asyncpg in V2 without touching callers.
"""

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from venture_intelligence.shared.models import (
    Alert,
    AlertLifecycleEvent,
    CanonicalEvent,
    Company,
    CRMEvent,
    CRMRecord,
    CRMStage,
    EvaluationResult,
    Evidence,
    Feature,
    FounderRanking,
    GraphEdge,
    GraphNode,
    Hypothesis,
    Observation,
    Person,
    Recommendation,
    RecommendationLifecycleEvent,
    Signal,
    SignalType,
)

# ── DDL ───────────────────────────────────────────────────────────────────────
# One string per logical group; executed in order on first boot.

_SCHEMA = """
-- ── Applied additive migrations ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

-- ── Events (append-only source of truth, §6.1) ───────────────────────────────
CREATE TABLE IF NOT EXISTS events (
    event_id       TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL DEFAULT '1.0',
    event_type     TEXT NOT NULL,
    entity_type    TEXT NOT NULL,
    entity_id      TEXT NOT NULL,
    source         TEXT NOT NULL,
    source_url     TEXT NOT NULL,
    occurred_at    TEXT NOT NULL,
    occurred_at_precision TEXT NOT NULL DEFAULT 'unknown',
    observed_at    TEXT NOT NULL,
    ingested_at    TEXT NOT NULL,
    processed_at   TEXT,           -- NULL until pipeline workers finish
    ranked_at      TEXT,           -- NULL until ranking worker runs
    notified_at    TEXT,           -- NULL until alert is dispatched
    validity       TEXT,           -- JSON TemporalValidity or NULL
    confidence     REAL NOT NULL,
    raw_payload    TEXT NOT NULL DEFAULT '{}',
    metadata       TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_entity   ON events(entity_id);
CREATE INDEX IF NOT EXISTS idx_events_type     ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_source   ON events(source);
CREATE INDEX IF NOT EXISTS idx_events_ingested ON events(ingested_at);
CREATE INDEX IF NOT EXISTS idx_events_observed ON events(observed_at);

-- ── Event bus queue (§13) ─────────────────────────────────────────────────────
-- Each event is processed independently by each worker type.
-- (event_id, worker) uniqueness guarantees exactly-once processing per worker.
CREATE TABLE IF NOT EXISTS event_queue (
    queue_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL,
    worker       TEXT NOT NULL,    -- 'validator'|'resolver'|'graph'|'signal'|etc.
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending|processing|done|failed
    error        TEXT,             -- error message if status='failed'
    created_at   TEXT NOT NULL,
    processed_at TEXT,
    UNIQUE(event_id, worker)
);
CREATE INDEX IF NOT EXISTS idx_queue_status ON event_queue(status, worker);

-- ── Persons (§14) ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS persons (
    person_id           TEXT PRIMARY KEY,
    canonical_name      TEXT NOT NULL,
    aliases             TEXT NOT NULL DEFAULT '[]',  -- JSON array
    linkedin_url        TEXT,
    github_username     TEXT,
    twitter_handle      TEXT,
    current_title       TEXT,
    current_company_id  TEXT,
    batch               TEXT,
    identity_confidence REAL NOT NULL DEFAULT 0.9,
    first_seen_at       TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    properties          TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_persons_name   ON persons(canonical_name);
CREATE INDEX IF NOT EXISTS idx_persons_github ON persons(github_username);

-- ── Companies (§14) ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS companies (
    company_id          TEXT PRIMARY KEY,
    canonical_name      TEXT NOT NULL,
    aliases             TEXT NOT NULL DEFAULT '[]',
    slug                TEXT,
    yc_url              TEXT,
    website             TEXT,
    description         TEXT,
    batch               TEXT,
    tags                TEXT NOT NULL DEFAULT '[]',
    founder_ids         TEXT NOT NULL DEFAULT '[]',
    identity_confidence REAL NOT NULL DEFAULT 0.9,
    first_seen_at       TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    properties          TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_companies_name ON companies(canonical_name);
CREATE INDEX IF NOT EXISTS idx_companies_slug ON companies(slug);

-- ── Entity links (cross-source identity map, §14) ─────────────────────────────
-- Maps "yc__stripe" → canonical company_id; "linkedin__john-doe" → person_id
CREATE TABLE IF NOT EXISTS entity_links (
    link_id      TEXT PRIMARY KEY,
    canonical_id TEXT NOT NULL,    -- person_id or company_id
    entity_kind  TEXT NOT NULL,    -- 'person' | 'company'
    source       TEXT NOT NULL,
    source_id    TEXT NOT NULL,    -- e.g. "yc__stripe"
    confidence   REAL NOT NULL,
    linked_at    TEXT NOT NULL,
    UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_links_canonical ON entity_links(canonical_id);

-- ── Candidate rejections and canonical identity gate ────────────────────────
CREATE TABLE IF NOT EXISTS candidate_rejections (
    rejection_id      TEXT PRIMARY KEY,
    candidate_label   TEXT NOT NULL,
    entity_type       TEXT NOT NULL,
    source            TEXT NOT NULL,
    source_url        TEXT NOT NULL,
    query             TEXT,
    reason            TEXT NOT NULL,
    confidence        REAL NOT NULL,
    provenance        TEXT NOT NULL DEFAULT '{}',
    raw_candidate     TEXT NOT NULL DEFAULT '{}',
    rejected_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rejections_label ON candidate_rejections(candidate_label);

CREATE TABLE IF NOT EXISTS entity_validations (
    entity_id         TEXT PRIMARY KEY,
    entity_type       TEXT NOT NULL,
    status            TEXT NOT NULL, -- validated|rejected
    reason            TEXT,
    evidence_count    INTEGER NOT NULL DEFAULT 0,
    provenance        TEXT NOT NULL DEFAULT '{}',
    validated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entity_validations_status ON entity_validations(status);

-- ── Knowledge graph nodes (§15) ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS graph_nodes (
    node_id    TEXT PRIMARY KEY,
    node_type  TEXT NOT NULL,
    label      TEXT NOT NULL,
    properties TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_type  ON graph_nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_nodes_label ON graph_nodes(label);

-- ── Knowledge graph edges (§15) ───────────────────────────────────────────────
-- UNIQUE on (source, target, type) — only one edge of each type between a pair.
-- Re-observed relationships update confidence and evidence_ids in place.
CREATE TABLE IF NOT EXISTS graph_edges (
    edge_id      TEXT PRIMARY KEY,
    source_id    TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    edge_type    TEXT NOT NULL,
    confidence   REAL NOT NULL DEFAULT 0.9,
    observed_at  TEXT NOT NULL,
    valid_from   TEXT,             -- start of relationship (NULL = unknown)
    valid_to     TEXT,             -- end of relationship   (NULL = still active)
    validity_precision TEXT NOT NULL DEFAULT 'unknown',
    temporal_status TEXT NOT NULL DEFAULT 'unknown',
    evidence_ids TEXT NOT NULL DEFAULT '[]',
    properties   TEXT NOT NULL DEFAULT '{}',
    UNIQUE(source_id, target_id, edge_type)
);
CREATE INDEX IF NOT EXISTS idx_edges_source ON graph_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON graph_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_type   ON graph_edges(edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_temporal
    ON graph_edges(observed_at, valid_from, valid_to);

-- ── Observations (§17) ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS observations (
    observation_id TEXT PRIMARY KEY,
    event_id       TEXT NOT NULL,
    entity_id      TEXT NOT NULL,
    source         TEXT NOT NULL,
    fact           TEXT NOT NULL,
    value          TEXT,           -- JSON if structured, NULL otherwise
    observed_at    TEXT NOT NULL,
    confidence     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_entity ON observations(entity_id);

-- ── Evidence (§17) ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id     TEXT PRIMARY KEY,
    entity_id       TEXT NOT NULL,
    claim           TEXT NOT NULL,
    observation_ids TEXT NOT NULL DEFAULT '[]',
    confidence      REAL NOT NULL,
    observed_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_entity ON evidence(entity_id);

-- ── Versioned deterministic features (M4) ──────────────────────────────────
CREATE TABLE IF NOT EXISTS features (
    feature_id      TEXT PRIMARY KEY,
    schema_version  TEXT NOT NULL DEFAULT '4.0',
    feature_version TEXT NOT NULL DEFAULT 'legacy-1',
    entity_id       TEXT NOT NULL,
    name            TEXT NOT NULL,
    value           REAL NOT NULL,
    unit            TEXT,
    source          TEXT NOT NULL,
    observed_at     TEXT NOT NULL,
    as_of           TEXT NOT NULL,
    computed_at     TEXT NOT NULL,
    evidence_ids    TEXT NOT NULL DEFAULT '[]',
    observation_ids TEXT NOT NULL DEFAULT '[]',
    crm_action_ids  TEXT NOT NULL DEFAULT '[]',
    formula         TEXT NOT NULL DEFAULT 'legacy_direct_value',
    parameters      TEXT NOT NULL DEFAULT '{}',
    input_digest    TEXT NOT NULL DEFAULT '',
    program_id      TEXT,
    batch_id        TEXT,
    confidence      REAL NOT NULL
);
-- ── Signals (§16, §17) ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS signals (
    signal_id    TEXT PRIMARY KEY,
    entity_id    TEXT NOT NULL,
    signal_type  TEXT NOT NULL,
    value        REAL NOT NULL,    -- 0.0 – 1.0
    evidence_ids TEXT NOT NULL DEFAULT '[]',
    feature_ids  TEXT NOT NULL DEFAULT '[]',
    crm_action_ids TEXT NOT NULL DEFAULT '[]',
    signal_version TEXT NOT NULL DEFAULT 'legacy-1',
    input_digest TEXT NOT NULL DEFAULT '',
    explanation  TEXT NOT NULL,
    confidence   REAL NOT NULL,
    observed_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_entity ON signals(entity_id);
CREATE INDEX IF NOT EXISTS idx_signals_type   ON signals(signal_type);

-- ── Hypotheses (§17) ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hypotheses (
    hypothesis_id TEXT PRIMARY KEY,
    entity_id     TEXT NOT NULL,
    statement     TEXT NOT NULL,
    signal_ids    TEXT NOT NULL DEFAULT '[]',
    hypothesis_version TEXT NOT NULL DEFAULT 'legacy-1',
    input_digest  TEXT NOT NULL DEFAULT '',
    probability   REAL NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hypotheses_entity ON hypotheses(entity_id);

-- ── Rankings (§18) ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rankings (
    ranking_id      TEXT PRIMARY KEY,
    entity_id       TEXT NOT NULL,
    rank            INTEGER NOT NULL,
    composite_score REAL NOT NULL,
    dimensions      TEXT NOT NULL DEFAULT '{}',  -- JSON dict
    hypothesis_ids  TEXT NOT NULL DEFAULT '[]',
    evidence_ids    TEXT NOT NULL DEFAULT '[]',
    signal_ids      TEXT NOT NULL DEFAULT '[]',
    feature_ids     TEXT NOT NULL DEFAULT '[]',
    crm_action_ids  TEXT NOT NULL DEFAULT '[]',
    ranking_version TEXT NOT NULL DEFAULT 'legacy-1',
    input_digest    TEXT NOT NULL DEFAULT '',
    reasoning       TEXT NOT NULL,
    confidence      REAL NOT NULL,
    ranked_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rankings_rank   ON rankings(rank);
CREATE INDEX IF NOT EXISTS idx_rankings_entity ON rankings(entity_id);
CREATE INDEX IF NOT EXISTS idx_rankings_score  ON rankings(composite_score DESC);

-- ── Recommendations (§8, §16) ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS recommendations (
    recommendation_id TEXT PRIMARY KEY,
    schema_version     TEXT NOT NULL DEFAULT '5.0',
    recommendation_version TEXT NOT NULL DEFAULT 'legacy-1',
    entity_id         TEXT NOT NULL,
    action            TEXT NOT NULL,
    reasoning         TEXT NOT NULL,
    evidence_ids      TEXT NOT NULL DEFAULT '[]',
    ranking_id        TEXT,
    signal_ids        TEXT NOT NULL DEFAULT '[]',
    feature_ids       TEXT NOT NULL DEFAULT '[]',
    crm_action_ids    TEXT NOT NULL DEFAULT '[]',
    input_digest      TEXT NOT NULL DEFAULT '',
    supersedes_recommendation_id TEXT,
    confidence        REAL NOT NULL,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recs_entity ON recommendations(entity_id);

CREATE TABLE IF NOT EXISTS recommendation_lifecycle_events (
    lifecycle_event_id TEXT PRIMARY KEY,
    recommendation_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    status TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT,
    provenance TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rec_lifecycle
ON recommendation_lifecycle_events(recommendation_id, occurred_at);

-- ── Alerts (§8, §19) ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alerts (
    alert_id           TEXT PRIMARY KEY,
    schema_version     TEXT NOT NULL DEFAULT '5.0',
    alert_version      TEXT NOT NULL DEFAULT 'legacy-1',
    recommendation_id  TEXT NOT NULL,
    entity_id          TEXT NOT NULL,
    entity_name        TEXT NOT NULL,
    action             TEXT NOT NULL,
    reasoning          TEXT NOT NULL,
    evidence_summary   TEXT NOT NULL DEFAULT '[]',
    rank               INTEGER,
    confidence         REAL NOT NULL,
    source_urls        TEXT NOT NULL DEFAULT '[]',
    crm_action_ids     TEXT NOT NULL DEFAULT '[]',
    fired_at           TEXT NOT NULL,
    notified_at        TEXT           -- NULL until delivery confirmed
);
CREATE INDEX IF NOT EXISTS idx_alerts_entity ON alerts(entity_id);
CREATE INDEX IF NOT EXISTS idx_alerts_fired  ON alerts(fired_at);

CREATE TABLE IF NOT EXISTS alert_lifecycle_events (
    lifecycle_event_id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL,
    recommendation_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    status TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT,
    delivery TEXT NOT NULL DEFAULT '{}',
    provenance TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_lifecycle
ON alert_lifecycle_events(alert_id, occurred_at);

-- ── CRM records (§20) ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS crm_records (
    crm_id     TEXT PRIMARY KEY,
    entity_id  TEXT NOT NULL UNIQUE,   -- one CRM record per entity
    stage      TEXT NOT NULL DEFAULT 'discovery',
    notes      TEXT NOT NULL DEFAULT '[]',
    decision   TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- ── CRM events — partner actions as events (§20) ──────────────────────────────
CREATE TABLE IF NOT EXISTS crm_events (
    crm_event_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL DEFAULT '5.0',
    action_version TEXT NOT NULL DEFAULT 'legacy-1',
    entity_id    TEXT NOT NULL,
    action       TEXT NOT NULL,
    feedback_kind TEXT NOT NULL DEFAULT 'workflow',
    from_stage   TEXT NOT NULL,
    to_stage     TEXT NOT NULL,
    note         TEXT,
    partner      TEXT,
    ranking_id   TEXT,
    provenance   TEXT NOT NULL DEFAULT '{}',
    occurred_at  TEXT NOT NULL DEFAULT '',
    observed_at  TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
    ,input_digest TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_crm_events_entity ON crm_events(entity_id);

-- ── Evaluation results (§21) ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS evaluation_results (
    eval_id                    TEXT PRIMARY KEY,
    batch                      TEXT NOT NULL,
    evaluated_at               TEXT NOT NULL,
    precision_at_10            REAL NOT NULL,
    precision_at_25            REAL NOT NULL,
    recall                     REAL NOT NULL,
    avg_lead_time_days         REAL NOT NULL,
    median_lead_time_days      REAL NOT NULL,
    false_positives            INTEGER NOT NULL,
    missed_companies           INTEGER NOT NULL,
    alert_acceptance_rate      REAL NOT NULL,
    investment_conversion_rate REAL NOT NULL,
    total_entities_ranked      INTEGER NOT NULL,
    notes                      TEXT NOT NULL DEFAULT ''
);

-- ── Historical evaluation (M3; JSON payload is canonical) ──────────────────
CREATE TABLE IF NOT EXISTS ground_truth_datasets (
    dataset_id        TEXT PRIMARY KEY,
    program_id        TEXT NOT NULL,
    batch_id          TEXT NOT NULL,
    target_level      TEXT NOT NULL,
    outcome_timestamp TEXT NOT NULL,
    digest            TEXT NOT NULL,
    imported_at       TEXT NOT NULL,
    payload            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ground_truth_program_batch
    ON ground_truth_datasets(program_id, batch_id, imported_at);

CREATE TABLE IF NOT EXISTS evaluation_runs_v2 (
    evaluation_run_id TEXT PRIMARY KEY,
    program_id        TEXT NOT NULL,
    batch_id          TEXT NOT NULL,
    target_level      TEXT NOT NULL,
    cutoff            TEXT NOT NULL,
    outcome_timestamp TEXT NOT NULL,
    status            TEXT NOT NULL,
    report_digest     TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    payload           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evaluation_runs_program_batch
    ON evaluation_runs_v2(program_id, batch_id, created_at);

-- ── M8 discovery-first candidate intelligence (append-only) ────────────────
CREATE TABLE IF NOT EXISTS batch_discovery_runs (
    run_id       TEXT PRIMARY KEY,
    program_id   TEXT NOT NULL,
    batch_id     TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    payload      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_batch_runs_program
    ON batch_discovery_runs(program_id, batch_id, started_at);

CREATE TABLE IF NOT EXISTS batch_discovery_observations (
    observation_id TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL,
    program_id     TEXT NOT NULL,
    batch_id       TEXT NOT NULL,
    hypothesis_key TEXT NOT NULL,
    observed_at    TEXT NOT NULL,
    origin_key     TEXT NOT NULL,
    stance         TEXT NOT NULL,
    payload        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_batch_observations_asof
    ON batch_discovery_observations(program_id, batch_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_batch_observations_hypothesis
    ON batch_discovery_observations(hypothesis_key, observed_at);

CREATE TABLE IF NOT EXISTS batch_candidate_hypotheses (
    revision_id    TEXT PRIMARY KEY,
    hypothesis_id  TEXT NOT NULL,
    revision       INTEGER NOT NULL,
    run_id         TEXT NOT NULL,
    program_id     TEXT NOT NULL,
    batch_id       TEXT NOT NULL,
    state          TEXT NOT NULL,
    confidence_pct INTEGER NOT NULL,
    observed_at    TEXT NOT NULL,
    input_digest   TEXT NOT NULL,
    payload        TEXT NOT NULL,
    UNIQUE(hypothesis_id, input_digest)
);
CREATE INDEX IF NOT EXISTS idx_batch_hypotheses_latest
    ON batch_candidate_hypotheses(program_id, batch_id, hypothesis_id, revision DESC);
"""


# ── Connection management ──────────────────────────────────────────────────────


class Database:
    """
    Thin wrapper around sqlite3.

    Usage pattern:
        db = Database("data/intelligence.db")
        db.write_event(event)
        events = db.unprocessed_events("validator")
    """

    def __init__(self, db_path: str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        """Create all tables and indexes if they don't exist."""
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._apply_schema_migrations(conn)
        self._quarantine_legacy_invalid_candidates()
        # Polluted projections are integrity-gated on read and reconciled only
        # through the explicit dry-run-first M6 maintenance command.

    @staticmethod
    def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
        return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def _apply_schema_migrations(self, conn: sqlite3.Connection) -> None:
        """Apply additive migrations without rewriting immutable event rows."""
        event_columns = self._column_names(conn, "events")
        if "occurred_at_precision" not in event_columns:
            conn.execute(
                "ALTER TABLE events ADD COLUMN occurred_at_precision "
                "TEXT NOT NULL DEFAULT 'unknown'"
            )
        if "validity" not in event_columns:
            conn.execute("ALTER TABLE events ADD COLUMN validity TEXT")

        edge_columns = self._column_names(conn, "graph_edges")
        if "validity_precision" not in edge_columns:
            conn.execute(
                "ALTER TABLE graph_edges ADD COLUMN validity_precision "
                "TEXT NOT NULL DEFAULT 'unknown'"
            )
        if "temporal_status" not in edge_columns:
            conn.execute(
                "ALTER TABLE graph_edges ADD COLUMN temporal_status TEXT NOT NULL DEFAULT 'unknown'"
            )

        additive_columns = {
            "features": {
                "schema_version": "TEXT NOT NULL DEFAULT '4.0'",
                "feature_version": "TEXT NOT NULL DEFAULT 'legacy-1'",
                "as_of": "TEXT NOT NULL DEFAULT ''",
                "computed_at": "TEXT NOT NULL DEFAULT ''",
                "evidence_ids": "TEXT NOT NULL DEFAULT '[]'",
                "observation_ids": "TEXT NOT NULL DEFAULT '[]'",
                "crm_action_ids": "TEXT NOT NULL DEFAULT '[]'",
                "formula": "TEXT NOT NULL DEFAULT 'legacy_direct_value'",
                "parameters": "TEXT NOT NULL DEFAULT '{}'",
                "input_digest": "TEXT NOT NULL DEFAULT ''",
                "program_id": "TEXT",
                "batch_id": "TEXT",
            },
            "signals": {
                "feature_ids": "TEXT NOT NULL DEFAULT '[]'",
                "crm_action_ids": "TEXT NOT NULL DEFAULT '[]'",
                "signal_version": "TEXT NOT NULL DEFAULT 'legacy-1'",
                "input_digest": "TEXT NOT NULL DEFAULT ''",
            },
            "hypotheses": {
                "hypothesis_version": "TEXT NOT NULL DEFAULT 'legacy-1'",
                "input_digest": "TEXT NOT NULL DEFAULT ''",
            },
            "rankings": {
                "signal_ids": "TEXT NOT NULL DEFAULT '[]'",
                "feature_ids": "TEXT NOT NULL DEFAULT '[]'",
                "crm_action_ids": "TEXT NOT NULL DEFAULT '[]'",
                "ranking_version": "TEXT NOT NULL DEFAULT 'legacy-1'",
                "input_digest": "TEXT NOT NULL DEFAULT ''",
            },
            "recommendations": {
                "schema_version": "TEXT NOT NULL DEFAULT '5.0'",
                "recommendation_version": "TEXT NOT NULL DEFAULT 'legacy-1'",
                "signal_ids": "TEXT NOT NULL DEFAULT '[]'",
                "feature_ids": "TEXT NOT NULL DEFAULT '[]'",
                "crm_action_ids": "TEXT NOT NULL DEFAULT '[]'",
                "input_digest": "TEXT NOT NULL DEFAULT ''",
                "supersedes_recommendation_id": "TEXT",
            },
            "alerts": {
                "schema_version": "TEXT NOT NULL DEFAULT '5.0'",
                "alert_version": "TEXT NOT NULL DEFAULT 'legacy-1'",
                "crm_action_ids": "TEXT NOT NULL DEFAULT '[]'",
            },
            "crm_events": {
                "schema_version": "TEXT NOT NULL DEFAULT '5.0'",
                "action_version": "TEXT NOT NULL DEFAULT 'legacy-1'",
                "feedback_kind": "TEXT NOT NULL DEFAULT 'workflow'",
                "ranking_id": "TEXT",
                "provenance": "TEXT NOT NULL DEFAULT '{}'",
                "occurred_at": "TEXT NOT NULL DEFAULT ''",
                "observed_at": "TEXT NOT NULL DEFAULT ''",
                "input_digest": "TEXT NOT NULL DEFAULT ''",
            },
        }
        for table, definitions in additive_columns.items():
            columns = self._column_names(conn, table)
            for column, definition in definitions.items():
                if column not in columns:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.execute("UPDATE features SET as_of=observed_at WHERE as_of='' OR as_of IS NULL")
        conn.execute(
            "UPDATE features SET computed_at=observed_at "
            "WHERE computed_at='' OR computed_at IS NULL"
        )
        conn.execute(
            "UPDATE crm_events SET occurred_at=created_at "
            "WHERE occurred_at='' OR occurred_at IS NULL"
        )
        conn.execute(
            "UPDATE crm_events SET observed_at=created_at "
            "WHERE observed_at='' OR observed_at IS NULL"
        )
        conn.execute("UPDATE crm_events SET action='stage_changed' WHERE action='stage_advanced'")
        feature_sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='features'"
        ).fetchone()
        feature_sql = str(feature_sql_row["sql"] or "") if feature_sql_row else ""
        if "unique(entity_id,name)" in "".join(feature_sql.casefold().split()):
            self._rebuild_legacy_features_table(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_features_entity_asof ON features(entity_id, as_of DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_features_name_version "
            "ON features(name, feature_version)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_crm_events_observed "
            "ON crm_events(entity_id, observed_at, created_at)"
        )

        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_observed ON events(observed_at)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_edges_temporal "
            "ON graph_edges(observed_at, valid_from, valid_to)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            ("0001_temporal_foundation", datetime.now(timezone.utc).isoformat()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            ("0002_historical_evaluation", datetime.now(timezone.utc).isoformat()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            ("0003_feature_lineage", datetime.now(timezone.utc).isoformat()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            ("0004_crm_feedback", datetime.now(timezone.utc).isoformat()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            ("0005_batch_discovery", datetime.now(timezone.utc).isoformat()),
        )

    @staticmethod
    def _rebuild_legacy_features_table(conn: sqlite3.Connection) -> None:
        """Preserve rows while removing the legacy one-value-per-name constraint."""
        conn.execute("DROP TABLE IF EXISTS features_m4_migration")
        conn.execute(
            """
            CREATE TABLE features_m4_migration (
                feature_id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL DEFAULT '4.0',
                feature_version TEXT NOT NULL DEFAULT 'legacy-1',
                entity_id TEXT NOT NULL,
                name TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT,
                source TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                as_of TEXT NOT NULL,
                computed_at TEXT NOT NULL,
                evidence_ids TEXT NOT NULL DEFAULT '[]',
                observation_ids TEXT NOT NULL DEFAULT '[]',
                crm_action_ids TEXT NOT NULL DEFAULT '[]',
                formula TEXT NOT NULL DEFAULT 'legacy_direct_value',
                parameters TEXT NOT NULL DEFAULT '{}',
                input_digest TEXT NOT NULL DEFAULT '',
                program_id TEXT,
                batch_id TEXT,
                confidence REAL NOT NULL
            )
            """
        )
        columns = (
            "feature_id, schema_version, feature_version, entity_id, name, value, unit, "
            "source, observed_at, as_of, computed_at, evidence_ids, observation_ids, "
            "crm_action_ids, formula, parameters, input_digest, program_id, batch_id, "
            "confidence"
        )
        conn.execute(
            f"INSERT INTO features_m4_migration ({columns}) SELECT {columns} FROM features"
        )
        conn.execute("DROP TABLE features")
        conn.execute("ALTER TABLE features_m4_migration RENAME TO features")

    def _quarantine_legacy_invalid_candidates(self) -> None:
        """Stop pre-gate events without rewriting the immutable event log.

        Earlier versions persisted raw search/platform labels before identity
        validation.  Their source observations remain available for audit, but
        they must not wake the resolver or any derived worker after upgrade.
        """
        from venture_intelligence.pipeline.entity_integrity import validate_candidate

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE entity_type IN ('company', 'person')"
            ).fetchall()
            for row in rows:
                try:
                    event = self._row_to_event(row)
                    validation = validate_candidate(event, self)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if validation.accepted:
                    continue
                rejection_id = f"legacy:{event.event_id}"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO candidate_rejections
                      (rejection_id, candidate_label, entity_type, source, source_url,
                       query, reason, confidence, provenance, raw_candidate, rejected_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        rejection_id,
                        validation.label or event.entity_id,
                        event.entity_type.value,
                        event.source.value,
                        event.source_url,
                        str(event.metadata.get("query") or "") or None,
                        validation.reason or "candidate_validation_failed",
                        event.confidence,
                        json.dumps(
                            {
                                "event_id": event.event_id,
                                "legacy": True,
                                "metadata": event.metadata,
                            }
                        ),
                        json.dumps(event.raw_payload),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                conn.execute(
                    """
                    UPDATE event_queue
                    SET status='failed',
                        error='candidate rejected by legacy integrity migration',
                        processed_at=?
                    WHERE event_id=? AND status IN ('pending', 'processing')
                    """,
                    (datetime.now(timezone.utc).isoformat(), event.event_id),
                )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection with row_factory set; commit on clean exit."""
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")  # concurrent reads during writes
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _ts(dt: datetime | None) -> str | None:
        """Convert datetime → ISO-8601 UTC string, or None."""
        return dt.isoformat() if dt else None

    @staticmethod
    def _from_ts(s: str | None) -> datetime | None:
        """Parse ISO-8601 string → UTC datetime, or None."""
        if not s:
            return None
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    # ════════════════════════════════════════════════════════════════════════════
    # EVENTS
    # ════════════════════════════════════════════════════════════════════════════

    def write_event(self, event: CanonicalEvent) -> None:
        """
        Persist a CanonicalEvent.  Idempotent on event_id (INSERT OR IGNORE).
        This is the only entry point for new events — nothing else writes
        directly to the events table.
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO events (
                    event_id, schema_version, event_type, entity_type, entity_id,
                    source, source_url, occurred_at, occurred_at_precision,
                    observed_at, ingested_at, processed_at, ranked_at,
                    notified_at, validity, confidence, raw_payload, metadata
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event.event_id,
                    event.schema_version,
                    event.event_type,
                    event.entity_type,
                    event.entity_id,
                    event.source,
                    event.source_url,
                    self._ts(event.occurred_at),
                    event.occurred_at_precision,
                    self._ts(event.observed_at),
                    self._ts(event.ingested_at),
                    self._ts(event.processed_at),
                    self._ts(event.ranked_at),
                    self._ts(event.notified_at),
                    json.dumps(event.validity.model_dump(mode="json"))
                    if event.validity is not None
                    else None,
                    event.confidence,
                    json.dumps(event.raw_payload),
                    json.dumps(event.metadata),
                ),
            )
            # Enqueue for all pipeline workers
            for worker in [
                "validator",
                "resolver",
                "graph_builder",
                "evidence_builder",
                "signal_extractor",
                "ranker",
                "recommender",
                "notifier",
            ]:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO event_queue
                      (event_id, worker, status, created_at)
                    VALUES (?,?,'pending',?)
                    """,
                    (event.event_id, worker, self._ts(event.ingested_at)),
                )

    def mark_processed(self, event_id: str, worker: str) -> None:
        """Mark one worker's queue entry as done."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE event_queue SET status='done', processed_at=?
                WHERE event_id=? AND worker=?
                """,
                (now, event_id, worker),
            )

    def mark_failed(self, event_id: str, worker: str, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE event_queue SET status='failed', error=?
                WHERE event_id=? AND worker=?
                """,
                (error, event_id, worker),
            )

    def pending_events(self, worker: str, limit: int = 100) -> list[CanonicalEvent]:
        """Return up to `limit` events not yet processed by `worker`."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.* FROM events e
                JOIN event_queue q ON q.event_id = e.event_id
                WHERE q.worker=? AND q.status='pending'
                ORDER BY e.ingested_at ASC
                LIMIT ?
                """,
                (worker, limit),
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def queue_status(self, event_id: str, worker: str) -> str | None:
        """Return a worker queue status for downstream validation gates."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM event_queue WHERE event_id=? AND worker=?",
                (event_id, worker),
            ).fetchone()
        return row["status"] if row else None

    def get_events_for_entity(self, entity_id: str) -> list[CanonicalEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE entity_id=? ORDER BY occurred_at ASC",
                (entity_id,),
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def _row_to_event(self, row: sqlite3.Row) -> CanonicalEvent:
        return CanonicalEvent(
            event_id=row["event_id"],
            schema_version=row["schema_version"],
            event_type=row["event_type"],
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            source=row["source"],
            source_url=row["source_url"],
            occurred_at=self._from_ts(row["occurred_at"]),
            occurred_at_precision=row["occurred_at_precision"],
            observed_at=self._from_ts(row["observed_at"]),
            ingested_at=self._from_ts(row["ingested_at"]),
            processed_at=self._from_ts(row["processed_at"]),
            ranked_at=self._from_ts(row["ranked_at"]),
            notified_at=self._from_ts(row["notified_at"]),
            validity=json.loads(row["validity"]) if row["validity"] else None,
            confidence=row["confidence"],
            raw_payload=json.loads(row["raw_payload"]),
            metadata=json.loads(row["metadata"]),
        )

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

    # ════════════════════════════════════════════════════════════════════════════
    # KNOWLEDGE GRAPH
    # ════════════════════════════════════════════════════════════════════════════

    def write_graph_node(self, node: GraphNode) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO graph_nodes
                  (node_id, node_type, label, properties, created_at, updated_at)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    node.node_id,
                    node.node_type,
                    node.label,
                    json.dumps(node.properties),
                    self._ts(node.created_at),
                    now,
                ),
            )

    def write_graph_edge(self, edge: GraphEdge) -> None:
        """
        Upsert an edge.  Re-observed relationships update confidence
        and evidence_ids rather than creating duplicates.
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO graph_edges
                  (edge_id, source_id, target_id, edge_type, confidence,
                   observed_at, valid_from, valid_to, validity_precision,
                   temporal_status, evidence_ids, properties)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_id, target_id, edge_type) DO UPDATE SET
                  confidence   = MAX(confidence, excluded.confidence),
                  observed_at  = excluded.observed_at,
                  valid_from   = COALESCE(excluded.valid_from, graph_edges.valid_from),
                  valid_to     = COALESCE(excluded.valid_to, graph_edges.valid_to),
                  validity_precision = CASE
                    WHEN excluded.validity_precision = 'unknown'
                    THEN graph_edges.validity_precision
                    ELSE excluded.validity_precision
                  END,
                  temporal_status = CASE
                    WHEN excluded.temporal_status = 'unknown'
                    THEN graph_edges.temporal_status
                    ELSE excluded.temporal_status
                  END,
                  evidence_ids = excluded.evidence_ids
                """,
                (
                    edge.edge_id,
                    edge.source_id,
                    edge.target_id,
                    edge.edge_type,
                    edge.confidence,
                    self._ts(edge.observed_at),
                    self._ts(edge.valid_from),
                    self._ts(edge.valid_to),
                    edge.validity_precision,
                    edge.temporal_status,
                    json.dumps(edge.evidence_ids),
                    json.dumps(edge.properties),
                ),
            )

    def get_node_neighbors(
        self, node_id: str, edge_type: str | None = None
    ) -> list[dict[str, Any]]:
        """Return all neighbors of a node, optionally filtered by edge type."""
        with self._connect() as conn:
            if edge_type:
                rows = conn.execute(
                    """
                    SELECT n.*, e.edge_id, e.edge_type,
                           e.confidence as edge_confidence, e.observed_at,
                           e.valid_from, e.valid_to, e.validity_precision,
                           e.temporal_status, e.evidence_ids
                    FROM graph_nodes n
                    JOIN graph_edges e ON e.target_id = n.node_id
                    WHERE e.source_id=? AND e.edge_type=?
                    """,
                    (node_id, edge_type),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT n.*, e.edge_id, e.edge_type,
                           e.confidence as edge_confidence, e.observed_at,
                           e.valid_from, e.valid_to, e.validity_precision,
                           e.temporal_status, e.evidence_ids
                    FROM graph_nodes n
                    JOIN graph_edges e ON e.target_id = n.node_id
                    WHERE e.source_id=?
                    """,
                    (node_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    def has_edge(self, source_id: str, target_id: str, edge_type: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM graph_edges WHERE source_id=? AND target_id=? AND edge_type=?",
                (source_id, target_id, edge_type),
            ).fetchone()
        return row is not None

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

    # ════════════════════════════════════════════════════════════════════════════
    # RANKINGS
    # ════════════════════════════════════════════════════════════════════════════

    def write_ranking(self, ranking: FounderRanking) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO rankings
                  (ranking_id, entity_id, rank, composite_score, dimensions,
                   hypothesis_ids, evidence_ids, signal_ids, feature_ids,
                   crm_action_ids, ranking_version, input_digest, reasoning,
                   confidence, ranked_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    ranking.ranking_id,
                    ranking.entity_id,
                    ranking.rank,
                    ranking.composite_score,
                    json.dumps([d.model_dump() for d in ranking.dimensions]),
                    json.dumps(ranking.hypothesis_ids),
                    json.dumps(ranking.evidence_ids),
                    json.dumps(ranking.signal_ids),
                    json.dumps(ranking.feature_ids),
                    json.dumps(ranking.crm_action_ids),
                    ranking.ranking_version,
                    ranking.input_digest,
                    ranking.reasoning,
                    ranking.confidence,
                    self._ts(ranking.ranked_at),
                ),
            )

    def get_rankings(
        self,
        limit: int = 100,
        *,
        as_of: datetime | None = None,
    ) -> list[FounderRanking]:
        """Return top-N entities ordered by composite_score descending."""
        with self._connect() as conn:
            cutoff_clause = "" if as_of is None else "WHERE ranked_at<=?"
            params: tuple[object, ...] = () if as_of is None else (self._ts(as_of),)
            rows = conn.execute(
                f"""
                SELECT * FROM (
                    SELECT r.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY entity_id
                               ORDER BY ranked_at DESC, ranking_id DESC
                           ) AS latest_ordinal
                    FROM rankings r {cutoff_clause}
                ) latest
                WHERE latest_ordinal=1
                ORDER BY composite_score DESC, entity_id ASC LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return [self._row_to_ranking(r) for r in rows]

    def get_ranking_for_entity(
        self,
        entity_id: str,
        *,
        as_of: datetime | None = None,
    ) -> FounderRanking | None:
        with self._connect() as conn:
            if as_of is None:
                row = conn.execute(
                    "SELECT * FROM rankings WHERE entity_id=? ORDER BY ranked_at DESC LIMIT 1",
                    (entity_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM rankings WHERE entity_id=? AND ranked_at<=? "
                    "ORDER BY ranked_at DESC LIMIT 1",
                    (entity_id, self._ts(as_of)),
                ).fetchone()
        return self._row_to_ranking(row) if row else None

    def get_ranking_history(
        self,
        entity_id: str,
        *,
        as_of: datetime | None = None,
        limit: int = 20,
    ) -> list[FounderRanking]:
        with self._connect() as conn:
            if as_of is None:
                rows = conn.execute(
                    "SELECT * FROM rankings WHERE entity_id=? "
                    "ORDER BY ranked_at DESC, ranking_id LIMIT ?",
                    (entity_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM rankings WHERE entity_id=? AND ranked_at<=? "
                    "ORDER BY ranked_at DESC, ranking_id LIMIT ?",
                    (entity_id, self._ts(as_of), limit),
                ).fetchall()
        return [self._row_to_ranking(row) for row in rows]

    def _row_to_ranking(self, row: sqlite3.Row) -> FounderRanking:
        from venture_intelligence.shared.models import RankingDimension

        return FounderRanking(
            ranking_id=row["ranking_id"],
            entity_id=row["entity_id"],
            rank=row["rank"],
            composite_score=row["composite_score"],
            dimensions=[RankingDimension(**d) for d in json.loads(row["dimensions"])],
            hypothesis_ids=json.loads(row["hypothesis_ids"]),
            evidence_ids=json.loads(row["evidence_ids"]),
            signal_ids=json.loads(row["signal_ids"]),
            feature_ids=json.loads(row["feature_ids"]),
            crm_action_ids=json.loads(row["crm_action_ids"]),
            ranking_version=row["ranking_version"],
            input_digest=row["input_digest"],
            reasoning=row["reasoning"],
            confidence=row["confidence"],
            ranked_at=self._from_ts(row["ranked_at"]),
        )

    # ════════════════════════════════════════════════════════════════════════════
    # RECOMMENDATIONS + ALERTS
    # ════════════════════════════════════════════════════════════════════════════

    def write_recommendation(self, rec: Recommendation) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO recommendations
                  (recommendation_id, schema_version, recommendation_version,
                   entity_id, action, reasoning, evidence_ids, ranking_id,
                   signal_ids, feature_ids, crm_action_ids, input_digest,
                   supersedes_recommendation_id, confidence, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    rec.recommendation_id,
                    rec.schema_version,
                    rec.recommendation_version,
                    rec.entity_id,
                    rec.action,
                    rec.reasoning,
                    json.dumps(rec.evidence_ids),
                    rec.ranking_id,
                    json.dumps(rec.signal_ids),
                    json.dumps(rec.feature_ids),
                    json.dumps(rec.crm_action_ids),
                    rec.input_digest,
                    rec.supersedes_recommendation_id,
                    rec.confidence,
                    self._ts(rec.created_at),
                ),
            )

    def get_recommendation_for_entity(
        self,
        entity_id: str,
        *,
        as_of: datetime | None = None,
    ) -> Recommendation | None:
        with self._connect() as conn:
            if as_of is None:
                row = conn.execute(
                    "SELECT * FROM recommendations WHERE entity_id=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (entity_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM recommendations WHERE entity_id=? AND created_at<=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (entity_id, self._ts(as_of)),
                ).fetchone()
        if row is None:
            return None
        return self._row_to_recommendation(row)

    def get_recommendation(self, recommendation_id: str) -> Recommendation | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM recommendations WHERE recommendation_id=?",
                (recommendation_id,),
            ).fetchone()
        return self._row_to_recommendation(row) if row is not None else None

    def get_recommendations_for_entity(
        self,
        entity_id: str,
        *,
        as_of: datetime | None = None,
    ) -> list[Recommendation]:
        with self._connect() as conn:
            if as_of is None:
                rows = conn.execute(
                    "SELECT * FROM recommendations WHERE entity_id=? "
                    "ORDER BY created_at DESC, recommendation_id",
                    (entity_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM recommendations WHERE entity_id=? AND created_at<=? "
                    "ORDER BY created_at DESC, recommendation_id",
                    (entity_id, self._ts(as_of)),
                ).fetchall()
        return [self._row_to_recommendation(row) for row in rows]

    def _row_to_recommendation(self, row: sqlite3.Row) -> Recommendation:
        return Recommendation(
            recommendation_id=row["recommendation_id"],
            schema_version=row["schema_version"],
            recommendation_version=row["recommendation_version"],
            entity_id=row["entity_id"],
            action=row["action"],
            reasoning=row["reasoning"],
            evidence_ids=json.loads(row["evidence_ids"]),
            ranking_id=row["ranking_id"],
            signal_ids=json.loads(row["signal_ids"]),
            feature_ids=json.loads(row["feature_ids"]),
            crm_action_ids=json.loads(row["crm_action_ids"]),
            input_digest=row["input_digest"],
            supersedes_recommendation_id=row["supersedes_recommendation_id"],
            confidence=row["confidence"],
            created_at=self._from_ts(row["created_at"]),
        )

    def write_recommendation_lifecycle(self, event: RecommendationLifecycleEvent) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO recommendation_lifecycle_events
                  (lifecycle_event_id, recommendation_id, entity_id, status,
                   actor, reason, provenance, occurred_at)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    event.lifecycle_event_id,
                    event.recommendation_id,
                    event.entity_id,
                    event.status,
                    event.actor,
                    event.reason,
                    json.dumps(event.provenance, sort_keys=True),
                    self._ts(event.occurred_at),
                ),
            )

    def get_recommendation_lifecycle(
        self,
        recommendation_id: str,
        *,
        as_of: datetime | None = None,
    ) -> list[RecommendationLifecycleEvent]:
        query = "SELECT * FROM recommendation_lifecycle_events WHERE recommendation_id=?"
        params: list[object] = [recommendation_id]
        if as_of is not None:
            query += " AND occurred_at<=?"
            params.append(self._ts(as_of))
        query += " ORDER BY occurred_at, lifecycle_event_id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            RecommendationLifecycleEvent(
                lifecycle_event_id=row["lifecycle_event_id"],
                recommendation_id=row["recommendation_id"],
                entity_id=row["entity_id"],
                status=row["status"],
                actor=row["actor"],
                reason=row["reason"],
                provenance=json.loads(row["provenance"]),
                occurred_at=self._from_ts(row["occurred_at"]),
            )
            for row in rows
        ]

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

    # ════════════════════════════════════════════════════════════════════════════
    # EVALUATION
    # ════════════════════════════════════════════════════════════════════════════

    def write_evaluation(self, result: EvaluationResult) -> None:
        import uuid

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO evaluation_results VALUES
                  (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(uuid.uuid4()),
                    result.batch,
                    self._ts(result.evaluated_at),
                    result.precision_at_10,
                    result.precision_at_25,
                    result.recall,
                    result.avg_lead_time_days,
                    result.median_lead_time_days,
                    result.false_positives,
                    result.missed_companies,
                    result.alert_acceptance_rate,
                    result.investment_conversion_rate,
                    result.total_entities_ranked,
                    result.notes,
                ),
            )

    def get_evaluations(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM evaluation_results ORDER BY evaluated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def write_ground_truth_dataset(self, dataset: Any) -> None:
        """Persist a validated, immutable ground-truth snapshot."""
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT payload FROM ground_truth_datasets WHERE dataset_id=?",
                (dataset.dataset_id,),
            ).fetchone()
            payload = dataset.model_dump_json()
            if existing is not None:
                # Identical semantic imports are idempotent.  The first import
                # timestamp remains authoritative for audit history.
                return
            conn.execute(
                """
                INSERT INTO ground_truth_datasets
                  (dataset_id, program_id, batch_id, target_level,
                   outcome_timestamp, digest, imported_at, payload)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    dataset.dataset_id,
                    dataset.program_id,
                    dataset.batch_id,
                    dataset.target_level.value,
                    self._ts(dataset.outcome_timestamp),
                    dataset.digest,
                    self._ts(dataset.imported_at),
                    payload,
                ),
            )

    def get_ground_truth_dataset(self, dataset_id: str) -> Any | None:
        from venture_intelligence.evaluation.models import GroundTruthDataset

        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM ground_truth_datasets WHERE dataset_id=?",
                (dataset_id,),
            ).fetchone()
        return GroundTruthDataset.model_validate_json(row["payload"]) if row else None

    def write_evaluation_run(self, result: Any) -> None:
        """Persist only the evaluation report; operational projections stay untouched."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO evaluation_runs_v2
                  (evaluation_run_id, program_id, batch_id, target_level,
                   cutoff, outcome_timestamp, status, report_digest, created_at, payload)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    result.evaluation_run_id,
                    result.program_id,
                    result.batch_id,
                    result.target_level.value,
                    self._ts(result.cutoff),
                    self._ts(result.outcome_timestamp),
                    result.status.value,
                    result.report_digest,
                    self._ts(result.created_at),
                    result.model_dump_json(),
                ),
            )

    def get_evaluation_run(self, evaluation_run_id: str) -> Any | None:
        from venture_intelligence.evaluation.models import EvaluationRun

        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM evaluation_runs_v2 WHERE evaluation_run_id=?",
                (evaluation_run_id,),
            ).fetchone()
        return EvaluationRun.model_validate_json(row["payload"]) if row else None

    def list_evaluation_runs(self) -> list[Any]:
        from venture_intelligence.evaluation.models import EvaluationRun

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM evaluation_runs_v2 ORDER BY created_at DESC"
            ).fetchall()
        return [EvaluationRun.model_validate_json(row["payload"]) for row in rows]

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


def _research_published_at(provenance: list[dict[str, Any]]) -> str | None:
    if not provenance:
        return None
    raw_payload = provenance[0].get("raw_payload")
    if not isinstance(raw_payload, dict):
        return None
    research_evidence = raw_payload.get("research_evidence")
    if not isinstance(research_evidence, dict):
        return None
    value = research_evidence.get("published_at")
    return str(value) if value else None


def _json_object(value: Any) -> dict[str, Any]:
    parsed = _json_value(value)
    return parsed if isinstance(parsed, dict) else {}


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value
