"""Deterministic historical evaluation built exclusively on isolated replay."""

from __future__ import annotations

import hashlib
import json
import statistics
import uuid
from datetime import datetime, timezone
from typing import Any

from venture_intelligence.evaluation.models import (
    EvaluationListResponse,
    EvaluationMetric,
    EvaluationRequest,
    EvaluationRun,
    EvaluationRunSummary,
    EvaluationSnapshot,
    EvaluationStatus,
    EvaluationTargetLevel,
    FalsePositiveDiagnostic,
    GroundTruthDataset,
    GroundTruthImportRequest,
    GroundTruthRecord,
    MetricName,
    MissedEntityDiagnostic,
    SourceAblationResult,
)
from venture_intelligence.programs import ProgramRegistry, get_program_registry
from venture_intelligence.replay.models import ReplayRequest, ReplayResult, ReplayStatus
from venture_intelligence.replay.service import SUPPRESSED_REPLAY_SIDE_EFFECTS, ReplayService
from venture_intelligence.shared.models import EntityType
from venture_intelligence.storage.db import Database

_EVALUATION_NAMESPACE = uuid.UUID("f424bd15-37dc-5f7e-a37a-5b24e599e256")


class EvaluationService:
    """Import truth and evaluate immutable history without operational writes."""

    def __init__(
        self,
        database: Database,
        *,
        registry: ProgramRegistry | None = None,
    ) -> None:
        self._database = database
        self._replay = ReplayService(database)
        self._registry = registry or get_program_registry()

    def import_ground_truth(self, request: GroundTruthImportRequest) -> GroundTruthDataset:
        program = self._registry.get(request.program_id)
        if program is None:
            raise ValueError(f"unknown configured program: {request.program_id}")
        if self._registry.batch(program.id, request.batch_id) is None:
            raise ValueError(
                f"batch {request.batch_id!r} is not configured for program {program.id!r}"
            )
        semantic = request.model_dump(mode="json")
        digest = _digest(semantic)
        dataset_id = str(uuid.uuid5(_EVALUATION_NAMESPACE, f"ground-truth:{digest}"))
        existing = self._database.get_ground_truth_dataset(dataset_id)
        if existing is not None:
            return existing
        dataset = GroundTruthDataset(
            dataset_id=dataset_id,
            digest=digest,
            imported_at=datetime.now(timezone.utc),
            **request.model_dump(),
        )
        self._database.write_ground_truth_dataset(dataset)
        return dataset

    def run(self, request: EvaluationRequest) -> EvaluationRun:
        dataset = self._database.get_ground_truth_dataset(request.ground_truth_dataset_id)
        if dataset is None:
            raise ValueError(f"unknown ground-truth dataset: {request.ground_truth_dataset_id}")

        replay = self._replay.run(
            ReplayRequest(
                cutoff=request.cutoff,
                mode=request.mode,
                max_events=request.max_events,
            )
        )
        metrics, false_positives, missed, matched, warnings = _evaluate_snapshot(dataset, replay)
        ablations: list[SourceAblationResult] = []
        baseline_values = {metric.name: metric.value for metric in metrics}
        for spec in request.ablations:
            ablated_replay = self._replay.run(
                ReplayRequest(
                    cutoff=request.cutoff,
                    mode=request.mode,
                    max_events=request.max_events,
                    excluded_sources=spec.excluded_sources,
                )
            )
            ablated_metrics, _, _, _, ablated_warnings = _evaluate_snapshot(dataset, ablated_replay)
            if ablated_replay.status in {ReplayStatus.PARTIAL, ReplayStatus.FAILED}:
                warnings.extend(
                    f"ablation {spec.ablation_id}: {warning}"
                    for warning in ablated_warnings
                    if "replay" in warning
                )
            ablations.append(
                SourceAblationResult(
                    ablation_id=spec.ablation_id,
                    excluded_sources=spec.excluded_sources,
                    replay_id=ablated_replay.replay_id,
                    projection_digest=ablated_replay.projection_digest,
                    metrics=ablated_metrics,
                    deltas_from_baseline={
                        metric.name: _round(metric.value - baseline_values[metric.name])
                        for metric in ablated_metrics
                    },
                )
            )

        snapshot = EvaluationSnapshot(
            ground_truth_digest=dataset.digest,
            replay_input_digest=replay.input_digest,
            replay_projection_digest=replay.projection_digest,
            replay_result=replay,
            matched_positive_names=sorted(matched),
            ranked_entity_ids=[
                item["entity_id"]
                for item in _rankings(replay, dataset.target_level, replay.snapshot.entities)
            ],
        )
        semantic_report = {
            "schema_version": "3.0",
            "engine_version": "m3-evaluation-1",
            "dataset_digest": dataset.digest,
            "cutoff": request.cutoff.isoformat(),
            "mode": request.mode.value,
            "replay_input_digest": replay.input_digest,
            "replay_projection_digest": replay.projection_digest,
            "metrics": [metric.model_dump(mode="json") for metric in metrics],
            "false_positives": [item.model_dump(mode="json") for item in false_positives],
            "missed": [item.model_dump(mode="json") for item in missed],
            "ablations": [item.model_dump(mode="json") for item in ablations],
        }
        report_digest = _digest(semantic_report)
        evaluation_run_id = str(uuid.uuid5(_EVALUATION_NAMESPACE, f"evaluation:{report_digest}"))
        positives = [record for record in dataset.records if record.is_positive]
        if not positives and not replay.snapshot.rankings:
            status = EvaluationStatus.EMPTY
        elif replay.status in {ReplayStatus.PARTIAL, ReplayStatus.FAILED} or warnings:
            status = EvaluationStatus.PARTIAL
        else:
            status = EvaluationStatus.COMPLETED
        result = EvaluationRun(
            evaluation_run_id=evaluation_run_id,
            status=status,
            program_id=dataset.program_id,
            batch_id=dataset.batch_id,
            target_level=dataset.target_level,
            cutoff=request.cutoff,
            outcome_timestamp=dataset.outcome_timestamp,
            replay_mode=request.mode,
            ground_truth_dataset_id=dataset.dataset_id,
            ground_truth_digest=dataset.digest,
            metrics=metrics,
            false_positives=false_positives,
            missed_entities=missed,
            ablations=ablations,
            snapshot=snapshot,
            warnings=sorted(set(warnings)),
            report_digest=report_digest,
            provenance={
                "ground_truth_source_url": dataset.source_url,
                "ground_truth_dataset_id": dataset.dataset_id,
                "replay_id": replay.replay_id,
                "selected_event_ids": replay.selected_event_ids,
                "selected_crm_action_ids": replay.selected_crm_action_ids,
            },
            created_at=datetime.now(timezone.utc),
            version_metadata={
                "event_schema": replay.schema_metadata["event_schema"],
                "replay_schema": replay.schema_metadata["replay_schema"],
                "projection_schema": replay.schema_metadata["projection_schema"],
                "evaluation_schema": "3.0",
                "feature_version": replay.schema_metadata["feature_version"],
                "feedback_feature_version": replay.schema_metadata.get(
                    "feedback_feature_version", "unavailable"
                ),
                "signal_version": replay.schema_metadata["signal_version"],
                "ranking_version": replay.schema_metadata["ranking_version"],
                "recommendation_version": replay.schema_metadata.get(
                    "recommendation_version", "unavailable"
                ),
                "crm_action_version": replay.schema_metadata.get(
                    "crm_action_version", "unavailable"
                ),
                "resolver_version": "m2-projection",
            },
            side_effects_suppressed=SUPPRESSED_REPLAY_SIDE_EFFECTS,
        )
        self._database.write_evaluation_run(result)
        return result

    def list_runs(self) -> EvaluationListResponse:
        runs = self._database.list_evaluation_runs()
        return EvaluationListResponse(
            evaluations=[
                EvaluationRunSummary(
                    evaluation_run_id=run.evaluation_run_id,
                    status=run.status,
                    program_id=run.program_id,
                    batch_id=run.batch_id,
                    target_level=run.target_level,
                    cutoff=run.cutoff,
                    outcome_timestamp=run.outcome_timestamp,
                    report_digest=run.report_digest,
                    created_at=run.created_at,
                    metrics=run.metrics,
                )
                for run in runs
            ]
        )


