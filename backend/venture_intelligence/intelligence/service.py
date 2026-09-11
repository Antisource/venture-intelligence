"""Read-only assembly of historically bounded M4 intelligence views."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from venture_intelligence.intelligence.models import (
    EntityIntelligenceResponse,
    EntitySummary,
    EvidenceLineageRecord,
    LineageEdge,
    RankingIntelligenceItem,
    RankingIntelligenceResponse,
    RankingMovement,
)
from venture_intelligence.pipeline.entity_integrity import validated_entity_record
from venture_intelligence.programs import get_program_registry
from venture_intelligence.shared.models import FounderRanking
from venture_intelligence.storage.db import Database


class IntelligenceService:
    """Expose canonical projections without recomputing or mutating pipeline state."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def rankings(
        self,
        *,
        as_of: datetime | None = None,
        entity_type: str | None = None,
        program_id: str | None = None,
        batch_id: str | None = None,
        limit: int = 100,
    ) -> RankingIntelligenceResponse:
        cutoff = _utc(as_of or datetime.now(timezone.utc))
        if program_id and batch_id is None:
            program = get_program_registry().get(program_id)
            batch_id = program.batch if program is not None else None
        items: list[RankingIntelligenceItem] = []
        projected_ids: set[str] = set()
        scan_limit = limit
        if entity_type or program_id or batch_id:
            scan_limit = max(
                limit,
                len(self._database.all_companies()) + len(self._database.all_persons()),
            )
        for ranking in self._database.get_rankings(limit=scan_limit, as_of=cutoff):
            summary = self._summary(ranking.entity_id)
            if summary is None or (entity_type and summary.entity_type != entity_type):
                continue
            if program_id and summary.program_id != program_id:
                continue
            if batch_id and summary.batch_id != batch_id:
                continue
            if summary.entity_id in projected_ids:
                continue
            projected_ids.add(summary.entity_id)
            history = self._database.get_ranking_history(ranking.entity_id, as_of=cutoff, limit=2)
            features = _referenced(
                self._database.get_features_for_entity(ranking.entity_id, as_of=cutoff),
                set(ranking.feature_ids),
                "feature_id",
            )
            signals = _referenced(
                self._database.get_signals_for_entity(ranking.entity_id, as_of=cutoff),
                set(ranking.signal_ids),
                "signal_id",
            )
            items.append(
                RankingIntelligenceItem(
                    entity=summary,
                    ranking=ranking,
                    movement=_movement(history),
                    top_signal=max(signals, key=lambda item: item.value, default=None),
                    top_feature=max(features, key=lambda item: item.confidence, default=None),
                )
            )
            if len(items) >= limit:
                break
        return RankingIntelligenceResponse(
            as_of=cutoff,
            program_id=program_id,
            batch_id=batch_id,
            items=items,
        )

    def entity(
        self,
        entity_id: str,
        *,
        as_of: datetime | None = None,
    ) -> EntityIntelligenceResponse | None:
        cutoff = _utc(as_of or datetime.now(timezone.utc))
        evidence_details = self._database.get_evidence_details_for_entity(entity_id, as_of=cutoff)
        summary = self._summary(entity_id, evidence_details)
        if summary is None:
            return None
        evidence = [
            EvidenceLineageRecord(
                evidence_id=item["evidence_id"],
                claim=item["claim"],
                confidence=item["confidence"],
                observed_at=item["observed_at"],
                observation_ids=item["observation_ids"],
                source_urls=item["source_urls"],
                sources=sorted(
                    {observation["source"] for observation in item.get("observations", [])}
                ),
                program_id=item.get("program_id"),
                batch_id=item.get("batch_id"),
                attribution_status=item.get("attribution_status", "attached"),
                attribution_reason=item.get("attribution_reason"),
                evidence_origin_url=item.get("evidence_origin_url"),
                acquisition_providers=item.get("acquisition_providers", []),
                occurred_at=item.get("occurred_at"),
                published_at=item.get("published_at"),
            )
            for item in evidence_details
        ]
        crm_actions = self._database.get_crm_events(entity_id, as_of=cutoff)
        history = self._database.get_ranking_history(entity_id, as_of=cutoff)
        ranking = history[0] if history else None
        all_features = self._database.get_features_for_entity(entity_id, as_of=cutoff)
        all_signals = self._database.get_signals_for_entity(entity_id, as_of=cutoff)
        all_hypotheses = self._database.get_hypotheses_for_entity(entity_id, as_of=cutoff)
        features = (
            _referenced(all_features, set(ranking.feature_ids), "feature_id")
            if ranking
            else _latest(all_features, "name")
        )
        signals = (
            _referenced(all_signals, set(ranking.signal_ids), "signal_id")
            if ranking
            else _latest(all_signals, "signal_type")
        )
        hypotheses = (
            _referenced(all_hypotheses, set(ranking.hypothesis_ids), "hypothesis_id")
            if ranking
            else all_hypotheses
        )
        recommendation = self._database.get_recommendation_for_entity(entity_id, as_of=cutoff)
        return EntityIntelligenceResponse(
            as_of=cutoff,
            entity=summary,
            evidence=evidence,
            crm_actions=crm_actions,
            features=features,
            signals=signals,
            hypotheses=hypotheses,
            ranking=ranking,
            ranking_history=history,
            movement=_movement(history),
            recommendation=recommendation,
            lineage=_lineage(
                evidence,
                crm_actions,
                features,
                signals,
                hypotheses,
                ranking,
                recommendation,
            ),
        )

    def _summary(
        self,
        entity_id: str,
        evidence_details: list[dict] | None = None,
    ) -> EntitySummary | None:
        if not self._database.is_entity_validated(entity_id):
            return None
        details = (
            evidence_details
            if evidence_details is not None
            else self._database.get_evidence_details_for_entity(entity_id)
        )
        identity_status, program_status, origin_count = _entity_statuses(details)
        company = self._database.get_company(entity_id)
        if company is not None and validated_entity_record(company):
            return EntitySummary(
                entity_id=entity_id,
                entity_type="company",
                canonical_name=company.canonical_name,
                identity_confidence=company.identity_confidence,
                identity_status=identity_status,
                program_status=program_status,
                independent_evidence_origins=origin_count,
                website=company.website,
                program_id=str(company.properties.get("program_id") or "") or None,
                batch_id=company.batch,
                founder_ids=company.founder_ids,
            )
        person = self._database.get_person(entity_id)
        if person is not None and validated_entity_record(person):
            return EntitySummary(
                entity_id=entity_id,
                entity_type="person",
                canonical_name=person.canonical_name,
                identity_confidence=person.identity_confidence,
                identity_status=identity_status,
                program_status=program_status,
                independent_evidence_origins=origin_count,
                current_title=person.current_title,
                current_company_id=person.current_company_id,
                program_id=str(person.properties.get("program_id") or "") or None,
                batch_id=person.batch,
            )
        return None

    def analyst_entities(self, question: str, limit: int = 5) -> list[dict]:
        """Return bounded, evidence-backed entities for deterministic analyst context."""
        result = []
        linked_founders: set[str] = set()
        for record in [*self._database.all_companies(), *self._database.all_persons()]:
            name = record.canonical_name
            entity_id = getattr(record, "company_id", None) or record.person_id
            if (
                question
                and entity_id not in linked_founders
                and not re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", question, re.I)
            ):
                continue
            view = self.entity(entity_id)
            if view is None or not view.evidence:
                continue
            linked_founders.update(getattr(record, "founder_ids", []))
            result.append(
                {
                    "entity": view.entity.model_dump(mode="json"),
                    "evidence": [item.model_dump(mode="json") for item in view.evidence[:8]],
                    "semantics": (
                        "Recorded evidence; not proof of current membership or current employment."
                    ),
                }
            )
            if len(result) >= max(1, min(limit, 8)):
                break
        return result


