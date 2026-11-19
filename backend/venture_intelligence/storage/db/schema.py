"""SQLite DDL, additive schema migrations and legacy data repairs."""

import json
import sqlite3
from datetime import datetime, timezone

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


class SchemaMixin:
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
