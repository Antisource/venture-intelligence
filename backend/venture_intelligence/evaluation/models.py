"""Authoritative contracts for isolated historical evaluation."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from venture_intelligence.pipeline.entity_integrity import is_generic_label
from venture_intelligence.replay.models import ReplayMode, ReplayResult
from venture_intelligence.shared.models import EntityType, Source


class EvaluationStatus(str, Enum):
    COMPLETED = "completed"
    EMPTY = "empty"
    PARTIAL = "partial"
    FAILED = "failed"


class EvaluationTargetLevel(str, Enum):
    COMPANY = "company"
    PERSON = "person"
    BOTH = "both"


class MetricName(str, Enum):
    PRECISION_AT_10 = "precision_at_10"
    PRECISION_AT_25 = "precision_at_25"
    RECALL = "recall"
    AVERAGE_LEAD_TIME_DAYS = "average_lead_time_days"
    MEDIAN_LEAD_TIME_DAYS = "median_lead_time_days"
    FALSE_POSITIVES_AT_25 = "false_positives_at_25"
    MISSED_ENTITIES = "missed_entities"
    ALERT_ACCEPTANCE_RATE = "alert_acceptance_rate"
    INVESTMENT_CONVERSION_RATE = "investment_conversion_rate"


class GroundTruthRecord(BaseModel):
    canonical_name: str = Field(min_length=2, max_length=200)
    entity_type: EntityType
    aliases: list[str] = Field(default_factory=list, max_length=20)
    is_positive: bool = True
    outcome_at: datetime | None = None
    alert_accepted: bool | None = None
    invested: bool | None = None
    external_id: str | None = Field(default=None, max_length=200)
    source_url: str = Field(pattern=r"^https?://", max_length=2_048)
    provenance: dict[str, Any]

    @field_validator("canonical_name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if is_generic_label(cleaned):
            raise ValueError("ground-truth identity must not be generic")
        return cleaned

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, values: list[str]) -> list[str]:
        cleaned = [" ".join(value.split()) for value in values if value.strip()]
        if any(is_generic_label(value) for value in cleaned):
            raise ValueError("ground-truth aliases must not be generic")
        if len({_identity_key(value) for value in cleaned}) != len(cleaned):
            raise ValueError("ground-truth aliases must be unique")
        return cleaned

    @field_validator("outcome_at")
    @classmethod
    def validate_outcome_at(cls, value: datetime | None) -> datetime | None:
        return _aware_utc(value) if value is not None else None

    @field_validator("provenance")
    @classmethod
    def validate_provenance(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("ground truth requires provenance")
        return value


class GroundTruthImportRequest(BaseModel):
    program_id: str = Field(min_length=1, max_length=100)
    batch_id: str = Field(min_length=1, max_length=100)
    target_level: EvaluationTargetLevel
    outcome_timestamp: datetime
    source_url: str = Field(pattern=r"^https?://", max_length=2_048)
    records: list[GroundTruthRecord] = Field(default_factory=list, max_length=10_000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("outcome_timestamp")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _aware_utc(value)

    @model_validator(mode="after")
    def validate_records(self) -> "GroundTruthImportRequest":
        allowed = {
            EvaluationTargetLevel.COMPANY: {EntityType.COMPANY},
            EvaluationTargetLevel.PERSON: {EntityType.PERSON},
            EvaluationTargetLevel.BOTH: {EntityType.COMPANY, EntityType.PERSON},
        }[self.target_level]
        identities: set[tuple[EntityType, str]] = set()
        for record in self.records:
            if record.entity_type not in allowed:
                raise ValueError("record entity_type does not match target_level")
            names = [record.canonical_name, *record.aliases]
            for name in names:
                key = (record.entity_type, _identity_key(name))
                if key in identities:
                    raise ValueError(f"duplicate ground-truth identity: {name}")
                identities.add(key)
        return self


class GroundTruthDataset(BaseModel):
    schema_version: str = "3.0"
    dataset_id: str
    digest: str
    program_id: str
    batch_id: str
    target_level: EvaluationTargetLevel
    outcome_timestamp: datetime
    source_url: str
    records: list[GroundTruthRecord]
    metadata: dict[str, Any] = Field(default_factory=dict)
    imported_at: datetime


class SourceAblationSpec(BaseModel):
    ablation_id: str = Field(min_length=1, max_length=100)
    excluded_sources: list[Source] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_sources(self) -> "SourceAblationSpec":
        if len(set(self.excluded_sources)) != len(self.excluded_sources):
            raise ValueError("ablation sources must be unique")
        return self


class EvaluationRequest(BaseModel):
    ground_truth_dataset_id: str
    cutoff: datetime
    mode: ReplayMode = ReplayMode.OBSERVED_AT
    max_events: int = Field(default=5_000, ge=1, le=25_000)
    ablations: list[SourceAblationSpec] = Field(default_factory=list, max_length=8)

    @field_validator("cutoff")
    @classmethod
    def validate_cutoff(cls, value: datetime) -> datetime:
        return _aware_utc(value)

    @model_validator(mode="after")
    def validate_ablations(self) -> "EvaluationRequest":
        identifiers = [item.ablation_id for item in self.ablations]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("ablation identifiers must be unique")
        return self


class EvaluationMetric(BaseModel):
    name: MetricName
    value: float
    numerator: float
    denominator: float
    sample_size: int
    unit: str
    definition: str


class MissedEntityDiagnostic(BaseModel):
    canonical_name: str
    entity_type: EntityType
    category: str
    evidence_count: int = 0
    signal_count: int = 0


class FalsePositiveDiagnostic(BaseModel):
    rank: int
    entity_id: str
    canonical_name: str
    entity_type: EntityType
    composite_score: float
    confidence: float
    signals: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class SourceAblationResult(BaseModel):
    ablation_id: str
    excluded_sources: list[Source]
    replay_id: str
    projection_digest: str
    metrics: list[EvaluationMetric]
    deltas_from_baseline: dict[MetricName, float]


class EvaluationSnapshot(BaseModel):
    ground_truth_digest: str
    replay_input_digest: str
    replay_projection_digest: str
    replay_result: ReplayResult
    matched_positive_names: list[str]
    ranked_entity_ids: list[str]


class EvaluationRun(BaseModel):
    schema_version: str = "3.0"
    engine_version: str = "m3-evaluation-1"
    evaluation_run_id: str
    status: EvaluationStatus
    program_id: str
    batch_id: str
    target_level: EvaluationTargetLevel
    cutoff: datetime
    outcome_timestamp: datetime
    replay_mode: ReplayMode
    ground_truth_dataset_id: str
    ground_truth_digest: str
    metrics: list[EvaluationMetric]
    false_positives: list[FalsePositiveDiagnostic]
    missed_entities: list[MissedEntityDiagnostic]
    ablations: list[SourceAblationResult]
    snapshot: EvaluationSnapshot
    warnings: list[str] = Field(default_factory=list)
    report_digest: str
    provenance: dict[str, Any]
    created_at: datetime
    version_metadata: dict[str, str]
    live_state_mutated: bool = False
    side_effects_suppressed: list[str]


class EvaluationRunSummary(BaseModel):
    evaluation_run_id: str
    status: EvaluationStatus
    program_id: str
    batch_id: str
    target_level: EvaluationTargetLevel
    cutoff: datetime
    outcome_timestamp: datetime
    report_digest: str
    created_at: datetime
    metrics: list[EvaluationMetric]


class EvaluationListResponse(BaseModel):
    evaluations: list[EvaluationRunSummary]


class EvaluationContractResponse(BaseModel):
    schema_version: str = "3.0"
    engine_version: str = "m3-evaluation-1"
    default_replay_mode: ReplayMode = ReplayMode.OBSERVED_AT
    historical_cutoff_field: str = "observed_at"
    remote_execution_enabled: bool = False
    import_cli_command: str
    run_cli_command: str
    supported_metrics: list[MetricName] = Field(default_factory=lambda: list(MetricName))
    target_levels: list[EvaluationTargetLevel] = Field(
        default_factory=lambda: list(EvaluationTargetLevel)
    )
    source_ablation_enabled: bool = True
    side_effects_suppressed: list[str]


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("evaluation timestamps must include a UTC offset")
    return value.astimezone(timezone.utc)


def _identity_key(value: str) -> str:
    return " ".join(value.casefold().split())
