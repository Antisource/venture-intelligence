"""Deterministic replay orchestration over an isolated disposable store."""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from venture_intelligence.pipeline.entity_integrity import validate_candidate
from venture_intelligence.pipeline.entity_resolution import EntityResolver
from venture_intelligence.pipeline.evidence_builder import EvidenceBuilder
from venture_intelligence.pipeline.graph import GraphBuilder
from venture_intelligence.pipeline.ranking_worker import RankingWorker
from venture_intelligence.pipeline.recommendation_worker import RecommendationWorker
from venture_intelligence.pipeline.signal_worker import SignalWorker
from venture_intelligence.pipeline.validator import EventValidator
from venture_intelligence.replay.models import (
    ReplayError,
    ReplayProjectionCounts,
    ReplayRequest,
    ReplayResult,
    ReplaySnapshot,
    ReplayStatus,
)
from venture_intelligence.shared.models import CanonicalEvent, CRMEvent
from venture_intelligence.storage.db import Database

SUPPRESSED_REPLAY_SIDE_EFFECTS = [
    "external_collection",
    "alerts",
    "notifications",
    "email",
    "slack",
    "crm",
    "websocket_broadcast",
]

_REPLAY_NAMESPACE = uuid.UUID("8bc69ebf-9f29-52ca-9a78-51db2f31d775")