def _entity_statuses(details: list[dict]) -> tuple[str, str, int]:
    attached = [item for item in details if item.get("attribution_status") == "attached"]
    origins = {
        (
            urlsplit(
                str(item.get("evidence_origin_url") or item.get("canonical_url") or "")
            ).hostname
            or ""
        ).casefold()
        for item in attached
        if item.get("evidence_origin_url") or item.get("canonical_url")
    }
    origins.discard("")
    official = any(
        provenance.get("source") in {"directory", "yc_website"}
        for item in attached
        for provenance in item.get("provenance", [])
    )
    contradictory = any(
        item.get("attribution_status") == "contradictory" for item in details
    )
    if official:
        identity_status = "verified"
    elif len(origins) >= 2:
        identity_status = "corroborated"
    elif attached:
        identity_status = "provisional"
    elif details:
        identity_status = "ambiguous"
    else:
        identity_status = "unresolved"

    has_program = any(item.get("program_id") or item.get("batch_id") for item in attached)
    official_program = official and any(
        "program membership" in str(item.get("claim") or "").casefold()
        or "founder program association" in str(item.get("claim") or "").casefold()
        for item in attached
    )
    if contradictory and not has_program:
        program_status = "contradicted"
    elif official_program:
        program_status = "verified"
    elif has_program and len(origins) >= 2:
        program_status = "supported"
    elif has_program:
        program_status = "hypothesis"
    else:
        program_status = "unknown"
    return identity_status, program_status, len(origins)

