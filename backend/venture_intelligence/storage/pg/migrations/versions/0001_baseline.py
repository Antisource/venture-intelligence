"""PostgreSQL baseline: the current SQLite schema plus tier-1 append-only triggers.

Revision ID: 0001
Revises:
Create Date: 2026-11-24

Creates every table of ``storage/db/schema.py`` and the tables SQLite creates
lazily (``investigations``, ``classifications``, ``quarantined_entities``),
translated faithfully: TEXT -> text, REAL -> double precision, INTEGER ->
bigint, AUTOINCREMENT -> identity (BY DEFAULT, so imported ids survive).
Timestamps and JSON intentionally stay text here; ``timestamptz``/``jsonb``,
provenance CHECKs and foreign keys arrive with the per-domain ports
(ADR-0001 §6, ADR-0002 §5).

ADR-0002 §3 tier 1: rows of the evidence-record tables that exist today
(``events``, ``observations``, ``candidate_rejections``,
``batch_discovery_observations``) can be inserted but never updated, deleted or
truncated, for every role including the owner.

TODO(ADR-0002 §2-§4): roles (vi_owner, vi_app, vi_erasure, vi_readonly) and
privilege revocation; the erasure path (``vi_erase_subject`` and
``vi_replay_erasure``) sets a transaction-local flag that
``vi_reject_append_only_change`` must honour; tier-2 triggers on crm_events,
the lifecycle tables and features; moving pipeline progress columns out of
``events`` into ``event_processing``. Until that link lands, the SQLite
pipeline's in-place ``events`` updates have no Postgres equivalent.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy: later revisions must not change what this baseline installs.
TIER1_APPEND_ONLY_TABLES = (
    "events",
    "observations",
    "candidate_rejections",
    "batch_discovery_observations",
)

_REJECT_FUNCTION = """
CREATE FUNCTION vi_reject_append_only_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- TODO(ADR-0002 §4): allow the change only when the transaction-local
    -- erasure flag set by the SECURITY DEFINER erasure functions is present.
    RAISE EXCEPTION 'vi_append_only: % on %.% is not allowed', TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation',
              HINT = 'Tier-1 evidence rows are append-only (ADR-0002 §3).';