@dataclass
class ReplayContext:
    """Owns a replay-only SQLite projection and its bounded request metadata."""

    source: Database
    request: ReplayRequest
    _temporary: tempfile.TemporaryDirectory[str] = field(init=False, repr=False)
    store: Database = field(init=False)
    store_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="venture-replay-")
        self.store_path = Path(self._temporary.name) / "replay.db"
        self.store = Database(str(self.store_path))

    def close(self) -> None:
        self._temporary.cleanup()

    def __enter__(self) -> "ReplayContext":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class ReplayService:
    """Rebuild projections from eligible immutable events without live writes."""

    def __init__(self, source: Database) -> None:
        self._source = source

    def run(self, request: ReplayRequest) -> ReplayResult:
        events, eligible_count, truncated = self._select_events(request)
        crm_actions = self._select_crm_actions(request)
        input_digest = _digest(
            {
                "events": [event.model_dump(mode="json") for event in events],
                "crm_actions": [action.model_dump(mode="json") for action in crm_actions],
            }
        )
        request_key = {
            "cutoff": request.cutoff.isoformat(),
            "start_at": request.start_at.isoformat() if request.start_at else None,
            "mode": request.mode.value,
            "max_events": request.max_events,
            "excluded_sources": sorted(source.value for source in request.excluded_sources),
            "input_digest": input_digest,
        }
        replay_id = str(uuid.uuid5(_REPLAY_NAMESPACE, _canonical_json(request_key)))
        errors: list[ReplayError] = []
        if truncated:
            errors.append(
                ReplayError(
                    stage="selection",
                    code="max_events_reached",
                    message=f"Replay was bounded to {request.max_events} events.",
                )
            )

        with ReplayContext(self._source, request) as context:
            replayed_ids, replayed_action_ids = self._rebuild(context, events, crm_actions, errors)
            snapshot = _snapshot(context.store)
            counts = _projection_counts(
                context.store,
                eligible=eligible_count,
                replayed=len(replayed_ids),
                rejected=len(events) - len(replayed_ids),
                eligible_crm_actions=len(crm_actions),
                replayed_crm_actions=len(replayed_action_ids),
            )
            projection_digest = _digest(snapshot.model_dump(mode="json"))

        if not events and not crm_actions:
            status = ReplayStatus.EMPTY
        elif errors:
            status = ReplayStatus.PARTIAL
        else:
            status = ReplayStatus.COMPLETED

        return ReplayResult(
            replay_id=replay_id,
            status=status,
            cutoff=request.cutoff,
            mode=request.mode,
            start_at=request.start_at,
            excluded_sources=request.excluded_sources,
            # Results may be exposed by read-only report APIs; retain the
            # auditable database filename without disclosing a local path.
            source_database=self._source._path.name,
            side_effects_suppressed=SUPPRESSED_REPLAY_SIDE_EFFECTS,
            selected_event_ids=[event.event_id for event in events],
            selected_crm_action_ids=[action.crm_event_id for action in crm_actions],
            input_digest=input_digest,
            projection_digest=projection_digest,
            counts=counts,
            errors=errors,
            snapshot=snapshot,
            created_at=datetime.now(timezone.utc),
            schema_metadata={
                "event_schema": "1.1",
                "replay_schema": "2.0",
                "projection_schema": "m5-1",
                "feature_version": "m4-feature-1",
                "feedback_feature_version": "m5-feedback-1",
                "signal_version": "m5-signal-1",
                "ranking_version": "m5-ranking-1",
                "recommendation_version": "m5-recommendation-1",
                "crm_action_version": "m5-crm-action-1",
            },
        )

    def _select_events(self, request: ReplayRequest) -> tuple[list[CanonicalEvent], int, bool]:
        boundary = request.mode.value
        clauses = [f"{boundary} <= ?"]
        params: list[object] = [request.cutoff.isoformat()]
        if request.start_at is not None:
            clauses.append(f"{boundary} >= ?")
            params.append(request.start_at.isoformat())
        if request.excluded_sources:
            placeholders = ",".join("?" for _ in request.excluded_sources)
            clauses.append(f"source NOT IN ({placeholders})")
            params.extend(source.value for source in request.excluded_sources)
        where = " AND ".join(clauses)
        with self._source._connect() as conn:
            eligible_count = int(
                conn.execute(
                    f"SELECT COUNT(*) AS total FROM events WHERE {where}", params
                ).fetchone()["total"]
            )
            rows = conn.execute(
                f"""
                SELECT * FROM events WHERE {where}
                ORDER BY {boundary}, observed_at, event_id
                LIMIT ?
                """,
                [*params, request.max_events],
            ).fetchall()
        return (
            [self._source._row_to_event(row) for row in rows],
            eligible_count,
            eligible_count > request.max_events,
        )

    def _select_crm_actions(self, request: ReplayRequest) -> list[CRMEvent]:
        boundary = request.mode.value
        clauses = [f"{boundary} <= ?"]
        params: list[object] = [request.cutoff.isoformat()]
        if request.start_at is not None:
            clauses.append(f"{boundary} >= ?")
            params.append(request.start_at.isoformat())
        where = " AND ".join(clauses)
        with self._source._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM crm_events WHERE {where} "
                f"ORDER BY {boundary}, observed_at, crm_event_id "
                "LIMIT ?",
                [*params, request.max_events],
            ).fetchall()
        return [self._source._row_to_crm_event(row) for row in rows]

    def _rebuild(
        self,
        context: ReplayContext,
        events: list[CanonicalEvent],
        crm_actions: list[CRMEvent],
        errors: list[ReplayError],
    ) -> tuple[list[str], list[str]]:
        validator = EventValidator(reference_time=context.request.cutoff, max_age_days=None)
        resolver = EntityResolver(context.store, entity_id_factory=_entity_id)
        evidence_builder = EvidenceBuilder(context.store)
        graph_builder = GraphBuilder(context.store)
        accepted: list[tuple[CanonicalEvent, str]] = []
        live_to_replay: dict[str, str] = {}

        for event in events:
            context.store.write_event(event)
            valid, reason = validator.validate(event)
            candidate = validate_candidate(event, context.store)
            if not valid or not candidate.accepted:
                rejection_reason = reason or candidate.reason or "rejected"
                context.store.write_candidate_rejection(
                    candidate_label=candidate.label,
                    entity_type=event.entity_type.value,
                    source=event.source.value,
                    source_url=event.source_url,
                    query=str(event.metadata.get("query") or "") or None,
                    reason=rejection_reason,
                    confidence=event.confidence,
                    provenance={"event_id": event.event_id, "replay": True},
                    raw_candidate=event.raw_payload,
                    rejection_id=str(uuid.uuid5(_REPLAY_NAMESPACE, f"reject:{event.event_id}")),
                )
                errors.append(
                    ReplayError(
                        event_id=event.event_id,
                        stage="candidate_validation" if valid else "event_validation",
                        code="candidate_rejected" if valid else "invalid_event",
                        message=rejection_reason,
                    )
                )
                continue
            try:
                canonical_id = resolver.resolve(event)
                accepted.append((event, canonical_id))
                live_id = self._source.resolve_entity_id(event.entity_id)
                if live_id:
                    live_to_replay[live_id] = canonical_id
                evidence_builder.process(event, canonical_id)
                graph_builder.process(event, canonical_id)
            except Exception as exc:
                errors.append(
                    ReplayError(
                        event_id=event.event_id,
                        stage="projection",
                        code="projection_error",
                        message=str(exc),
                    )
                )

        # A second graph pass resolves relationships whose target appeared later.
        for event, canonical_id in accepted:
            graph_builder.process(event, canonical_id)

        replayed_action_ids: list[str] = []
        for action in crm_actions:
            replay_entity_id = live_to_replay.get(action.entity_id)
            if replay_entity_id is None:
                errors.append(
                    ReplayError(
                        event_id=action.crm_event_id,
                        stage="crm_feedback",
                        code="crm_entity_unavailable",
                        message="CRM action had no entity eligible in this replay window.",
                    )
                )
                continue
            context.store.write_crm_event(action.model_copy(update={"entity_id": replay_entity_id}))
            replayed_action_ids.append(action.crm_event_id)

        entity_ids = sorted({canonical_id for _, canonical_id in accepted})
        signal_worker = SignalWorker(context.store)
        input_as_of = context.request.cutoff
        if context.request.mode.value == "occurred_at" and (events or crm_actions):
            # The isolated replay store already contains only event-time-eligible
            # inputs. Permit those selected inputs to feed projections without
            # relabelling their observation time or leaking any unselected event.
            input_as_of = max(
                [event.observed_at for event in events]
                + [action.observed_at for action in crm_actions]
            )
        for entity_id in entity_ids:
            signal_worker.process_entity(
                entity_id,
                as_of=context.request.cutoff,
                input_as_of=input_as_of,
            )
        rankings = RankingWorker(context.store).rank_all(
            ranked_at=context.request.cutoff,
            input_as_of=input_as_of,
        )
        recommender = RecommendationWorker(context.store)
        for ranking in rankings:
            recommendation_id = str(
                uuid.uuid5(_REPLAY_NAMESPACE, f"recommendation:{ranking.entity_id}")
            )
            recommendation = recommender.decide(
                ranking,
                created_at=context.request.cutoff,
                recommendation_id=recommendation_id,
            )
            context.store.write_recommendation(recommendation)
        return [event.event_id for event, _ in accepted], replayed_action_ids


