"""Candidate and canonical-entity integrity gates.

This module is deliberately used *before* EventBus persistence and again at
promotion/ranking boundaries.  A search term is evidence, not an entity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from venture_intelligence.shared.models import CanonicalEvent, EntityType, EventType, Source

if TYPE_CHECKING:
    from venture_intelligence.storage.db import Database

GENERIC_ENTITY_LABELS = frozenset(
    {
        "twitter",
        "x",
        "linkedin",
        "instagram",
        "github",
        "arxiv",
        "keyframes",
        "substack",
        "medium",
        "hacker news",
        "hackernews",
        "product hunt",
        "producthunt",
        "wellfound",
        "discord",
        "facebook",
        "media",
        "founder",
        "company",
        "startup",
        "unknown",
        "search",
        "result",
        "results",
        "technology",
        "platform",
        "repository",
        "paper",
    }
)


@dataclass(frozen=True)
class CandidateValidation:
    accepted: bool
    reason: str | None = None
    label: str = ""


def candidate_label(event: CanonicalEvent) -> str:
    """Return the explicit candidate label; never derive one from an ID/URL."""
    value = event.raw_payload.get("name")
    return value.strip() if isinstance(value, str) else ""


def is_generic_label(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", value.casefold()).strip(" ._-/#")
    if not normalized or normalized in GENERIC_ENTITY_LABELS:
        return True
    # "Twitter Media" and similar source/platform-labelled strings are still
    # query artefacts.  Match complete words, never a substring of a name.
    words = set(re.findall(r"[a-z0-9]+", normalized))
    return any(" " not in generic and generic in words for generic in GENERIC_ENTITY_LABELS)


def related_company(event: CanonicalEvent, database: Database) -> str | None:
    """Resolve repository enrichment only through a validated company domain."""
    if event.source != Source.GITHUB or event.event_type != EventType.REPO_CREATED:
        return None
    source_id = event.metadata.get("related_entity_source_id")
    entity_id = database.resolve_entity_id(source_id) if source_id else None
    company = database.get_company(entity_id) if entity_id else None
    if not company or not database.is_entity_validated(entity_id):
        return None
    try:
        website = urlsplit(company.website or "").hostname
        homepage = urlsplit(str(event.raw_payload.get("homepage") or "")).hostname
    except ValueError:
        return None
    return entity_id if website and website == homepage else None


def validate_candidate(
    event: CanonicalEvent, database: Database | None = None
) -> CandidateValidation:
    """Validate person/company promotion before it can become an event.

    Repository and paper events are observations, but they are never a person or
    company promotion and are excluded later by the ranking gate.
    """
    if event.entity_type not in {EntityType.COMPANY, EntityType.PERSON}:
        return CandidateValidation(True)
    label = candidate_label(event)
    if is_generic_label(label):
        return CandidateValidation(False, "generic_label", label)
    if len(label) < 2 or len(label) > 160:
        return CandidateValidation(False, "invalid_label", label)
    if not re.search(r"[A-Za-z]", label):
        return CandidateValidation(False, "non_identity_label", label)

    if event.source == Source.PRODUCT_HUNT and event.event_type == EventType.PRODUCT_LAUNCHED:
        existing = database.resolve_entity_id(event.entity_id) if database else None
        company = database.get_company(existing) if existing else None
        post = event.raw_payload.get("product_hunt_post", {})
        try:
            website = urlsplit(company.website or "").hostname if company else None
            product_site = urlsplit(
                str(post.get("resolved_website") or post.get("website") or "")
            ).hostname
        except (ValueError, AttributeError):
            website = product_site = None
        if (
            company
            and database.is_entity_validated(existing)
            and company.canonical_name.casefold() == label.casefold()
            and website
            and website == product_site
        ):
            return CandidateValidation(True, label=label)
        return CandidateValidation(False, "enrichment_requires_validated_identity", label)

    tier = str(event.metadata.get("evidence_tier", ""))
    identity_urls = event.metadata.get("identity_evidence_urls", [])
    research_attributed = (
        event.source == Source.RESEARCH_AGENT
        and event.metadata.get("attribution_status") == "attached"
        and isinstance(identity_urls, list)
        and event.source_url in identity_urls
    )
    batch_hypothesis_verified = _validated_batch_hypothesis(event, identity_urls)
    if event.entity_type == EntityType.COMPANY:
        accepted_sources = {
            Source.DIRECTORY,
            Source.TRIANGULATION,
            Source.YC_WEBSITE,
            Source.WEBSITE,
        }
        research_verified = (
            (research_attributed or batch_hypothesis_verified)
            and bool(event.metadata.get("identity_validated"))
            and bool(event.metadata.get("identity_evidence"))
            and int(event.metadata.get("supporting_source_count", 0)) >= 2
        )
        if event.source not in accepted_sources and not research_verified:
            # A public company profile is explicit identity evidence.  It is
            # not a discovery-search result and still must have a non-generic
            # label (checked above).
            if not (event.source == Source.LINKEDIN and "/company/" in event.source_url):
                return CandidateValidation(False, "insufficient_company_identity_evidence", label)
        if event.source == Source.TRIANGULATION and tier != "corroborated_public_source":
            return CandidateValidation(False, "unverified_triangulation", label)
    else:
        has_profile = bool(
            event.raw_payload.get("profile_url")
            or event.raw_payload.get("linkedin_url")
            or "/in/" in event.source_url
        )
        has_context = bool(
            event.raw_payload.get("headline")
            or event.raw_payload.get("company_name")
            or event.raw_payload.get("company")
        )
        research_verified = (
            (research_attributed or batch_hypothesis_verified)
            and bool(event.metadata.get("identity_validated"))
            and bool(event.metadata.get("identity_evidence"))
            and int(event.metadata.get("supporting_source_count", 0)) >= 2
        )
        claim = event.raw_payload.get("founder_claim")
        try:
            profile = urlsplit(str(event.raw_payload.get("profile_url") or ""))
        except ValueError:
            return CandidateValidation(False, "invalid_profile_reference", label)
        claim_name = (
            str(
                claim.get("full_name")
                or claim.get("name")
                or " ".join(
                    str(claim.get(key) or "").strip() for key in ("first_name", "last_name")
                )
            ).strip()
            if isinstance(claim, dict)
            else ""
        )
        official_founder = (
            event.source == Source.DIRECTORY
            and event.metadata.get("identity_evidence") == "explicit_public_founder_record"
            and bool(event.metadata.get("parent_event_id"))
            and event.raw_payload.get("company_source_url") == event.source_url
            and bool(event.raw_payload.get("company_source_id"))
            and has_context
            and len(label.split()) >= 2
            and profile.scheme == "https"
            and bool(profile.hostname)
            and isinstance(claim, dict)
            and claim_name == label
            and bool(
                claim.get("_claim_container") == "founder_set"
                or re.search(
                    r"\b(?:co[- ]?)?founder\b",
                    str(claim.get("title") or ""),
                    re.I,
                )
            )
            and str(event.raw_payload.get("profile_url"))
            in {claim.get("linkedin_url"), claim.get("website_url")}
        )
        if (
            not research_verified
            and not official_founder
            and not (event.source == Source.LINKEDIN and has_profile and has_context)
        ):
            return CandidateValidation(False, "insufficient_person_identity_evidence", label)
    return CandidateValidation(True, label=label)


def _validated_batch_hypothesis(
    event: CanonicalEvent,
    identity_urls: object,
) -> bool:
    """Recognize the aggregate M8 promotion contract without blessing search siblings."""
    if event.source != Source.RESEARCH_AGENT:
        return False
    assessment = event.metadata.get("assessment")
    return bool(
        event.metadata.get("candidate_hypothesis_id")
        and event.metadata.get("evidence_tier") == "corroborated_public_source"
        and isinstance(identity_urls, list)
        and identity_urls
        and isinstance(assessment, dict)
        and assessment.get("promotion_eligible") is True
        and assessment.get("identity_corroborated") is True
        and int(assessment.get("independent_supporting_sources", 0)) >= 2
    )


def validated_entity_record(record: Any) -> bool:
    """Defensive API/UI predicate for materialized canonical entity records."""
    name = getattr(record, "canonical_name", None)
    return isinstance(name, str) and not is_generic_label(name)


def validate_materialized_identity(
    canonical_name: str,
    entity_id: str,
) -> CandidateValidation:
    """Apply the centralized identity vocabulary to legacy materialized rows."""
    if is_generic_label(canonical_name):
        return CandidateValidation(False, "generic_label", canonical_name)
    components = [item for item in re.split(r"[_:/#-]+", entity_id) if item]
    if components and is_generic_label(components[-1]):
        return CandidateValidation(False, "generic_identity_component", components[-1])
    return CandidateValidation(True, label=canonical_name)
