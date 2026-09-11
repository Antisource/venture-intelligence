"""Deterministic evidence-to-feature computation for Milestone 4."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from venture_intelligence.shared.models import (
    CRMActionType,
    CRMEvent,
    CRMFeedbackKind,
    EventType,
    Feature,
    Source,
)
from venture_intelligence.storage.db import Database

FEATURE_SCHEMA_VERSION = "4.0"
FEATURE_VERSION = "m4-feature-1"
FEEDBACK_FEATURE_VERSION = "m5-feedback-1"
_FEATURE_NAMESPACE = uuid.UUID("8d5a4ec5-da3f-54e9-b44d-10dac9bd4664")


class FeatureWorker:
    """Compute a small, explicit feature set from bounded historical inputs."""

    def __init__(self, database: Database, *, feature_version: str = FEATURE_VERSION) -> None:
        self._database = database
        self._feature_version = feature_version

    def process_entity(
        self,
        entity_id: str,
        *,
        as_of: datetime | None = None,
        input_as_of: datetime | None = None,
    ) -> list[Feature]:
        cutoff = _aware_utc(as_of or datetime.now(timezone.utc))
        input_cutoff = _aware_utc(input_as_of) if input_as_of is not None else cutoff
        inputs = self._database.get_feature_inputs_for_entity(entity_id, as_of=input_cutoff)
        actions = self._database.get_crm_events(entity_id, as_of=input_cutoff)
        if not inputs:
            return []

        features: list[Feature] = []
        features.append(
            self._build(
                entity_id,
                "evidence_count",
                float(len(inputs)),
                "count",
                inputs,
                cutoff,
                "count(active_unique_evidence)",
            )
        )
        independent_sources = {
            (item.get("source_domain") or item.get("source") or "unknown").casefold()
            for item in inputs
        }
        features.append(
            self._build(
                entity_id,
                "independent_source_count",
                float(len(independent_sources)),
                "count",
                inputs,
                cutoff,
                "count(distinct_source_domain_or_source)",
            )
        )

        generic_program_claims = {
            "Has confirmed program membership",
            "Has supported program association",
            "Has supported founder program association",
            "Has confirmed founder program membership",
        }
        # Persisted pre-M6 evidence remains a valid compatibility input. New
        # evidence is never emitted with these accelerator-specific strings.
        legacy_program_claims = {
            "Is a confirmed YC F26 company",
            "Is a confirmed YC F26 founder",
            "Is likely a YC F26 company",
            "Is a YC F26 founder",
        }
        program_claims = generic_program_claims | legacy_program_claims
        program_inputs = [
            item
            for item in inputs
            if (
                item.get("event_type")
                in {
                    EventType.COMPANY_LISTED.value,
                    EventType.PROGRAM_MEMBERSHIP_ADDED.value,
                    EventType.YC_BATCH_ADDED.value,
                }
                or item.get("claim") in program_claims
            )
            and (
                item.get("claim") in program_claims
                or item.get("program_id")
                or item.get("source") in {Source.YC_WEBSITE.value, Source.DIRECTORY.value}
            )
        ]
        if program_inputs:
            official = {
                Source.YC_WEBSITE.value,
                Source.DIRECTORY.value,
            }
            value = max(
                1.0 if item.get("source") in official else float(item["confidence"])
                for item in program_inputs
            )
            features.append(
                self._build(
                    entity_id,
                    "program_membership_confidence",
                    value,
                    "ratio",
                    program_inputs,
                    cutoff,
                    "max(official_directory=1.0, corroborated_evidence_confidence)",
                )
            )

        definitions = [
            (
                "repository_count",
                "count",
                {EventType.REPO_CREATED.value},
                "count(distinct_repository_observations)",
                "count",
                {"Has active GitHub presence"},
            ),
            (
                "github_stars",
                "count",
                {EventType.REPO_CREATED.value},
                "sum(repository_star_observation_values)",
                "sum",
                {"Has active GitHub presence"},
            ),
            (
                "github_commits_7d",
                "count",
                {EventType.COMMIT_VELOCITY_HIGH.value},
                "sum(commit_observation_values_7d)",
                "sum",
                {"Is building actively"},
            ),
            (
                "open_roles_30d",
                "count",
                {EventType.JOB_POSTED.value, EventType.HIRING_VELOCITY_HIGH.value},
                "max(explicit_open_roles, distinct_job_postings)",
                "roles",
                {"Is actively hiring", "Has high hiring velocity"},
            ),
            (
                "research_publication_count",
                "count",
                {EventType.PAPER_PUBLISHED.value},
                "count(distinct_publication_observations)",
                "count",
                {"Has published research"},
            ),
            (
                "public_launch_count",
                "count",
                {EventType.PRODUCT_LAUNCHED.value, EventType.SHOW_HN_POSTED.value},
                "count(distinct_public_launch_observations)",
                "count",
                {"Has public product launch"},
            ),
            (
                "community_evidence_count",
                "count",
                {
                    EventType.HN_MENTIONED.value,
                    EventType.SOCIAL_MENTION.value,
                    EventType.NEWSLETTER_MENTION.value,
                    EventType.BLOG_MENTION.value,
                    EventType.COMMUNITY_MEMBERSHIP.value,
                },
                "count(distinct_public_community_observations)",
                "count",
                {"Has public community signal", "Has public environmental signal"},
            ),
        ]
        for name, unit, event_types, formula, reducer, claims in definitions:
            selected = [
                item
                for item in inputs
                if item.get("event_type") in event_types or item.get("claim") in claims
            ]
            if not selected:
                continue
            value = _reduce(selected, reducer)
            features.append(self._build(entity_id, name, value, unit, selected, cutoff, formula))

        website_inputs = [
            item
            for item in inputs
            if item.get("event_type") == EventType.COMPANY_WEBSITE_FOUND.value
            or item.get("claim") == "Has a public company website"
        ]
        if website_inputs:
            features.append(
                self._build(
                    entity_id,
                    "company_website_present",
                    1.0,
                    "boolean",
                    website_inputs,
                    cutoff,
                    "any(company_website_observation)",
                )
            )

        features.extend(self._feedback_features(entity_id, actions, cutoff))

        for feature in features:
            self._database.write_feature(feature)
        return sorted(features, key=lambda item: item.name)

    def _feedback_features(
        self,
        entity_id: str,
        actions: list[CRMEvent],
        as_of: datetime,
    ) -> list[Feature]:
        positive_types = {
            CRMActionType.MEETING_SCHEDULED,
            CRMActionType.INTERESTED,
            CRMActionType.INTRODUCED,
            CRMActionType.WATCHLISTED,
            CRMActionType.FOLLOW_UP_REQUESTED,
            CRMActionType.RELATIONSHIP_STARTED,
        }
        definitions = [
            (
                "human_feedback_positive_count",
                [item for item in actions if item.action in positive_types],
                CRMFeedbackKind.HUMAN_JUDGMENT,
            ),
            (
                "human_feedback_negative_count",
                [item for item in actions if item.action is CRMActionType.PASSED],
                CRMFeedbackKind.HUMAN_JUDGMENT,
            ),
            (
                "realized_outcome_count",
                [item for item in actions if item.action is CRMActionType.INVESTED],
                CRMFeedbackKind.REALIZED_EXTERNAL_OUTCOME,
            ),
        ]
        return [
            self._build_feedback(entity_id, name, selected, kind, as_of)
            for name, selected, kind in definitions
            if selected
        ]

    def _build_feedback(
        self,
        entity_id: str,
        name: str,
        actions: list[CRMEvent],
        feedback_kind: CRMFeedbackKind,
        as_of: datetime,
    ) -> Feature:
        crm_action_ids = sorted(item.crm_event_id for item in actions)
        semantics = {
            "entity_id": entity_id,
            "name": name,
            "value": len(actions),
            "crm_actions": sorted(
                (
                    {
                        "action": item.action.value,
                        "feedback_kind": item.feedback_kind.value,
                        "occurred_at": item.occurred_at.isoformat(),
                        "observed_at": item.observed_at.isoformat(),
                        "partner": item.partner,
                        "input_digest": item.input_digest,
                    }
                    for item in actions
                ),
                key=_canonical_json,
            ),
            "as_of": as_of.isoformat(),
            "feature_version": FEEDBACK_FEATURE_VERSION,
        }
        input_digest = _digest(semantics)
        return Feature(
            feature_id=str(uuid.uuid5(_FEATURE_NAMESPACE, input_digest)),
            schema_version="5.0",
            feature_version=FEEDBACK_FEATURE_VERSION,
            entity_id=entity_id,
            name=name,
            value=float(len(actions)),
            unit="count",
            source=Source.CRM,
            observed_at=as_of,
            as_of=as_of,
            computed_at=as_of,
            crm_action_ids=crm_action_ids,
            formula=f"count(distinct_{feedback_kind.value}_crm_actions)",
            parameters={
                "feedback_kind": feedback_kind.value,
                "authoritative_ground_truth": False,
                "input_count": len(actions),
            },
            input_digest=input_digest,
            confidence=0.9 if feedback_kind is CRMFeedbackKind.REALIZED_EXTERNAL_OUTCOME else 0.75,
        )

    def _build(
        self,
        entity_id: str,
        name: str,
        value: float,
        unit: str,
        inputs: list[dict[str, Any]],
        as_of: datetime,
        formula: str,
    ) -> Feature:
        evidence_ids = sorted({str(item["evidence_id"]) for item in inputs})
        observation_ids = sorted(
            {
                str(observation["observation_id"])
                for item in inputs
                for observation in item.get("observations", [])
            }
        )
        source_values = sorted(
            {str(item.get("source") or Source.TRIANGULATION.value) for item in inputs}
        )
        source = Source(source_values[0]) if len(source_values) == 1 else Source.TRIANGULATION
        program_ids = sorted({str(item["program_id"]) for item in inputs if item.get("program_id")})
        batch_ids = sorted({str(item["batch_id"]) for item in inputs if item.get("batch_id")})
        confidence = sum(float(item["confidence"]) for item in inputs) / len(inputs)
        semantics = {
            "entity_id": entity_id,
            "name": name,
            "value": round(float(value), 6),
            "unit": unit,
            "formula": formula,
            "inputs": sorted(
                (
                    {
                        "claim": item.get("claim"),
                        "confidence": item.get("confidence"),
                        "observed_at": item.get("observed_at"),
                        "source_urls": sorted(item.get("source_urls", [])),
                        "observations": sorted(
                            (
                                {
                                    "fact": observation.get("fact"),
                                    "value": observation.get("value"),
                                    "source": observation.get("source"),
                                    "observed_at": observation.get("observed_at"),
                                }
                                for observation in item.get("observations", [])
                            ),
                            key=_canonical_json,
                        ),
                    }
                    for item in inputs
                ),
                key=_canonical_json,
            ),
            "as_of": as_of.isoformat(),
            "feature_version": self._feature_version,
        }
        input_digest = _digest(semantics)
        return Feature(
            feature_id=str(uuid.uuid5(_FEATURE_NAMESPACE, input_digest)),
            schema_version=FEATURE_SCHEMA_VERSION,
            feature_version=self._feature_version,
            entity_id=entity_id,
            name=name,
            value=round(float(value), 6),
            unit=unit,
            source=source,
            observed_at=as_of,
            as_of=as_of,
            computed_at=as_of,
            evidence_ids=evidence_ids,
            observation_ids=observation_ids,
            formula=formula,
            parameters={"input_count": len(inputs)},
            input_digest=input_digest,
            program_id=program_ids[0] if len(program_ids) == 1 else None,
            batch_id=batch_ids[0] if len(batch_ids) == 1 else None,
            confidence=round(confidence, 6),
        )


def _reduce(items: list[dict[str, Any]], reducer: str) -> float:
    observations = [observation for item in items for observation in item.get("observations", [])]
    if reducer == "sum":
        return float(sum(_number(item.get("value")) for item in observations))
    if reducer == "roles":
        explicit = [_number(item.get("value")) for item in observations]
        return float(max([len(observations), *explicit]))
    return float(len({str(item.get("event_id")) for item in observations}))


def _number(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return 0.0


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("feature as_of must include a UTC offset")
    return value.astimezone(timezone.utc)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()