def _entity_id(kind: str, event: CanonicalEvent) -> str:
    label = str(event.raw_payload.get("name") or event.entity_id).strip().casefold()
    return str(uuid.uuid5(_REPLAY_NAMESPACE, f"{kind}:{label}:{event.entity_id}"))


def _snapshot(db: Database) -> ReplaySnapshot:
    with db._connect() as conn:
        companies = conn.execute(
            "SELECT company_id, canonical_name, aliases, slug, yc_url, website, "
            "description, batch, tags, identity_confidence, properties FROM companies"
        ).fetchall()
        persons = conn.execute(
            "SELECT person_id, canonical_name, aliases, linkedin_url, github_username, "
            "current_title, batch, identity_confidence, properties FROM persons"
        ).fetchall()
        observation_rows = conn.execute(
            "SELECT observation_id, event_id, fact FROM observations"
        ).fetchall()
        evidence_rows = conn.execute("SELECT * FROM evidence").fetchall()
        crm_action_rows = conn.execute("SELECT * FROM crm_events").fetchall()
        feature_rows = conn.execute("SELECT * FROM features").fetchall()
        signal_rows = conn.execute("SELECT * FROM signals").fetchall()
        hypothesis_rows = conn.execute("SELECT * FROM hypotheses").fetchall()
        ranking_rows = conn.execute("SELECT * FROM rankings").fetchall()
        recommendation_rows = conn.execute("SELECT * FROM recommendations").fetchall()
        node_rows = conn.execute(
            "SELECT node_id, node_type, label, properties FROM graph_nodes"
        ).fetchall()
        edge_rows = conn.execute(
            "SELECT source_id, target_id, edge_type, confidence, observed_at, "
            "valid_from, valid_to, validity_precision, temporal_status, "
            "evidence_ids, properties FROM graph_edges"
        ).fetchall()

    observations = {
        row["observation_id"]: {"event_id": row["event_id"], "fact": row["fact"]}
        for row in observation_rows
    }
    evidence_semantics: dict[str, dict[str, Any]] = {}
    for row in evidence_rows:
        obs = [observations[item] for item in _json(row["observation_ids"], [])]
        evidence_semantics[row["evidence_id"]] = {
            "entity_id": row["entity_id"],
            "claim": row["claim"],
            "observations": sorted(obs, key=_canonical_json),
            "confidence": row["confidence"],
            "observed_at": row["observed_at"],
        }
    crm_action_semantics = {
        row["crm_event_id"]: {
            "entity_id": row["entity_id"],
            "action": row["action"],
            "feedback_kind": row["feedback_kind"],
            "from_stage": row["from_stage"],
            "to_stage": row["to_stage"],
            "note": row["note"],
            "partner": row["partner"],
            "ranking_id": row["ranking_id"],
            "provenance": _json(row["provenance"], {}),
            "occurred_at": row["occurred_at"],
            "observed_at": row["observed_at"],
            "input_digest": row["input_digest"],
        }
        for row in crm_action_rows
    }
    feature_semantics: dict[str, dict[str, Any]] = {}
    for row in feature_rows:
        feature_semantics[row["feature_id"]] = {
            "entity_id": row["entity_id"],
            "name": row["name"],
            "value": row["value"],
            "unit": row["unit"],
            "feature_version": row["feature_version"],
            "formula": row["formula"],
            "parameters": _json(row["parameters"], {}),
            "evidence": sorted(
                [
                    evidence_semantics[item]["claim"]
                    for item in _json(row["evidence_ids"], [])
                    if item in evidence_semantics
                ]
            ),
            "crm_actions": sorted(
                [
                    crm_action_semantics[item]["action"]
                    for item in _json(row["crm_action_ids"], [])
                    if item in crm_action_semantics
                ]
            ),
            "as_of": row["as_of"],
            "confidence": row["confidence"],
            "input_digest": row["input_digest"],
        }
    signal_semantics: dict[str, dict[str, Any]] = {}
    for row in signal_rows:
        signal_semantics[row["signal_id"]] = {
            "entity_id": row["entity_id"],
            "signal_type": row["signal_type"],
            "signal_version": row["signal_version"],
            "value": row["value"],
            "evidence": sorted(
                [
                    evidence_semantics[item]["claim"]
                    for item in _json(row["evidence_ids"], [])
                    if item in evidence_semantics
                ],
            ),
            "features": sorted(
                [
                    feature_semantics[item]["name"]
                    for item in _json(row["feature_ids"], [])
                    if item in feature_semantics
                ]
            ),
            "crm_actions": sorted(
                [
                    crm_action_semantics[item]["action"]
                    for item in _json(row["crm_action_ids"], [])
                    if item in crm_action_semantics
                ]
            ),
            "input_digest": row["input_digest"],
            "explanation": row["explanation"],
            "confidence": row["confidence"],
            "observed_at": row["observed_at"],
        }
    hypothesis_semantics = {
        row["hypothesis_id"]: {
            "entity_id": row["entity_id"],
            "statement": row["statement"],
            "signals": sorted(
                [signal_semantics[item]["signal_type"] for item in _json(row["signal_ids"], [])]
            ),
            "probability": row["probability"],
            "created_at": row["created_at"],
        }
        for row in hypothesis_rows
    }
    entities = [
        {
            "entity_type": "company",
            "entity_id": row["company_id"],
            **_row(row, {"company_id"}, {"aliases", "tags", "properties"}),
        }
        for row in companies
    ] + [
        {
            "entity_type": "person",
            "entity_id": row["person_id"],
            **_row(row, {"person_id"}, {"aliases", "properties"}),
        }
        for row in persons
    ]
    rankings = [
        {
            "entity_id": row["entity_id"],
            "rank": row["rank"],
            "composite_score": row["composite_score"],
            "ranking_version": row["ranking_version"],
            "dimensions": _json(row["dimensions"], []),
            "hypotheses": sorted(
                [
                    hypothesis_semantics[item]["statement"]
                    for item in _json(row["hypothesis_ids"], [])
                ]
            ),
            "evidence": sorted(
                [
                    evidence_semantics[item]["claim"]
                    for item in _json(row["evidence_ids"], [])
                    if item in evidence_semantics
                ]
            ),
            "features": sorted(
                [
                    feature_semantics[item]["name"]
                    for item in _json(row["feature_ids"], [])
                    if item in feature_semantics
                ]
            ),
            "signals": sorted(
                [
                    signal_semantics[item]["signal_type"]
                    for item in _json(row["signal_ids"], [])
                    if item in signal_semantics
                ]
            ),
            "crm_actions": sorted(
                [
                    crm_action_semantics[item]["action"]
                    for item in _json(row["crm_action_ids"], [])
                    if item in crm_action_semantics
                ]
            ),
            "input_digest": row["input_digest"],
            "reasoning": row["reasoning"],
            "confidence": row["confidence"],
            "ranked_at": row["ranked_at"],
        }
        for row in ranking_rows
    ]
    recommendations = [
        {
            "entity_id": row["entity_id"],
            "action": row["action"],
            "recommendation_version": row["recommendation_version"],
            "reasoning": row["reasoning"],
            "evidence": sorted(
                [evidence_semantics[item]["claim"] for item in _json(row["evidence_ids"], [])]
            ),
            "confidence": row["confidence"],
            "crm_actions": sorted(
                [
                    crm_action_semantics[item]["action"]
                    for item in _json(row["crm_action_ids"], [])
                    if item in crm_action_semantics
                ]
            ),
            "created_at": row["created_at"],
        }
        for row in recommendation_rows
    ]
    graph_nodes = [_row(row, json_fields={"properties"}) for row in node_rows]
    graph_edges = [_row(row, json_fields={"evidence_ids", "properties"}) for row in edge_rows]
    return ReplaySnapshot(
        entities=_sorted(entities),
        evidence=_sorted(list(evidence_semantics.values())),
        crm_actions=_sorted(list(crm_action_semantics.values())),
        features=_sorted(list(feature_semantics.values())),
        signals=_sorted(list(signal_semantics.values())),
        hypotheses=_sorted(list(hypothesis_semantics.values())),
        rankings=_sorted(rankings),
        recommendations=_sorted(recommendations),
        graph_nodes=_sorted(graph_nodes),
        graph_edges=_sorted(graph_edges),
    )


