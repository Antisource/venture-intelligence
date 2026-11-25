"""Bounded analyst chat boundary grounded in canonical evidence."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from venture_intelligence.agents.analyst import AnalystAgent
from venture_intelligence.api.routers.dependencies import _get_db
from venture_intelligence.batch_discovery.repository import BatchDiscoveryRepository
from venture_intelligence.config import settings
from venture_intelligence.operations.cycle import OperationsStateStore
from venture_intelligence.programs import get_program_registry

router = APIRouter()


class AnalystRequest(BaseModel):
    question: str
    context_name: str | None = Field(default=None, max_length=200)


@router.post("/api/analyst/chat")
async def analyst_chat(request: AnalystRequest) -> dict:
    """Bounded analyst boundary; cloud reasoning remains opt-in and separate."""
    question = request.question.strip()
    if not question or len(question) > 2_000:
        raise HTTPException(422, "question must contain 1-2000 characters")
    context_name = (request.context_name or "").strip()
    if context_name and _is_cross_entity_question(question):
        return {
            "question": question,
            "answer": (
                f"This assistant is scoped to {context_name}. "
                "Use Discover or command search for cross-company questions."
            ),
            "model": "deterministic-scope-boundary",
            "evidence_sufficient": False,
            "epistemic_state": "unavailable",
            "caveats": ["No global answer was generated from entity-local evidence."],
            "citations": [],
        }
    result = await AnalystAgent().answer(
        question[:2_000],
        context=_grounded_analyst_context(
            f"{context_name} {question}" if context_name else question
        ),
    )
    return {"question": question, **result.model_dump(mode="json")}


def _is_cross_entity_question(question: str) -> bool:
    normalized = " ".join(question.casefold().split())
    return bool(
        re.search(
            r"\b(?:list|show|give me|compare)\b.{0,80}"
            r"\b(?:company|companies|founder|founders|startup|startups|entity|entities)\b",
            normalized,
        )
        or re.search(
            r"\b(?:all|every|which)\b.{0,80}"
            r"\b(?:company|companies|founder|founders|startup|startups|entity|entities)\b",
            normalized,
        )
    )


def _grounded_analyst_context(question: str) -> dict:
    database = _get_db()
    from venture_intelligence.intelligence.service import IntelligenceService

    entities = IntelligenceService(database).analyst_entities(question)
    repository = BatchDiscoveryRepository(database)
    candidates: list[dict] = []
    citations: list[dict[str, str]] = [
        {"label": item["claim"], "url": url}
        for entity in entities
        for item in entity["evidence"]
        for url in item["source_urls"]
    ]
    terms = {token for token in question.casefold().replace("?", " ").split() if len(token) > 2}
    for program in get_program_registry().active():
        for candidate in repository.latest_hypotheses(
            program.id,
            program.batch,
            include_rejected=True,
        ):
            searchable = " ".join(
                [
                    candidate.display_name,
                    candidate.candidate_name or "",
                    candidate.company_name or "",
                    *candidate.aliases,
                    *candidate.usernames,
                ]
            ).casefold()
            if terms and not any(term in searchable for term in terms):
                continue
            observations = repository.observations_by_ids(candidate.observation_ids)
            sources = [
                {
                    "label": item.source_domain,
                    "url": item.canonical_url,
                    "evidence_id": item.observation_id,
                    "stance": item.stance.value,
                }
                for item in observations[:8]
            ]
            citations.extend({"label": item["label"], "url": item["url"]} for item in sources)
            candidates.append(
                {
                    "hypothesis_id": candidate.hypothesis_id,
                    "display_name": candidate.display_name,
                    "confidence_pct": candidate.assessment.confidence_pct,
                    "identity_confidence_pct": (candidate.assessment.identity_confidence_pct),
                    "state": candidate.state.value,
                    "epistemic_state": (
                        "known"
                        if candidate.state.value == "confirmed"
                        else "contradicted"
                        if candidate.assessment.contradictory_observations
                        and not candidate.assessment.batch_evidence_present
                        else "unavailable"
                        if candidate.assessment.source_coverage.checked_families == 0
                        and candidate.assessment.source_coverage.unavailable_families > 0
                        else "hypothesized"
                    ),
                    "reason": (
                        candidate.assessment.components[0].explanation
                        if candidate.assessment.components
                        else "Insufficient evidence — candidate not promoted"
                    ),
                    "contradictions": candidate.assessment.contradictory_observations,
                    "promotion_blockers": candidate.assessment.promotion_blockers,
                    "why_now": candidate.why_now,
                    "next_best_checks": candidate.assessment.next_best_checks,
                    "source_coverage": candidate.assessment.source_coverage.model_dump(mode="json"),
                    "investment_attention_pct": (candidate.profile.investment_attention_pct),
                    "sources": sources,
                }
            )
            if len(candidates) >= 8:
                break
    operations = OperationsStateStore(settings.operations_state_path).load()
    raw_changes = [
        change.model_dump(mode="json")
        for cycle in operations.cycles[:3]
        for result in cycle.program_results
        for change in result.changes
    ]
    changes_by_event: dict[tuple[str, tuple[str, ...], tuple[str, ...]], dict] = {}
    for change in raw_changes:
        key = (
            str(change["hypothesis_id"]),
            tuple(sorted(change.get("causal_observation_ids", []))),
            tuple(sorted(change.get("change_types", []))),
        )
        changes_by_event.setdefault(key, change)
    changes = list(changes_by_event.values())[:8]
    unavailable_statuses = {
        "blocked",
        "circuit_open",
        "failed",
        "network_error",
        "rate_limited",
        "timeout",
        "unavailable",
    }
    important_sources_unavailable = any(
        str(skipped.get("status", "")).casefold() in unavailable_statuses
        for cycle in operations.cycles[:1]
        for result in cycle.program_results
        for skipped in result.providers_skipped
    )
    return {
        "source": "canonical_database",
        "entities": entities,
        "question": question,
        "candidates": candidates,
        "changes": changes,
        "citations": citations[:16],
        "important_sources_unavailable": important_sources_unavailable,
    }
