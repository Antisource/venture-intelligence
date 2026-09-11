"""Safe quarantine helpers for polluted entities from older research runs.

Cleanup is deliberately opt-in and defaults to a dry run. Nothing is deleted;
records are copied to an audit table before an operator can decide whether a
separate retention policy should remove them.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from uuid import UUID

from venture_intelligence.pipeline.entity_integrity import validate_materialized_identity
from venture_intelligence.storage.db import Database


def reconcile_directory_company_links(db: Database, *, apply: bool = False) -> dict:
    """Repair legacy collection-URL company collapses from immutable events.

    Detection is deliberately narrow: an explicit directory company event's
    exact normalized name disagrees with the canonical company currently linked
    to that event's source ID.  Shared URLs, similar names, and domains never
    serve as merge keys.
    """
    from venture_intelligence.pipeline.entity_resolution import EntityResolver
    from venture_intelligence.pipeline.graph import GraphBuilder
    from venture_intelligence.pipeline.ranking_worker import RankingWorker
    from venture_intelligence.pipeline.signal_worker import SignalWorker
    from venture_intelligence.shared.models import EntityType, EventType, Source

    def identity(value: object) -> str:
        return " ".join(str(value or "").casefold().replace("-", " ").split())

    def base_source_id(source_id: str) -> str | None:
        base, separator, suffix = source_id.rpartition("__")
        if not separator:
            return None
        try:
            UUID(suffix)
        except ValueError:
            return None
        return base or None

    with db._connect() as connection:
        rows = connection.execute(
            """SELECT * FROM events
               WHERE entity_type=? AND event_type=? AND source=?
               ORDER BY observed_at, event_id""",
            (EntityType.COMPANY.value, EventType.COMPANY_LISTED.value, Source.DIRECTORY.value),
        ).fetchall()
    latest_events = {str(row["entity_id"]): db._row_to_event(row) for row in rows}
    mismatches: list[dict] = []
    duplicates: list[dict] = []
    events_by_source: dict[str, object] = {}
    for source_id, event in latest_events.items():
        canonical_id = db.resolve_entity_id(source_id)
        if canonical_id is None:
            continue
        company = db.get_company(canonical_id)
        expected_name = str(event.raw_payload.get("name") or "")
        base_id = base_source_id(source_id)
        base_canonical_id = db.resolve_entity_id(base_id) if base_id else None
        base_company = db.get_company(base_canonical_id) if base_canonical_id else None
        event_batch = event.metadata.get("batch_id") or event.raw_payload.get("batch")
        if (
            company is not None
            and base_company is not None
            and base_company.company_id != company.company_id
            and identity(base_company.canonical_name) == identity(expected_name)
            and identity(company.canonical_name) == identity(expected_name)
            and base_company.properties.get("program_id") == event.metadata.get("program_id")
            and base_company.batch == event_batch
            and company.batch == event_batch
        ):
            duplicates.append(
                {
                    "source_id": source_id,
                    "base_source_id": base_id,
                    "survivor_id": base_company.company_id,
                    "duplicate_id": company.company_id,
                    "canonical_name": expected_name,
                }
            )
            events_by_source[source_id] = event
            continue
        if company is not None and identity(company.canonical_name) == identity(expected_name):
            continue
        events_by_source[source_id] = event
        mismatches.append(
            {
                "source_id": source_id,
                "linked_canonical_id": canonical_id,
                "linked_name": company.canonical_name if company is not None else None,
                "expected_name": expected_name,
                "program_id": event.metadata.get("program_id"),
                "batch_id": event.metadata.get("batch_id") or event.raw_payload.get("batch"),
            }
        )

    report = {
        "mismatches": mismatches,
        "duplicate_canonical_entities": duplicates,
        "repaired": [],
        "consolidated": [],
        "applied": apply,
    }
    if not apply or not (mismatches or duplicates):
        return report

    resolver = EntityResolver(db)
    graph = GraphBuilder(db)
    old_by_source = {item["source_id"]: item["linked_canonical_id"] for item in mismatches}
    affected_ids = set(old_by_source.values())
    for item in duplicates:
        source_id = str(item["source_id"])
        survivor_id = str(item["survivor_id"])
        duplicate_id = str(item["duplicate_id"])
        event = events_by_source[source_id]
        affected_ids.update((survivor_id, duplicate_id))
        with db._connect() as connection:
            connection.execute(
                "UPDATE entity_links SET canonical_id=? WHERE canonical_id=?",
                (survivor_id, duplicate_id),
            )
            connection.execute(
                "UPDATE observations SET entity_id=? WHERE entity_id=?",
                (survivor_id, duplicate_id),
            )
            connection.execute(
                "UPDATE evidence SET entity_id=? WHERE entity_id=?",
                (survivor_id, duplicate_id),
            )
            connection.execute(
                "DELETE FROM graph_edges WHERE source_id=? OR target_id=?",
                (duplicate_id, duplicate_id),
            )
            connection.execute("DELETE FROM graph_nodes WHERE node_id=?", (duplicate_id,))
            for table in ("features", "signals", "hypotheses", "rankings"):
                connection.execute(f"DELETE FROM {table} WHERE entity_id=?", (duplicate_id,))
            connection.execute("DELETE FROM entity_validations WHERE entity_id=?", (duplicate_id,))
            connection.execute("DELETE FROM companies WHERE company_id=?", (duplicate_id,))
        resolver.reset_cache()
        resolver.resolve(event)
        graph.process(event, survivor_id)
        old_by_source[source_id] = duplicate_id
        report["consolidated"].append(item)
    for item in mismatches:
        source_id = str(item["source_id"])
        event = events_by_source[source_id]
        with db._connect() as connection:
            connection.execute("DELETE FROM entity_links WHERE source_id=?", (source_id,))
        resolver.reset_cache()
        canonical_id = resolver.resolve(event)
        affected_ids.add(canonical_id)
        with db._connect() as connection:
            observation_ids = [
                str(row[0])
                for row in connection.execute(
                    "SELECT observation_id FROM observations WHERE event_id=?", (event.event_id,)
                ).fetchall()
            ]
            connection.execute(
                "UPDATE observations SET entity_id=? WHERE event_id=?",
                (canonical_id, event.event_id),
            )
            if observation_ids:
                for evidence in connection.execute(
                    "SELECT evidence_id, observation_ids FROM evidence"
                ).fetchall():
                    linked_observations = set(json.loads(evidence["observation_ids"] or "[]"))
                    if linked_observations.intersection(observation_ids):
                        connection.execute(
                            "UPDATE evidence SET entity_id=? WHERE evidence_id=?",
                            (canonical_id, evidence["evidence_id"]),
                        )
        graph.process(event, canonical_id)
        report["repaired"].append(
            {**item, "canonical_id": canonical_id, "canonical_name": event.raw_payload["name"]}
        )

    repaired_sources = set(old_by_source)
    with db._connect() as connection:
        founder_rows = connection.execute(
            """SELECT * FROM events
               WHERE entity_type=? AND event_type=? AND source=?
               ORDER BY observed_at, event_id""",
            (EntityType.PERSON.value, EventType.FOUNDER_DETECTED.value, Source.DIRECTORY.value),
        ).fetchall()
    for row in founder_rows:
        event = db._row_to_event(row)
        company_source_id = str(event.raw_payload.get("company_source_id") or "")
        if company_source_id not in repaired_sources:
            continue
        person_id = db.resolve_entity_id(event.entity_id)
        company_id = db.resolve_entity_id(company_source_id)
        old_company_id = old_by_source[company_source_id]
        if person_id is None or company_id is None:
            continue
        with db._connect() as connection:
            connection.execute(
                "DELETE FROM graph_edges WHERE source_id=? AND target_id=? AND edge_type='FOUNDED'",
                (person_id, old_company_id),
            )
        graph.process(event, person_id)

    for company_id in affected_ids:
        company = db.get_company(company_id)
        if company is None:
            continue
        with db._connect() as connection:
            company.founder_ids = [
                str(row[0])
                for row in connection.execute(
                    """SELECT DISTINCT source_id FROM graph_edges
                       WHERE target_id=? AND edge_type='FOUNDED' ORDER BY source_id""",
                    (company_id,),
                ).fetchall()
            ]
        db.write_company(company)

    with db._connect() as connection:
        placeholders = ",".join("?" for _ in affected_ids)
        for table in ("features", "signals", "rankings"):
            connection.execute(
                f"DELETE FROM {table} WHERE entity_id IN ({placeholders})", tuple(affected_ids)
            )
    for company_id in sorted(affected_ids):
        if db.get_company(company_id) is not None and db.is_entity_validated(company_id):
            SignalWorker(db).process_entity(company_id)
    RankingWorker(db).rank_all()
    return report


def quarantine_generic_entities(db: Database, *, apply: bool = False) -> list[dict]:
    """Return and optionally quarantine obvious generic person/company rows."""
    candidates: list[dict] = []
    with db._connect() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS quarantined_entities (
                entity_id TEXT PRIMARY KEY, entity_type TEXT NOT NULL,
                canonical_name TEXT NOT NULL, reason TEXT NOT NULL,
                raw_record TEXT NOT NULL, quarantined_at TEXT NOT NULL
            )"""
        )
        for table, entity_type, id_column in (
            ("persons", "person", "person_id"),
            ("companies", "company", "company_id"),
        ):
            rows = connection.execute(f"SELECT * FROM {table}").fetchall()
            for row in rows:
                name = str(row["canonical_name"] or "")
                entity_id = str(row[id_column])
                validation = validate_materialized_identity(name, entity_id)
                if validation.accepted:
                    continue
                item = {
                    "entity_id": entity_id,
                    "entity_type": entity_type,
                    "canonical_name": row["canonical_name"],
                    "reason": validation.reason or "invalid_identity",
                }
                candidates.append(item)
                if apply:
                    connection.execute(
                        "INSERT OR REPLACE INTO quarantined_entities VALUES (?,?,?,?,?,?)",
                        (
                            entity_id,
                            entity_type,
                            row["canonical_name"],
                            item["reason"],
                            json.dumps(dict(row)),
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    )
        if apply:
            connection.commit()
    return candidates


def cleanup_generic_derived_state(db: Database, *, apply: bool = False) -> dict:
    """Audit or remove polluted *derived* generic entity state.

    Legacy runs sometimes materialized search terms as graph/canonical entities.
    This migration keeps immutable events, observations, and evidence intact;
    only the invalid canonical/graph and downstream derived projections are
    removed.  ``apply=False`` is a read-only audit suitable for operators.
    """
    report = {
        "canonical_entities": [],
        "orphaned_derived_entities": [],
        "graph_nodes": [],
        "graph_edges": 0,
        "derived_rows": {},
        "applied": apply,
    }
    with db._connect() as connection:
        canonical: list[tuple[str, str, str]] = []
        known_canonical_ids: set[str] = set()
        for table, entity_type, id_column in (
            ("persons", "person", "person_id"),
            ("companies", "company", "company_id"),
        ):
            for row in connection.execute(f"SELECT {id_column}, canonical_name FROM {table}"):
                entity_id = str(row[0])
                known_canonical_ids.add(entity_id)
                name = str(row[1] or "")
                validation = validate_materialized_identity(name, entity_id)
                if not validation.accepted:
                    canonical.append((entity_id, table, id_column))
                    report["canonical_entities"].append(
                        {
                            "entity_id": entity_id,
                            "entity_type": entity_type,
                            "canonical_name": name,
                            "reason": validation.reason,
                        }
                    )
        node_ids: list[str] = []
        for row in connection.execute("SELECT node_id, node_type, label FROM graph_nodes"):
            node_id, node_type, label = str(row[0]), str(row[1]), str(row[2] or "")
            validation = validate_materialized_identity(label, node_id)
            if not validation.accepted:
                node_ids.append(node_id)
                report["graph_nodes"].append(
                    {
                        "node_id": node_id,
                        "node_type": node_type,
                        "label": label,
                        "reason": validation.reason,
                    }
                )
        entity_ids = {item[0] for item in canonical} | set(node_ids)
        orphan_sources: dict[str, set[str]] = {}
        for table, column in (
            ("entity_links", "canonical_id"),
            ("features", "entity_id"),
            ("signals", "entity_id"),
            ("hypotheses", "entity_id"),
            ("rankings", "entity_id"),
            ("recommendations", "entity_id"),
            ("alerts", "entity_id"),
            ("crm_records", "entity_id"),
            ("crm_events", "entity_id"),
        ):
            for row in connection.execute(f"SELECT DISTINCT {column} FROM {table}"):
                entity_id = str(row[0] or "")
                if not entity_id or entity_id in known_canonical_ids:
                    continue
                components = [item for item in re.split(r"[_:/#-]+", entity_id) if item]
                label_hint = components[-1] if components else entity_id
                validation = validate_materialized_identity(label_hint, entity_id)
                if validation.accepted:
                    continue
                entity_ids.add(entity_id)
                orphan_sources.setdefault(entity_id, set()).add(table)
        report["orphaned_derived_entities"] = [
            {
                "entity_id": entity_id,
                "reason": validate_materialized_identity(
                    re.split(r"[_:/#-]+", entity_id)[-1], entity_id
                ).reason,
                "tables": sorted(tables),
            }
            for entity_id, tables in sorted(orphan_sources.items())
        ]
        if not entity_ids:
            return report
        placeholders = ",".join("?" for _ in entity_ids)
        edge_query = (
            f"SELECT COUNT(*) FROM graph_edges WHERE source_id IN ({placeholders}) "
            f"OR target_id IN ({placeholders})"
        )
        edge_count = connection.execute(
            edge_query,
            tuple(entity_ids) + tuple(entity_ids),
        ).fetchone()[0]
        report["graph_edges"] = int(edge_count)
        if not apply:
            return report
        # Preserve an auditable copy of canonical rows before deleting them.
        connection.execute(
            """CREATE TABLE IF NOT EXISTS quarantined_entities (
                entity_id TEXT PRIMARY KEY, entity_type TEXT NOT NULL,
                canonical_name TEXT NOT NULL, reason TEXT NOT NULL,
                raw_record TEXT NOT NULL, quarantined_at TEXT NOT NULL
            )"""
        )
        for entity_id, table, id_column in canonical:
            row = connection.execute(
                f"SELECT * FROM {table} WHERE {id_column}=?", (entity_id,)
            ).fetchone()
            if row is not None:
                entity_type = "person" if table == "persons" else "company"
                connection.execute(
                    "INSERT OR REPLACE INTO quarantined_entities VALUES (?,?,?,?,?,?)",
                    (
                        entity_id,
                        entity_type,
                        row["canonical_name"],
                        "generic legacy entity; derived state removed",
                        json.dumps(dict(row)),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
        for entity_id, tables in orphan_sources.items():
            label_hint = re.split(r"[_:/#-]+", entity_id)[-1]
            validation = validate_materialized_identity(label_hint, entity_id)
            connection.execute(
                "INSERT OR REPLACE INTO quarantined_entities VALUES (?,?,?,?,?,?)",
                (
                    entity_id,
                    "orphaned_derived_identity",
                    label_hint,
                    validation.reason or "invalid_identity",
                    json.dumps({"derived_tables": sorted(tables)}),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        recommendation_ids = [
            str(row[0])
            for row in connection.execute(
                "SELECT recommendation_id FROM recommendations "
                f"WHERE entity_id IN ({placeholders})",
                tuple(entity_ids),
            ).fetchall()
        ]
        alert_ids = [
            str(row[0])
            for row in connection.execute(
                f"SELECT alert_id FROM alerts WHERE entity_id IN ({placeholders})",
                tuple(entity_ids),
            ).fetchall()
        ]
        for table, column, ids in (
            ("recommendation_lifecycle_events", "recommendation_id", recommendation_ids),
            ("alert_lifecycle_events", "alert_id", alert_ids),
        ):
            if ids:
                lifecycle_placeholders = ",".join("?" for _ in ids)
                result = connection.execute(
                    f"DELETE FROM {table} WHERE {column} IN ({lifecycle_placeholders})",
                    tuple(ids),
                )
                if result.rowcount:
                    report["derived_rows"][table] = int(result.rowcount)
        for table, column in (
            ("graph_edges", "source_id"),
            ("graph_edges", "target_id"),
            ("graph_nodes", "node_id"),
            ("entity_links", "canonical_id"),
            ("entity_validations", "entity_id"),
            ("features", "entity_id"),
            ("signals", "entity_id"),
            ("hypotheses", "entity_id"),
            ("rankings", "entity_id"),
            ("recommendations", "entity_id"),
            ("alerts", "entity_id"),
            ("crm_records", "entity_id"),
            ("crm_events", "entity_id"),
        ):
            result = connection.execute(
                f"DELETE FROM {table} WHERE {column} IN ({placeholders})", tuple(entity_ids)
            )
            if result.rowcount:
                report["derived_rows"][table] = int(result.rowcount)
        for table, column in (("persons", "person_id"), ("companies", "company_id")):
            result = connection.execute(
                f"DELETE FROM {table} WHERE {column} IN ({placeholders})", tuple(entity_ids)
            )
            if result.rowcount:
                report["derived_rows"][table] = int(result.rowcount)
        connection.commit()
    return report


def reconcile_polluted_entities(db: Database, *, apply: bool = False) -> dict:
    """M6 operator-facing name for the idempotent dry-run-first operation."""
    report = cleanup_generic_derived_state(db, apply=apply)
    report["directory_company_links"] = reconcile_directory_company_links(db, apply=apply)
    report.update(
        schema_version="6.0",
        mode="apply" if apply else "dry_run",
        immutable_inputs_preserved=["events", "event_queue", "observations", "evidence"],
    )
    return report


__all__ = [
    "quarantine_generic_entities",
    "reconcile_directory_company_links",
    "cleanup_generic_derived_state",
    "reconcile_polluted_entities",
]
