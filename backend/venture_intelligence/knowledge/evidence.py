"""Evidence categorisation and deterministic confidence calculations.

The knowledge layer accepts evidence from several collectors.  This module is
the single, side-effect-free place that turns a raw evidence mapping into the
JSON contract rendered by the wiki and UI.  It deliberately never promotes an
observation to an entity.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterable
from urllib.parse import urlsplit

from venture_intelligence.programs import get_program_registry


class EvidenceCategory(str, Enum):
    FIRST_PARTY_IDENTITY = "first_party_identity"
    ACCELERATOR_PROGRAM = "accelerator_program"
    FOUNDER_BACKGROUND = "founder_background"
    COMPANY_ACTIVITY = "company_activity"
    TECHNICAL_PUBLICATION = "technical_publication"
    SOCIAL_ANNOUNCEMENT = "social_announcement"
    HISTORICAL_ARCHIVED = "historical_archived"


_SOURCE_WEIGHTS = {
    "first_party": 0.92,
    "accelerator": 0.94,
    "official": 0.90,
    "secondary": 0.72,
    "indexed_social": 0.52,
    "archive": 0.62,
    "unknown": 0.45,
}


def _program_domains() -> set[str]:
    return {
        (urlsplit(url).hostname or "").casefold().removeprefix("www.")
        for program in get_program_registry().all()
        for url in (
            program.directory_url,
            program.talent_page_url,
            program.substack_rss_url,
            *program.blog_urls,
            *program.website_urls,
        )
        if url
    }


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def confidence_label(value: float) -> str:
    value = _clamp(value)
    if value < 0.50:
        return "low"
    if value < 0.75:
        return "medium"
    if value < 0.90:
        return "high"
    return "very_high"


def source_tier(evidence: dict[str, Any]) -> str:
    explicit = str(evidence.get("confidence_basis", {}).get("source_tier", "")).casefold()
    if explicit in _SOURCE_WEIGHTS:
        return explicit
    domain = str(evidence.get("source_domain", "")).casefold()
    if domain.removeprefix("www.") in _program_domains():
        return "accelerator"
    if domain in {"github.com", "arxiv.org", "hacker-news.firebaseio.com"}:
        return "first_party"
    if (
        domain in {"web.archive.org", "wayback.archive-it.org"}
        or evidence.get("temporal_status") == "historical"
    ):
        return "archive"
    if domain in {
        "linkedin.com",
        "x.com",
        "twitter.com",
        "instagram.com",
        "threads.net",
    }:
        return "indexed_social"
    if evidence.get("direct_public"):
        return "first_party"
    return "secondary"


def categorise_evidence(evidence: dict[str, Any]) -> EvidenceCategory:
    """Return a stable category using explicit metadata before safe heuristics."""
    raw = str(evidence.get("category", "")).casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "identity": EvidenceCategory.FIRST_PARTY_IDENTITY,
        "first_party": EvidenceCategory.FIRST_PARTY_IDENTITY,
        "program": EvidenceCategory.ACCELERATOR_PROGRAM,
        "accelerator": EvidenceCategory.ACCELERATOR_PROGRAM,
        "founder": EvidenceCategory.FOUNDER_BACKGROUND,
        "technical": EvidenceCategory.TECHNICAL_PUBLICATION,
        "publication": EvidenceCategory.TECHNICAL_PUBLICATION,
        "social": EvidenceCategory.SOCIAL_ANNOUNCEMENT,
        "announcement": EvidenceCategory.SOCIAL_ANNOUNCEMENT,
        "historical": EvidenceCategory.HISTORICAL_ARCHIVED,
        "archive": EvidenceCategory.HISTORICAL_ARCHIVED,
    }
    if raw in {item.value for item in EvidenceCategory}:
        return EvidenceCategory(raw)
    if raw in aliases:
        return aliases[raw]
    if str(evidence.get("temporal_status", "")).casefold() in {"historical", "stale"}:
        return EvidenceCategory.HISTORICAL_ARCHIVED
    domain = str(evidence.get("source_domain", "")).casefold()
    title = str(evidence.get("title", "")).casefold()
    if domain.removeprefix("www.") in _program_domains():
        return EvidenceCategory.ACCELERATOR_PROGRAM
    if domain in {"github.com", "arxiv.org"} or any(
        word in title for word in ("paper", "publication", "repository")
    ):
        return EvidenceCategory.TECHNICAL_PUBLICATION
    if domain in {
        "linkedin.com",
        "x.com",
        "twitter.com",
        "instagram.com",
        "threads.net",
    }:
        return EvidenceCategory.SOCIAL_ANNOUNCEMENT
    if evidence.get("direct_public") or domain.startswith("www."):
        return EvidenceCategory.FIRST_PARTY_IDENTITY
    return EvidenceCategory.COMPANY_ACTIVITY


def normalize_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Add category and explainable percentage fields without mutating input."""
    item = dict(evidence)
    category = categorise_evidence(item)
    tier = source_tier(item)
    basis = dict(item.get("confidence_basis") or {})
    basis.setdefault("source_tier", tier)
    basis.setdefault("identity_match", 1.0)
    basis.setdefault("corroborating_sources", 0)
    basis.setdefault("recency_factor", 1.0)
    basis.setdefault("contradictions", 0)
    raw_confidence = _clamp(float(item.get("confidence", item.get("confidence_pct", 0)) or 0))
    source_score = _SOURCE_WEIGHTS[tier]
    if raw_confidence <= 0:
        raw_confidence = source_score
    score = min(raw_confidence, source_score)
    score *= _clamp(float(basis["identity_match"]))
    score *= _clamp(float(basis["recency_factor"]))
    score += min(0.15, 0.05 * max(0, int(basis["corroborating_sources"])))
    score -= min(0.45, 0.15 * max(0, int(basis["contradictions"])))
    if category is EvidenceCategory.HISTORICAL_ARCHIVED:
        score = min(score, 0.75)
    score = _clamp(score)
    item["category"] = category.value
    item["confidence"] = score
    item["confidence_pct"] = round(score * 100)
    item["confidence_label"] = confidence_label(score)
    item["confidence_basis"] = basis
    return item