def _evaluate_snapshot(
    dataset: GroundTruthDataset,
    replay: ReplayResult,
) -> tuple[
    list[EvaluationMetric],
    list[FalsePositiveDiagnostic],
    list[MissedEntityDiagnostic],
    set[str],
    list[str],
]:
    entities = {
        item["entity_id"]: item
        for item in replay.snapshot.entities
        if _entity_matches_program(item, dataset.program_id, dataset.batch_id)
    }
    records_by_identity = _ground_truth_index(dataset.records)
    entity_records: dict[str, GroundTruthRecord] = {}
    for entity_id, entity in entities.items():
        entity_type = EntityType(entity["entity_type"])
        names = [entity.get("canonical_name", ""), *entity.get("aliases", [])]
        for name in names:
            record = records_by_identity.get((entity_type, _identity_key(name)))
            if record is not None:
                entity_records[entity_id] = record
                break

    rankings = _rankings(replay, dataset.target_level, list(entities.values()))
    positive_ranked = {
        item["entity_id"]
        for item in rankings
        if item["entity_id"] in entity_records and entity_records[item["entity_id"]].is_positive
    }
    matched_names = {entity_records[entity_id].canonical_name for entity_id in positive_ranked}
    positives = [record for record in dataset.records if record.is_positive]
    metrics = [
        _precision_metric(rankings, entity_records, 10),
        _precision_metric(rankings, entity_records, 25),
    ]
    metrics.append(
        _metric(
            MetricName.RECALL,
            len(matched_names),
            len(positives),
            len(positives),
            "ratio",
            "Unique positive ground-truth identities ranked / positive identities.",
        )
    )

    lead_times = _lead_times(dataset, replay, entity_records, positive_ranked)
    metrics.extend(
        [
            _value_metric(
                MetricName.AVERAGE_LEAD_TIME_DAYS,
                statistics.fmean(lead_times) if lead_times else 0.0,
                len(lead_times),
                "days",
                "Mean days from earliest observed evidence to the outcome timestamp.",
            ),
            _value_metric(
                MetricName.MEDIAN_LEAD_TIME_DAYS,
                statistics.median(lead_times) if lead_times else 0.0,
                len(lead_times),
                "days",
                "Median days from earliest observed evidence to the outcome timestamp.",
            ),
        ]
    )

    false_positives = _false_positive_diagnostics(rankings[:25], entities, entity_records, replay)
    missed = _missed_diagnostics(positives, entities, entity_records, replay, positive_ranked)
    metrics.extend(
        [
            _value_metric(
                MetricName.FALSE_POSITIVES_AT_25,
                float(len(false_positives)),
                min(25, len(rankings)),
                "count",
                "Top-25 ranked identities not labelled as positive ground truth.",
            ),
            _value_metric(
                MetricName.MISSED_ENTITIES,
                float(len(missed)),
                len(positives),
                "count",
                "Positive ground-truth identities absent from historical rankings.",
            ),
        ]
    )
    metrics.extend(_business_metrics(replay, entity_records))
    warnings: list[str] = []
    if not positives:
        warnings.append("ground_truth_has_no_positive_records")
    if not rankings:
        warnings.append("historical_replay_has_no_rankings")
    if replay.status == ReplayStatus.PARTIAL:
        warnings.append("historical_replay_partial")
    return metrics, false_positives, missed, matched_names, warnings