END;
$$
"""


def _install_append_only_triggers() -> None:
    op.execute(_REJECT_FUNCTION)
    for table in TIER1_APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_append_only_row BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION vi_reject_append_only_change()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_append_only_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION vi_reject_append_only_change()"
        )


def _drop_append_only_triggers() -> None:
    for table in TIER1_APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only_truncate ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only_row ON {table}")
    op.execute("DROP FUNCTION IF EXISTS vi_reject_append_only_change()")


def upgrade() -> None:
    op.create_table(
        "alert_lifecycle_events",
        sa.Column("lifecycle_event_id", sa.Text(), nullable=False),
        sa.Column("alert_id", sa.Text(), nullable=False),
        sa.Column("recommendation_id", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("delivery", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("provenance", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("occurred_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("lifecycle_event_id", name=op.f("pk_alert_lifecycle_events")),
    )
    op.create_index(
        "idx_alert_lifecycle", "alert_lifecycle_events", ["alert_id", "occurred_at"], unique=False
    )
    op.create_table(
        "alerts",
        sa.Column("alert_id", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), server_default=sa.text("'5.0'"), nullable=False),
        sa.Column("alert_version", sa.Text(), server_default=sa.text("'legacy-1'"), nullable=False),
        sa.Column("recommendation_id", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("entity_name", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("evidence_summary", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("rank", sa.BigInteger(), nullable=True),
        sa.Column("confidence", sa.Double(), nullable=False),
        sa.Column("source_urls", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("crm_action_ids", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("fired_at", sa.Text(), nullable=False),
        sa.Column("notified_at", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("alert_id", name=op.f("pk_alerts")),
    )
    op.create_index("idx_alerts_entity", "alerts", ["entity_id"], unique=False)
    op.create_index("idx_alerts_fired", "alerts", ["fired_at"], unique=False)
    op.create_table(
        "batch_candidate_hypotheses",
        sa.Column("revision_id", sa.Text(), nullable=False),
        sa.Column("hypothesis_id", sa.Text(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("program_id", sa.Text(), nullable=False),
        sa.Column("batch_id", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("confidence_pct", sa.BigInteger(), nullable=False),
        sa.Column("observed_at", sa.Text(), nullable=False),
        sa.Column("input_digest", sa.Text(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("revision_id", name=op.f("pk_batch_candidate_hypotheses")),
        sa.UniqueConstraint(
            "hypothesis_id",
            "input_digest",
            name=op.f("uq_batch_candidate_hypotheses_hypothesis_id_input_digest"),
        ),
    )
    op.create_index(
        "idx_batch_hypotheses_latest",
        "batch_candidate_hypotheses",
        ["program_id", "batch_id", "hypothesis_id", sa.literal_column("revision DESC")],
        unique=False,
    )
    op.create_table(
        "batch_discovery_observations",
        sa.Column("observation_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("program_id", sa.Text(), nullable=False),
        sa.Column("batch_id", sa.Text(), nullable=False),
        sa.Column("hypothesis_key", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.Text(), nullable=False),
        sa.Column("origin_key", sa.Text(), nullable=False),
        sa.Column("stance", sa.Text(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("observation_id", name=op.f("pk_batch_discovery_observations")),
    )
    op.create_index(
        "idx_batch_observations_asof",
        "batch_discovery_observations",
        ["program_id", "batch_id", "observed_at"],
        unique=False,
    )
    op.create_index(
        "idx_batch_observations_hypothesis",
        "batch_discovery_observations",
        ["hypothesis_key", "observed_at"],
        unique=False,
    )
    op.create_table(
        "batch_discovery_runs",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("program_id", sa.Text(), nullable=False),
        sa.Column("batch_id", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("finished_at", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("input_digest", sa.Text(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_batch_discovery_runs")),
    )
    op.create_index(
        "idx_batch_runs_program",
        "batch_discovery_runs",
        ["program_id", "batch_id", "started_at"],
        unique=False,
    )
    op.create_table(
        "candidate_rejections",
        sa.Column("rejection_id", sa.Text(), nullable=False),
        sa.Column("candidate_label", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Double(), nullable=False),
        sa.Column("provenance", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("raw_candidate", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("rejected_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("rejection_id", name=op.f("pk_candidate_rejections")),
    )
    op.create_index(
        "idx_rejections_label", "candidate_rejections", ["candidate_label"], unique=False
    )
    op.create_table(
        "classifications",
        sa.Column("classification_id", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("person_type", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Double(), nullable=False),
        sa.Column("evidence_ids", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("classified_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("classification_id", name=op.f("pk_classifications")),
    )
    op.create_table(
        "companies",
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("aliases", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("slug", sa.Text(), nullable=True),
        sa.Column("yc_url", sa.Text(), nullable=True),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("batch", sa.Text(), nullable=True),
        sa.Column("tags", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("founder_ids", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "identity_confidence", sa.Double(), server_default=sa.text("0.9"), nullable=False
        ),
        sa.Column("first_seen_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("properties", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.PrimaryKeyConstraint("company_id", name=op.f("pk_companies")),
    )
    op.create_index("idx_companies_name", "companies", ["canonical_name"], unique=False)
    op.create_index("idx_companies_slug", "companies", ["slug"], unique=False)
    op.create_table(
        "crm_events",
        sa.Column("crm_event_id", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), server_default=sa.text("'5.0'"), nullable=False),
        sa.Column(
            "action_version", sa.Text(), server_default=sa.text("'legacy-1'"), nullable=False
        ),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("feedback_kind", sa.Text(), server_default=sa.text("'workflow'"), nullable=False),
        sa.Column("from_stage", sa.Text(), nullable=False),
        sa.Column("to_stage", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("partner", sa.Text(), nullable=True),
        sa.Column("ranking_id", sa.Text(), nullable=True),
        sa.Column("provenance", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("occurred_at", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("observed_at", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("input_digest", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.PrimaryKeyConstraint("crm_event_id", name=op.f("pk_crm_events")),
    )
    op.create_index("idx_crm_events_entity", "crm_events", ["entity_id"], unique=False)
    op.create_index(
        "idx_crm_events_observed",
        "crm_events",
        ["entity_id", "observed_at", "created_at"],
        unique=False,
    )
    op.create_table(
        "crm_records",
        sa.Column("crm_id", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), server_default=sa.text("'discovery'"), nullable=False),
        sa.Column("notes", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("decision", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("crm_id", name=op.f("pk_crm_records")),
        sa.UniqueConstraint("entity_id", name=op.f("uq_crm_records_entity_id")),
    )
    op.create_table(
        "entity_links",
        sa.Column("link_id", sa.Text(), nullable=False),
        sa.Column("canonical_id", sa.Text(), nullable=False),
        sa.Column("entity_kind", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Double(), nullable=False),
        sa.Column("linked_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("link_id", name=op.f("pk_entity_links")),
        sa.UniqueConstraint("source", "source_id", name=op.f("uq_entity_links_source_source_id")),
    )
    op.create_index("idx_links_canonical", "entity_links", ["canonical_id"], unique=False)
    op.create_table(
        "entity_validations",
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("evidence_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("provenance", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("validated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("entity_id", name=op.f("pk_entity_validations")),
    )
    op.create_index("idx_entity_validations_status", "entity_validations", ["status"], unique=False)
    op.create_table(
        "evaluation_results",
        sa.Column("eval_id", sa.Text(), nullable=False),
        sa.Column("batch", sa.Text(), nullable=False),
        sa.Column("evaluated_at", sa.Text(), nullable=False),
        sa.Column("precision_at_10", sa.Double(), nullable=False),
        sa.Column("precision_at_25", sa.Double(), nullable=False),
        sa.Column("recall", sa.Double(), nullable=False),
        sa.Column("avg_lead_time_days", sa.Double(), nullable=False),
        sa.Column("median_lead_time_days", sa.Double(), nullable=False),
        sa.Column("false_positives", sa.BigInteger(), nullable=False),
        sa.Column("missed_companies", sa.BigInteger(), nullable=False),
        sa.Column("alert_acceptance_rate", sa.Double(), nullable=False),
        sa.Column("investment_conversion_rate", sa.Double(), nullable=False),
        sa.Column("total_entities_ranked", sa.BigInteger(), nullable=False),
        sa.Column("notes", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.PrimaryKeyConstraint("eval_id", name=op.f("pk_evaluation_results")),
    )
    op.create_table(
        "evaluation_runs_v2",
        sa.Column("evaluation_run_id", sa.Text(), nullable=False),
        sa.Column("program_id", sa.Text(), nullable=False),
        sa.Column("batch_id", sa.Text(), nullable=False),
        sa.Column("target_level", sa.Text(), nullable=False),
        sa.Column("cutoff", sa.Text(), nullable=False),
        sa.Column("outcome_timestamp", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("report_digest", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("evaluation_run_id", name=op.f("pk_evaluation_runs_v2")),
    )
    op.create_index(
        "idx_evaluation_runs_program_batch",
        "evaluation_runs_v2",
        ["program_id", "batch_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "event_queue",
        sa.Column("queue_id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("worker", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("processed_at", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("queue_id", name=op.f("pk_event_queue")),
        sa.UniqueConstraint("event_id", "worker", name=op.f("uq_event_queue_event_id_worker")),
    )
    op.create_index("idx_queue_status", "event_queue", ["status", "worker"], unique=False)
    op.create_table(
        "events",
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), server_default=sa.text("'1.0'"), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.Text(), nullable=False),
        sa.Column(
            "occurred_at_precision", sa.Text(), server_default=sa.text("'unknown'"), nullable=False
        ),
        sa.Column("observed_at", sa.Text(), nullable=False),
        sa.Column("ingested_at", sa.Text(), nullable=False),
        sa.Column("processed_at", sa.Text(), nullable=True),
        sa.Column("ranked_at", sa.Text(), nullable=True),
        sa.Column("notified_at", sa.Text(), nullable=True),
        sa.Column("validity", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Double(), nullable=False),
        sa.Column("raw_payload", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("metadata", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_events")),
    )
    op.create_index("idx_events_entity", "events", ["entity_id"], unique=False)
    op.create_index("idx_events_ingested", "events", ["ingested_at"], unique=False)
    op.create_index("idx_events_observed", "events", ["observed_at"], unique=False)
    op.create_index("idx_events_source", "events", ["source"], unique=False)
    op.create_index("idx_events_type", "events", ["event_type"], unique=False)
    op.create_table(
        "evidence",
        sa.Column("evidence_id", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("observation_ids", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("confidence", sa.Double(), nullable=False),
        sa.Column("observed_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("evidence_id", name=op.f("pk_evidence")),
    )
    op.create_index("idx_evidence_entity", "evidence", ["entity_id"], unique=False)
    op.create_table(
        "features",
        sa.Column("feature_id", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), server_default=sa.text("'4.0'"), nullable=False),
        sa.Column(
            "feature_version", sa.Text(), server_default=sa.text("'legacy-1'"), nullable=False
        ),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("value", sa.Double(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.Text(), nullable=False),
        sa.Column("as_of", sa.Text(), nullable=False),
        sa.Column("computed_at", sa.Text(), nullable=False),
        sa.Column("evidence_ids", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("observation_ids", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("crm_action_ids", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "formula", sa.Text(), server_default=sa.text("'legacy_direct_value'"), nullable=False
        ),
        sa.Column("parameters", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("input_digest", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("program_id", sa.Text(), nullable=True),
        sa.Column("batch_id", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Double(), nullable=False),
        sa.PrimaryKeyConstraint("feature_id", name=op.f("pk_features")),
    )
    op.create_index(
        "idx_features_entity_asof",
        "features",
        ["entity_id", sa.literal_column("as_of DESC")],
        unique=False,
    )
    op.create_index(
        "idx_features_name_version", "features", ["name", "feature_version"], unique=False
    )
    op.create_table(
        "graph_edges",
        sa.Column("edge_id", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("edge_type", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Double(), server_default=sa.text("0.9"), nullable=False),
        sa.Column("observed_at", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.Text(), nullable=True),
        sa.Column("valid_to", sa.Text(), nullable=True),
        sa.Column(
            "validity_precision", sa.Text(), server_default=sa.text("'unknown'"), nullable=False
        ),
        sa.Column(
            "temporal_status", sa.Text(), server_default=sa.text("'unknown'"), nullable=False
        ),
        sa.Column("evidence_ids", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("properties", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.PrimaryKeyConstraint("edge_id", name=op.f("pk_graph_edges")),
        sa.UniqueConstraint(
            "source_id",
            "target_id",
            "edge_type",
            name=op.f("uq_graph_edges_source_id_target_id_edge_type"),
        ),
    )
    op.create_index("idx_edges_source", "graph_edges", ["source_id"], unique=False)
    op.create_index("idx_edges_target", "graph_edges", ["target_id"], unique=False)
    op.create_index(
        "idx_edges_temporal", "graph_edges", ["observed_at", "valid_from", "valid_to"], unique=False
    )
    op.create_index("idx_edges_type", "graph_edges", ["edge_type"], unique=False)
    op.create_table(
        "graph_nodes",
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("node_type", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("properties", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("node_id", name=op.f("pk_graph_nodes")),
    )
    op.create_index("idx_nodes_label", "graph_nodes", ["label"], unique=False)
    op.create_index("idx_nodes_type", "graph_nodes", ["node_type"], unique=False)
    op.create_table(
        "ground_truth_datasets",
        sa.Column("dataset_id", sa.Text(), nullable=False),
        sa.Column("program_id", sa.Text(), nullable=False),
        sa.Column("batch_id", sa.Text(), nullable=False),
        sa.Column("target_level", sa.Text(), nullable=False),
        sa.Column("outcome_timestamp", sa.Text(), nullable=False),
        sa.Column("digest", sa.Text(), nullable=False),
        sa.Column("imported_at", sa.Text(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("dataset_id", name=op.f("pk_ground_truth_datasets")),
    )
    op.create_index(
        "idx_ground_truth_program_batch",
        "ground_truth_datasets",
        ["program_id", "batch_id", "imported_at"],
        unique=False,
    )
    op.create_table(
        "hypotheses",
        sa.Column("hypothesis_id", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("signal_ids", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "hypothesis_version", sa.Text(), server_default=sa.text("'legacy-1'"), nullable=False
        ),
        sa.Column("input_digest", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("probability", sa.Double(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("hypothesis_id", name=op.f("pk_hypotheses")),
    )
    op.create_index("idx_hypotheses_entity", "hypotheses", ["entity_id"], unique=False)
    op.create_table(
        "investigations",
        sa.Column("investigation_id", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'open'"), nullable=False),
        sa.Column("lead_partner", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("evidence_ids", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("open_questions", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("decision", sa.Text(), nullable=True),
        sa.Column("decision_reasoning", sa.Text(), nullable=True),
        sa.Column("opened_at", sa.Text(), nullable=False),
        sa.Column("concluded_at", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("investigation_id", name=op.f("pk_investigations")),
    )
    op.create_table(
        "observations",
        sa.Column("observation_id", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("fact", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("observed_at", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Double(), nullable=False),
        sa.PrimaryKeyConstraint("observation_id", name=op.f("pk_observations")),
    )
    op.create_index("idx_obs_entity", "observations", ["entity_id"], unique=False)
    op.create_table(
        "persons",
        sa.Column("person_id", sa.Text(), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("aliases", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("linkedin_url", sa.Text(), nullable=True),
        sa.Column("github_username", sa.Text(), nullable=True),
        sa.Column("twitter_handle", sa.Text(), nullable=True),
        sa.Column("current_title", sa.Text(), nullable=True),
        sa.Column("current_company_id", sa.Text(), nullable=True),
        sa.Column("batch", sa.Text(), nullable=True),
        sa.Column(
            "identity_confidence", sa.Double(), server_default=sa.text("0.9"), nullable=False
        ),
        sa.Column("first_seen_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("properties", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.PrimaryKeyConstraint("person_id", name=op.f("pk_persons")),
    )
    op.create_index("idx_persons_github", "persons", ["github_username"], unique=False)
    op.create_index("idx_persons_name", "persons", ["canonical_name"], unique=False)
    op.create_table(
        "quarantined_entities",
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("raw_record", sa.Text(), nullable=False),
        sa.Column("quarantined_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("entity_id", name=op.f("pk_quarantined_entities")),
    )
    op.create_table(
        "rankings",
        sa.Column("ranking_id", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("rank", sa.BigInteger(), nullable=False),
        sa.Column("composite_score", sa.Double(), nullable=False),
        sa.Column("dimensions", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("hypothesis_ids", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("evidence_ids", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("signal_ids", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("feature_ids", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("crm_action_ids", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "ranking_version", sa.Text(), server_default=sa.text("'legacy-1'"), nullable=False
        ),
        sa.Column("input_digest", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Double(), nullable=False),
        sa.Column("ranked_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("ranking_id", name=op.f("pk_rankings")),
    )
    op.create_index("idx_rankings_entity", "rankings", ["entity_id"], unique=False)
    op.create_index("idx_rankings_rank", "rankings", ["rank"], unique=False)
    op.create_index(
        "idx_rankings_score", "rankings", [sa.literal_column("composite_score DESC")], unique=False
    )
    op.create_table(
        "recommendation_lifecycle_events",
        sa.Column("lifecycle_event_id", sa.Text(), nullable=False),
        sa.Column("recommendation_id", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("provenance", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("occurred_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "lifecycle_event_id", name=op.f("pk_recommendation_lifecycle_events")
        ),
    )
    op.create_index(
        "idx_rec_lifecycle",
        "recommendation_lifecycle_events",
        ["recommendation_id", "occurred_at"],
        unique=False,
    )
    op.create_table(
        "recommendations",
        sa.Column("recommendation_id", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), server_default=sa.text("'5.0'"), nullable=False),
        sa.Column(
            "recommendation_version",
            sa.Text(),
            server_default=sa.text("'legacy-1'"),
            nullable=False,
        ),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("evidence_ids", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("ranking_id", sa.Text(), nullable=True),
        sa.Column("signal_ids", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("feature_ids", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("crm_action_ids", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("input_digest", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("supersedes_recommendation_id", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Double(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("recommendation_id", name=op.f("pk_recommendations")),
    )
    op.create_index("idx_recs_entity", "recommendations", ["entity_id"], unique=False)
    op.create_table(
        "schema_migrations",
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("applied_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("version", name=op.f("pk_schema_migrations")),
    )
    op.create_table(
        "signals",
        sa.Column("signal_id", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("signal_type", sa.Text(), nullable=False),
        sa.Column("value", sa.Double(), nullable=False),
        sa.Column("evidence_ids", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("feature_ids", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("crm_action_ids", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "signal_version", sa.Text(), server_default=sa.text("'legacy-1'"), nullable=False
        ),
        sa.Column("input_digest", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Double(), nullable=False),
        sa.Column("observed_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("signal_id", name=op.f("pk_signals")),
    )
    op.create_index("idx_signals_entity", "signals", ["entity_id"], unique=False)
    op.create_index("idx_signals_type", "signals", ["signal_type"], unique=False)
    _install_append_only_triggers()


def downgrade() -> None:
    _drop_append_only_triggers()
    op.drop_index("idx_signals_type", table_name="signals")
    op.drop_index("idx_signals_entity", table_name="signals")
    op.drop_table("signals")
    op.drop_table("schema_migrations")
    op.drop_index("idx_recs_entity", table_name="recommendations")
    op.drop_table("recommendations")
    op.drop_index("idx_rec_lifecycle", table_name="recommendation_lifecycle_events")
    op.drop_table("recommendation_lifecycle_events")
    op.drop_index("idx_rankings_score", table_name="rankings")
    op.drop_index("idx_rankings_rank", table_name="rankings")
    op.drop_index("idx_rankings_entity", table_name="rankings")
    op.drop_table("rankings")
    op.drop_table("quarantined_entities")
    op.drop_index("idx_persons_name", table_name="persons")
    op.drop_index("idx_persons_github", table_name="persons")
    op.drop_table("persons")
    op.drop_index("idx_obs_entity", table_name="observations")
    op.drop_table("observations")
    op.drop_table("investigations")
    op.drop_index("idx_hypotheses_entity", table_name="hypotheses")
    op.drop_table("hypotheses")
    op.drop_index("idx_ground_truth_program_batch", table_name="ground_truth_datasets")
    op.drop_table("ground_truth_datasets")
    op.drop_index("idx_nodes_type", table_name="graph_nodes")
    op.drop_index("idx_nodes_label", table_name="graph_nodes")
    op.drop_table("graph_nodes")
    op.drop_index("idx_edges_type", table_name="graph_edges")
    op.drop_index("idx_edges_temporal", table_name="graph_edges")
    op.drop_index("idx_edges_target", table_name="graph_edges")
    op.drop_index("idx_edges_source", table_name="graph_edges")
    op.drop_table("graph_edges")
    op.drop_index("idx_features_name_version", table_name="features")
    op.drop_index("idx_features_entity_asof", table_name="features")
    op.drop_table("features")
    op.drop_index("idx_evidence_entity", table_name="evidence")
    op.drop_table("evidence")
    op.drop_index("idx_events_type", table_name="events")
    op.drop_index("idx_events_source", table_name="events")
    op.drop_index("idx_events_observed", table_name="events")
    op.drop_index("idx_events_ingested", table_name="events")
    op.drop_index("idx_events_entity", table_name="events")
    op.drop_table("events")
    op.drop_index("idx_queue_status", table_name="event_queue")
    op.drop_table("event_queue")
    op.drop_index("idx_evaluation_runs_program_batch", table_name="evaluation_runs_v2")
    op.drop_table("evaluation_runs_v2")
    op.drop_table("evaluation_results")
    op.drop_index("idx_entity_validations_status", table_name="entity_validations")
    op.drop_table("entity_validations")
    op.drop_index("idx_links_canonical", table_name="entity_links")
    op.drop_table("entity_links")
    op.drop_table("crm_records")
    op.drop_index("idx_crm_events_observed", table_name="crm_events")
    op.drop_index("idx_crm_events_entity", table_name="crm_events")
    op.drop_table("crm_events")
    op.drop_index("idx_companies_slug", table_name="companies")
    op.drop_index("idx_companies_name", table_name="companies")
    op.drop_table("companies")
    op.drop_table("classifications")
    op.drop_index("idx_rejections_label", table_name="candidate_rejections")
    op.drop_table("candidate_rejections")
    op.drop_index("idx_batch_runs_program", table_name="batch_discovery_runs")
    op.drop_table("batch_discovery_runs")
    op.drop_index("idx_batch_observations_hypothesis", table_name="batch_discovery_observations")
    op.drop_index("idx_batch_observations_asof", table_name="batch_discovery_observations")
    op.drop_table("batch_discovery_observations")
    op.drop_index("idx_batch_hypotheses_latest", table_name="batch_candidate_hypotheses")
    op.drop_table("batch_candidate_hypotheses")
    op.drop_index("idx_alerts_fired", table_name="alerts")
    op.drop_index("idx_alerts_entity", table_name="alerts")
    op.drop_table("alerts")
    op.drop_index("idx_alert_lifecycle", table_name="alert_lifecycle_events")
    op.drop_table("alert_lifecycle_events")