def _movement(history: list[FounderRanking]) -> RankingMovement:
    if not history:
        return RankingMovement()
    current = history[0]
    if len(history) < 2:
        return RankingMovement(current_rank=current.rank)
    previous = history[1]
    return RankingMovement(
        current_rank=current.rank,
        previous_rank=previous.rank,
        rank_delta=previous.rank - current.rank,
        score_delta=round(current.composite_score - previous.composite_score, 6),
    )


def _lineage(
    evidence,
    crm_actions,
    features,
    signals,
    hypotheses,
    ranking,
    recommendation,
):
    links: set[tuple[str, str, str, str, str]] = set()
    for item in evidence:
        for observation_id in item.observation_ids:
            links.add((observation_id, "observation", item.evidence_id, "evidence", "supports"))
    for feature in features:
        for evidence_id in feature.evidence_ids:
            links.add((evidence_id, "evidence", feature.feature_id, "feature", "computes"))
        for crm_action_id in feature.crm_action_ids:
            links.add((crm_action_id, "crm_action", feature.feature_id, "feature", "computes"))
    for signal in signals:
        for feature_id in signal.feature_ids:
            links.add((feature_id, "feature", signal.signal_id, "signal", "normalizes"))
    for hypothesis in hypotheses:
        for signal_id in hypothesis.signal_ids:
            links.add((signal_id, "signal", hypothesis.hypothesis_id, "hypothesis", "supports"))
    if ranking is not None:
        for signal_id in ranking.signal_ids:
            links.add((signal_id, "signal", ranking.ranking_id, "ranking", "scores"))
        for feature_id in ranking.feature_ids:
            links.add((feature_id, "feature", ranking.ranking_id, "ranking", "traces"))
        for hypothesis_id in ranking.hypothesis_ids:
            links.add((hypothesis_id, "hypothesis", ranking.ranking_id, "ranking", "explains"))
    if recommendation is not None and recommendation.ranking_id:
        links.add(
            (
                recommendation.ranking_id,
                "ranking",
                recommendation.recommendation_id,
                "recommendation",
                "drives",
            )
        )
    return [
        LineageEdge(
            from_id=from_id,
            from_type=from_type,
            to_id=to_id,
            to_type=to_type,
            relation=relation,
        )
        for from_id, from_type, to_id, to_type, relation in sorted(links)
    ]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of must include a UTC offset")
    return value.astimezone(timezone.utc)


def _referenced(items: list, identifiers: set[str], field: str) -> list:
    return [item for item in items if getattr(item, field) in identifiers]


def _latest(items: list, field: str) -> list:
    selected: dict[object, object] = {}
    for item in items:
        selected.setdefault(getattr(item, field), item)
    return list(selected.values())