def _projection_counts(
    db: Database,
    *,
    eligible: int,
    replayed: int,
    rejected: int,
    eligible_crm_actions: int,
    replayed_crm_actions: int,
) -> ReplayProjectionCounts:
    tables = {
        "companies": "companies",
        "persons": "persons",
        "evidence": "evidence",
        "features": "features",
        "signals": "signals",
        "hypotheses": "hypotheses",
        "rankings": "rankings",
        "recommendations": "recommendations",
        "graph_nodes": "graph_nodes",
        "graph_edges": "graph_edges",
        "alerts": "alerts",
        "crm_records": "crm_records",
    }
    with db._connect() as conn:
        values = {
            field: int(conn.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()["total"])
            for field, table in tables.items()
        }
    return ReplayProjectionCounts(
        eligible_events=eligible,
        replayed_events=replayed,
        rejected_events=rejected,
        eligible_crm_actions=eligible_crm_actions,
        replayed_crm_actions=replayed_crm_actions,
        **values,
    )


def _row(
    row: Any,
    exclude: set[str] | None = None,
    json_fields: set[str] | None = None,
) -> dict[str, Any]:
    excluded = exclude or set()
    encoded = json_fields or set()
    return {
        key: _json(value, value) if key in encoded else value
        for key, value in dict(row).items()
        if key not in excluded
    }


def _json(value: str | None, fallback: Any) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _sorted(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=_canonical_json)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