def _entity_matches_program(entity: dict[str, Any], program_id: str, batch_id: str) -> bool:
    properties = entity.get("properties") or {}
    return (
        str(properties.get("program_id") or "") == program_id
        and str(entity.get("batch") or "").casefold() == batch_id.casefold()
    )


def _ground_truth_index(
    records: list[GroundTruthRecord],
) -> dict[tuple[EntityType, str], GroundTruthRecord]:
    index: dict[tuple[EntityType, str], GroundTruthRecord] = {}
    for record in records:
        for name in [record.canonical_name, *record.aliases]:
            index[(record.entity_type, _identity_key(name))] = record
    return index


def _rankings(
    replay: ReplayResult,
    target_level: EvaluationTargetLevel,
    entity_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    entity_types = {item["entity_id"]: item["entity_type"] for item in entity_rows}
    allowed = {
        EvaluationTargetLevel.COMPANY: {EntityType.COMPANY.value},
        EvaluationTargetLevel.PERSON: {EntityType.PERSON.value},
        EvaluationTargetLevel.BOTH: {EntityType.COMPANY.value, EntityType.PERSON.value},
    }[target_level]
    eligible = [
        item for item in replay.snapshot.rankings if entity_types.get(item["entity_id"]) in allowed
    ]
    return sorted(eligible, key=lambda item: (int(item["rank"]), item["entity_id"]))


def _precision_metric(
    rankings: list[dict[str, Any]],
    entity_records: dict[str, GroundTruthRecord],
    k: int,
) -> EvaluationMetric:
    numerator = sum(
        1
        for item in rankings[:k]
        if item["entity_id"] in entity_records and entity_records[item["entity_id"]].is_positive
    )
    return _metric(
        MetricName.PRECISION_AT_10 if k == 10 else MetricName.PRECISION_AT_25,
        numerator,
        k,
        min(k, len(rankings)),
        "ratio",
        f"Positive ground-truth identities in the first {k} rankings / {k}.",
    )


def _lead_times(
    dataset: GroundTruthDataset,
    replay: ReplayResult,
    entity_records: dict[str, GroundTruthRecord],
    positive_ranked: set[str],
) -> list[float]:
    earliest: dict[str, datetime] = {}
    for evidence in replay.snapshot.evidence:
        entity_id = evidence["entity_id"]
        if entity_id not in positive_ranked:
            continue
        observed_at = datetime.fromisoformat(evidence["observed_at"])
        earliest[entity_id] = min(observed_at, earliest.get(entity_id, observed_at))
    values: list[float] = []
    for entity_id, observed_at in earliest.items():
        record = entity_records[entity_id]
        outcome = record.outcome_at or dataset.outcome_timestamp
        values.append(max(0.0, (outcome - observed_at).total_seconds() / 86_400))
    return values


def _false_positive_diagnostics(
    rankings: list[dict[str, Any]],
    entities: dict[str, dict[str, Any]],
    records: dict[str, GroundTruthRecord],
    replay: ReplayResult,
) -> list[FalsePositiveDiagnostic]:
    signals = _group_values(replay.snapshot.signals, "entity_id", "signal_type")
    evidence = _group_values(replay.snapshot.evidence, "entity_id", "claim")
    results: list[FalsePositiveDiagnostic] = []
    for ranking in rankings:
        entity_id = ranking["entity_id"]
        if entity_id in records and records[entity_id].is_positive:
            continue
        entity = entities.get(entity_id)
        if entity is None:
            continue
        results.append(
            FalsePositiveDiagnostic(
                rank=int(ranking["rank"]),
                entity_id=entity_id,
                canonical_name=entity["canonical_name"],
                entity_type=EntityType(entity["entity_type"]),
                composite_score=float(ranking["composite_score"]),
                confidence=float(ranking["confidence"]),
                signals=signals.get(entity_id, []),
                evidence=evidence.get(entity_id, []),
            )
        )
    return results


def _missed_diagnostics(
    positives: list[GroundTruthRecord],
    entities: dict[str, dict[str, Any]],
    records: dict[str, GroundTruthRecord],
    replay: ReplayResult,
    positive_ranked: set[str],
) -> list[MissedEntityDiagnostic]:
    evidence_counts = _counts(replay.snapshot.evidence)
    signal_counts = _counts(replay.snapshot.signals)
    entity_for_record = {record.canonical_name: entity_id for entity_id, record in records.items()}
    missed: list[MissedEntityDiagnostic] = []
    for record in positives:
        entity_id = entity_for_record.get(record.canonical_name)
        if entity_id in positive_ranked:
            continue
        if entity_id is None or entity_id not in entities:
            category = "not_discovered"
        elif evidence_counts.get(entity_id, 0) == 0:
            category = "evidence_missing"
        elif signal_counts.get(entity_id, 0) == 0:
            category = "signal_extraction_failure"
        else:
            category = "ranking_failure"
        missed.append(
            MissedEntityDiagnostic(
                canonical_name=record.canonical_name,
                entity_type=record.entity_type,
                category=category,
                evidence_count=evidence_counts.get(entity_id or "", 0),
                signal_count=signal_counts.get(entity_id or "", 0),
            )
        )
    return sorted(missed, key=lambda item: (item.entity_type.value, item.canonical_name.casefold()))


def _business_metrics(
    replay: ReplayResult,
    records: dict[str, GroundTruthRecord],
) -> list[EvaluationMetric]:
    contact_ids = {
        item["entity_id"]
        for item in replay.snapshot.recommendations
        if str(item["action"]).casefold() == "contact"
    }
    acceptance = [
        record.alert_accepted
        for entity_id, record in records.items()
        if entity_id in contact_ids and record.alert_accepted is not None
    ]
    investments = [
        record.invested
        for entity_id, record in records.items()
        if entity_id in contact_ids and record.invested is not None
    ]
    return [
        _metric(
            MetricName.ALERT_ACCEPTANCE_RATE,
            sum(bool(item) for item in acceptance),
            len(acceptance),
            len(acceptance),
            "ratio",
            "Accepted historical CONTACT recommendations / explicitly labelled CONTACT outcomes.",
        ),
        _metric(
            MetricName.INVESTMENT_CONVERSION_RATE,
            sum(bool(item) for item in investments),
            len(investments),
            len(investments),
            "ratio",
            "Invested historical CONTACT recommendations / explicitly labelled CONTACT outcomes.",
        ),
    ]


def _metric(
    name: MetricName,
    numerator: float,
    denominator: float,
    sample_size: int,
    unit: str,
    definition: str,
) -> EvaluationMetric:
    return EvaluationMetric(
        name=name,
        value=_round(numerator / denominator) if denominator else 0.0,
        numerator=float(numerator),
        denominator=float(denominator),
        sample_size=sample_size,
        unit=unit,
        definition=definition,
    )


def _value_metric(
    name: MetricName,
    value: float,
    sample_size: int,
    unit: str,
    definition: str,
) -> EvaluationMetric:
    return EvaluationMetric(
        name=name,
        value=_round(value),
        numerator=_round(value),
        denominator=1.0,
        sample_size=sample_size,
        unit=unit,
        definition=definition,
    )


def _group_values(items: list[dict[str, Any]], key: str, value: str) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for item in items:
        grouped.setdefault(str(item[key]), []).append(str(item[value]))
    return {item: sorted(set(values)) for item, values in grouped.items()}


def _counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        entity_id = str(item["entity_id"])
        counts[entity_id] = counts.get(entity_id, 0) + 1
    return counts


def _identity_key(value: str) -> str:
    return " ".join(value.casefold().split())


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _round(value: float) -> float:
    return round(float(value), 6)