def summarize_evidence(evidence: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Return category buckets and an entity-level confidence summary."""
    buckets: dict[str, list[dict[str, Any]]] = {item.value: [] for item in EvidenceCategory}
    normalized = [normalize_evidence(item) for item in evidence]
    for item in normalized:
        buckets[item["category"]].append(item)
    category_confidence: dict[str, dict[str, Any]] = {}
    for category, items in buckets.items():
        if not items:
            category_confidence[category] = {
                "confidence": 0.0,
                "confidence_pct": 0,
                "label": "unknown",
                "evidence_count": 0,
            }
            continue
        score = sum(float(item["confidence"]) for item in items) / len(items)
        unique_sources = {
            str(item.get("canonical_url") or item.get("url") or item.get("source_domain"))
            for item in items
        }
        if len(unique_sources) < 2:
            score = min(score, 0.95)
        category_confidence[category] = {
            "confidence": round(_clamp(score), 4),
            "confidence_pct": round(_clamp(score) * 100),
            "label": confidence_label(score),
            "evidence_count": len(items),
        }
    active = [
        item
        for item in normalized
        if item.get("temporal_status", "current") not in {"stale", "historical"}
    ]
    overall = sum(float(item["confidence"]) for item in active) / len(active) if active else 0.0
    unique_domains = {
        str(item.get("source_domain") or item.get("canonical_url") or item.get("url"))
        for item in active
    }
    if len(unique_domains) < 2:
        overall = min(overall, 0.95)
    return {
        "items": normalized,
        "categories": buckets,
        "category_confidence": category_confidence,
        "overall_confidence": round(_clamp(overall), 4),
        "overall_confidence_pct": round(_clamp(overall) * 100),
        "overall_confidence_label": confidence_label(overall),
    }
