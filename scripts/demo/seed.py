"""Write a clearly synthetic demo dataset into a SQLite database.

Every founder, company and program here is fictional. Websites use the
reserved ``example.test`` domain, names are invented, and every event,
record and node carries ``synthetic: true`` provenance naming this dataset.
Never add real people or companies.

The dataset is deterministic for a given seed: ids, names, timestamps and
scores come from ``random.Random(seed)`` and a fixed base time. It is written
only through the public ``Database`` methods, so it exercises the same storage
contract as the pipeline, and it is enough to populate the inbox (alerts),
rankings, evidence, CRM and graph views.

Usage::

    uv run python scripts/demo/seed.py --database data/demo.db [--seed 7]
    uv run vi demo seed --database data/demo.db [--seed 7]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from venture_intelligence.shared.models import (
    Alert,
    CanonicalEvent,
    Company,
    CRMActionType,
    CRMEvent,
    CRMFeedbackKind,
    CRMRecord,
    CRMStage,
    EntityType,
    EventType,
    Evidence,
    FounderRanking,
    GraphEdge,
    GraphEdgeType,
    GraphNode,
    GraphNodeType,
    Observation,
    Person,
    RankingDimension,
    Recommendation,
    RecommendationAction,
    Signal,
    SignalType,
    Source,
    TemporalPrecision,
)
from venture_intelligence.shared.models.ranking import DIMENSION_WEIGHTS
from venture_intelligence.storage.db import Database

DATASET_ID = "vi-demo-synthetic-v1"
DEFAULT_SEED = 7
DEFAULT_COMPANIES = 12
BASE_TIME = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
NOTICE = "Synthetic demo data: fictional founders, companies and programs."
PIPELINE_WORKERS = (
    "validator",
    "resolver",
    "graph_builder",
    "evidence_builder",
    "signal_extractor",
    "ranker",
    "recommender",
    "notifier",
)

# Fictional vocabulary. Company names are invented compounds; people use
# made-up surnames that announce themselves as sample data.
_PROGRAMS = (
    ("demo_harbor", "Harbor Demo Accelerator", "HD1"),
    ("demo_summit", "Summit Demo Fellowship", "SD2"),
    ("demo_orbit", "Orbit Demo Residency", "OD3"),
)
_NAME_HEADS = (
    "Amberquill", "Brindlecove", "Cindervale", "Dovetonic", "Emberlark", "Fernwhistle",
    "Glimmerfen", "Hollowmint", "Ironpetal", "Junipath", "Kestrelmoss", "Lumenbrook",
    "Mistvane", "Nettlegrove", "Opalwick", "Pebblecrest",
)
_NAME_TAILS = ("Labs", "Systems", "Works", "Analytics", "Robotics", "Bio")
_SECTORS = ("developer tools", "climate", "health", "fintech", "robotics", "AI infrastructure")
_FIRST_NAMES = (
    "Ari", "Bo", "Cass", "Dev", "Eli", "Fen", "Gale", "Hollis", "Indy", "Jules",
    "Kit", "Lark", "Milo", "Nova", "Oak", "Pax", "Quinn", "Rue", "Sage", "Tam",
)
_SURNAMES = (
    "Samplewood", "Testerly", "Mockingdale", "Fixturson", "Placeholdt", "Demovic",
    "Seedlingham", "Synthwell",
)
_CRM_STAGES = (
    CRMStage.QUALIFICATION,
    CRMStage.PARTNER_REVIEW,
    CRMStage.INVESTIGATION,
    CRMStage.RELATIONSHIP,
    CRMStage.PASSED,
    CRMStage.DISCOVERY,
)


def _provenance(seed: int, **extra: Any) -> dict[str, Any]:
    return {"synthetic": True, "dataset": DATASET_ID, "seed": seed, "notice": NOTICE, **extra}


@dataclass
class DemoDataset:
    """Every record the seeder writes, built before any I/O."""

    seed: int
    programs: list[dict[str, str]] = field(default_factory=list)
    companies: list[Company] = field(default_factory=list)
    persons: list[Person] = field(default_factory=list)
    events: list[CanonicalEvent] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    rankings: list[FounderRanking] = field(default_factory=list)
    graph_nodes: list[GraphNode] = field(default_factory=list)
    graph_edges: list[GraphEdge] = field(default_factory=list)
    crm_records: list[CRMRecord] = field(default_factory=list)
    crm_events: list[CRMEvent] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    # (entity_id, entity_type, evidence_count) and (canonical_id, kind, source, source_id)
    validations: list[tuple[str, str, int]] = field(default_factory=list)
    links: list[tuple[str, str, str, str]] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "programs": len(self.programs),
            "companies": len(self.companies),
            "persons": len(self.persons),
            "events": len(self.events),
            "observations": len(self.observations),
            "evidence": len(self.evidence),
            "signals": len(self.signals),
            "rankings": len(self.rankings),
            "graph_nodes": len(self.graph_nodes),
            "graph_edges": len(self.graph_edges),
            "crm_records": len(self.crm_records),
            "crm_events": len(self.crm_events),
            "recommendations": len(self.recommendations),
            "alerts": len(self.alerts),
        }

    def digest(self) -> str:
        """Content digest; equal digests mean byte-identical generated records."""
        payload = {
            name: [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in getattr(self, name)
            ]
            for name in (*self.counts(), "validations", "links")
        }
        encoded = json.dumps(payload, sort_keys=True, default=str).encode()
        return hashlib.sha256(encoded).hexdigest()


class _Builder:
    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self.data = DemoDataset(seed=seed)
        self._namespace = uuid.uuid5(uuid.NAMESPACE_URL, f"https://example.test/{DATASET_ID}")

    def uid(self, *parts: object) -> str:
        return str(uuid.uuid5(self._namespace, ":".join(str(item) for item in (self.seed, *parts))))

    def at(self, days: float) -> datetime:
        return BASE_TIME + timedelta(days=days)

    def evidence_for(
        self,
        *,
        entity_id: str,
        entity_type: EntityType,
        event_type: EventType,
        source: Source,
        url: str,
        claim: str,
        day: float,
        metadata: dict[str, Any],
        payload: dict[str, Any],
    ) -> Evidence:
        event = CanonicalEvent(
            event_id=self.uid("event", entity_id, event_type.value),
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            source=source,
            source_url=url,
            occurred_at=self.at(day - 1),
            occurred_at_precision=TemporalPrecision.DAY,
            observed_at=self.at(day),
            ingested_at=self.at(day),
            processed_at=self.at(day),
            confidence=0.9,
            raw_payload={**payload, "synthetic": True},
            metadata=_provenance(self.seed, **metadata),
        )
        observation = Observation(
            observation_id=self.uid("observation", event.event_id),
            event_id=event.event_id,
            entity_id=entity_id,
            source=source,
            fact=claim,
            value={"synthetic": True},
            observed_at=event.observed_at,
            confidence=0.9,
        )
        evidence = Evidence(
            evidence_id=self.uid("evidence", event.event_id),
            entity_id=entity_id,
            claim=claim,
            observation_ids=[observation.observation_id],
            confidence=0.85,
            observed_at=event.observed_at,
        )
        self.data.events.append(event)
        self.data.observations.append(observation)
        self.data.evidence.append(evidence)
        return evidence

    def ranking_for(self, entity_id: str, evidence: list[Evidence], signals: list[Signal]) -> None:
        dimensions = [
            RankingDimension(
                name=name,
                score=round(self.rng.uniform(0.25, 0.95), 3),
                weight=weight,
                evidence="Synthetic demo score derived from fictional evidence.",
                signal_ids=[item.signal_id for item in signals],
            )
            for name, weight in DIMENSION_WEIGHTS.items()
        ]
        composite = round(sum(item.score * item.weight for item in dimensions), 4)
        self.data.rankings.append(
            FounderRanking(
                ranking_id=self.uid("ranking", entity_id),
                entity_id=entity_id,
                rank=0,  # assigned once every entity is scored
                composite_score=composite,
                dimensions=dimensions,
                hypothesis_ids=[],
                evidence_ids=[item.evidence_id for item in evidence],
                signal_ids=[item.signal_id for item in signals],
                reasoning=(
                    "Synthetic demo ranking. Scores are generated from fictional evidence "
                    "to exercise the rankings view."
                ),
                confidence=round(self.rng.uniform(0.55, 0.9), 3),
                ranked_at=self.at(20),
            )
        )

    def build(self, company_count: int) -> DemoDataset:
        data = self.data
        for program_id, name, batch in _PROGRAMS:
            data.programs.append({"program_id": program_id, "name": name, "batch": batch})
            data.graph_nodes.append(
                GraphNode(
                    node_id=program_id,
                    node_type=GraphNodeType.ACCELERATOR,
                    label=name,
                    properties=_provenance(self.seed, batch=batch),
                    created_at=self.at(0),
                    updated_at=self.at(0),
                )
            )
        heads = self.rng.sample(_NAME_HEADS, k=min(company_count, len(_NAME_HEADS)))
        people = [(first, last) for last in _SURNAMES for first in _FIRST_NAMES]
        self.rng.shuffle(people)
        for index, head in enumerate(heads):
            self._company(index, head, people)
        ordered = sorted(data.rankings, key=lambda item: (-item.composite_score, item.entity_id))
        data.rankings = [
            item.model_copy(update={"rank": rank}) for rank, item in enumerate(ordered, start=1)
        ]
        self._decisions()
        return data

    def _company(self, index: int, head: str, people: list[tuple[str, str]]) -> None:
        data = self.data
        program_id, _, batch = _PROGRAMS[index % len(_PROGRAMS)]
        tail = self.rng.choice(_NAME_TAILS)
        sector = self.rng.choice(_SECTORS)
        name = f"{head} {tail}"
        slug = head.casefold()
        domain = f"{slug}.example.test"
        company_id = f"demo__{slug}"
        source_id = f"website__{slug}"
        day = 1 + index
        meta = {"program_id": program_id, "batch_id": batch}
        company_evidence = [
            self.evidence_for(
                entity_id=company_id,
                entity_type=EntityType.COMPANY,
                event_type=EventType.COMPANY_LISTED,
                source=Source.WEBSITE,
                url=f"https://{domain}/",
                claim=f"{name} describes itself as a {sector} company (synthetic).",
                day=day,
                metadata=meta,
                payload={"name": name, "slug": slug},
            ),
            self.evidence_for(
                entity_id=company_id,
                entity_type=EntityType.COMPANY,
                event_type=EventType.PRODUCT_LAUNCHED,
                source=Source.BLOG,
                url=f"https://news.example.test/{slug}-launch",
                claim=f"{name} announced a product launch in {batch} (synthetic).",
                day=day + 3,
                metadata=meta,
                payload={"name": name, "product": f"{head} One"},
            ),
        ]
        founder_count = 1 + self.rng.randrange(2)
        founders: list[Person] = []
        for offset in range(founder_count):
            first, last = people.pop()
            person_id = f"demo__{first.casefold()}-{last.casefold()}"
            founders.append(
                Person(
                    person_id=person_id,
                    canonical_name=f"{first} {last}",
                    current_title="Co-founder" if founder_count > 1 else "Founder",
                    current_company_id=company_id,
                    batch=batch,
                    identity_confidence=0.9,
                    first_seen_at=self.at(day),
                    updated_at=self.at(day),
                    properties=_provenance(self.seed, program_id=program_id),
                )
            )
        company = Company(
            company_id=company_id,
            canonical_name=name,
            slug=slug,
            website=f"https://{domain}",
            description=f"Synthetic demo {sector} company. Not a real business.",
            batch=batch,
            tags=[sector, "synthetic"],
            founder_ids=[item.person_id for item in founders],
            identity_confidence=0.92,
            first_seen_at=self.at(day),
            updated_at=self.at(day),
            properties=_provenance(self.seed, program_id=program_id),
        )
        data.companies.append(company)
        data.links.append((company_id, "company", Source.WEBSITE.value, source_id))
        data.validations.append((company_id, "company", len(company_evidence)))
        company_signals = [
            Signal(
                signal_id=self.uid("signal", company_id, signal_type.value),
                entity_id=company_id,
                signal_type=signal_type,
                value=round(self.rng.uniform(0.4, 0.95), 3),
                evidence_ids=[item.evidence_id for item in company_evidence],
                explanation=explanation,
                confidence=0.8,
                observed_at=self.at(day + 4),
            )
            for signal_type, explanation in (
                (SignalType.PROGRAM_AFFINITY, f"Synthetic: listed with {program_id}."),
                (SignalType.TECHNICAL_DEPTH, "Synthetic: launch notes describe deep tech work."),
            )
        ]
        data.signals.extend(company_signals)
        self.ranking_for(company_id, company_evidence, company_signals)
        data.graph_nodes.append(
            GraphNode(
                node_id=company_id,
                node_type=GraphNodeType.COMPANY,
                label=name,
                properties=_provenance(self.seed, website=company.website, batch=batch),
                created_at=self.at(day),
                updated_at=self.at(day),
            )
        )
        data.graph_edges.append(
            GraphEdge(
                edge_id=self.uid("edge", company_id, program_id),
                source_id=company_id,
                target_id=program_id,
                edge_type=GraphEdgeType.MEMBER_OF,
                confidence=0.85,
                observed_at=self.at(day),
                valid_from=self.at(day - 1),
                evidence_ids=[company_evidence[0].evidence_id],
                properties=_provenance(self.seed),
            )
        )
        for person in founders:
            data.persons.append(person)
            person_evidence = [
                self.evidence_for(
                    entity_id=person.person_id,
                    entity_type=EntityType.PERSON,
                    event_type=EventType.FOUNDER_DETECTED,
                    source=Source.WEBSITE,
                    url=f"https://{domain}/team",
                    claim=(
                        f"{person.canonical_name} is listed as {person.current_title} "
                        f"of {name} (synthetic)."
                    ),
                    day=day + 1,
                    metadata=meta,
                    payload={"name": person.canonical_name, "company": name},
                )
            ]
            data.validations.append((person.person_id, "person", len(person_evidence)))
            person_signal = Signal(
                signal_id=self.uid("signal", person.person_id, SignalType.REPEAT_FOUNDER.value),
                entity_id=person.person_id,
                signal_type=SignalType.REPEAT_FOUNDER,
                value=round(self.rng.uniform(0.2, 0.9), 3),
                evidence_ids=[item.evidence_id for item in person_evidence],
                explanation="Synthetic: fictional founder history.",
                confidence=0.7,
                observed_at=self.at(day + 2),
            )
            data.signals.append(person_signal)
            self.ranking_for(person.person_id, person_evidence, [person_signal])
            data.graph_nodes.append(
                GraphNode(
                    node_id=person.person_id,
                    node_type=GraphNodeType.PERSON,
                    label=person.canonical_name,
                    properties=_provenance(self.seed, title=person.current_title),
                    created_at=self.at(day + 1),
                    updated_at=self.at(day + 1),
                )
            )
            data.graph_edges.append(
                GraphEdge(
                    edge_id=self.uid("edge", person.person_id, company_id),
                    source_id=person.person_id,
                    target_id=company_id,
                    edge_type=GraphEdgeType.FOUNDED,
                    confidence=0.85,
                    observed_at=self.at(day + 1),
                    valid_from=self.at(day - 30),
                    evidence_ids=[person_evidence[0].evidence_id],
                    properties=_provenance(self.seed),
                )
            )

    def _decisions(self) -> None:
        data = self.data
        company_ids = {item.company_id: item for item in data.companies}
        ranked_companies = [item for item in data.rankings if item.entity_id in company_ids]
        for index, ranking in enumerate(ranked_companies[: len(_CRM_STAGES)]):
            stage = _CRM_STAGES[index]
            passed = stage is CRMStage.PASSED
            note = f"Synthetic demo note for {company_ids[ranking.entity_id].canonical_name}."
            data.crm_records.append(
                CRMRecord(
                    crm_id=self.uid("crm", ranking.entity_id),
                    entity_id=ranking.entity_id,
                    stage=stage,
                    notes=[note],
                    decision="Synthetic pass: outside the demo thesis." if passed else None,
                    created_at=self.at(21),
                    updated_at=self.at(22 + index),
                )
            )
            data.crm_events.append(
                CRMEvent(
                    crm_event_id=self.uid("crm-event", ranking.entity_id),
                    entity_id=ranking.entity_id,
                    action=CRMActionType.PASSED if passed else CRMActionType.STAGE_CHANGED,
                    feedback_kind=CRMFeedbackKind.WORKFLOW,
                    from_stage=CRMStage.DISCOVERY,
                    to_stage=stage,
                    note=note,
                    partner="Demo Partner (synthetic)",
                    ranking_id=ranking.ranking_id,
                    provenance=_provenance(self.seed),
                    occurred_at=self.at(22 + index),
                    observed_at=self.at(22 + index),
                    created_at=self.at(22 + index),
                )
            )
        evidence_by_entity: dict[str, list[Evidence]] = {}
        for item in data.evidence:
            evidence_by_entity.setdefault(item.entity_id, []).append(item)
        urls = {item.event_id: item.source_url for item in data.events}
        observation_events = {item.observation_id: item.event_id for item in data.observations}
        for index, ranking in enumerate(ranked_companies[:5]):
            company = company_ids[ranking.entity_id]
            evidence = evidence_by_entity[ranking.entity_id]
            action = RecommendationAction.CONTACT if index < 2 else RecommendationAction.MONITOR
            recommendation = Recommendation(
                recommendation_id=self.uid("recommendation", ranking.entity_id),
                entity_id=ranking.entity_id,
                action=action,
                reasoning=f"Synthetic demo recommendation for {company.canonical_name}.",
                evidence_ids=[item.evidence_id for item in evidence],
                ranking_id=ranking.ranking_id,
                signal_ids=ranking.signal_ids,
                confidence=ranking.confidence,
                created_at=self.at(23 + index),
            )
            data.recommendations.append(recommendation)
            data.alerts.append(
                Alert(
                    alert_id=self.uid("alert", ranking.entity_id),
                    recommendation_id=recommendation.recommendation_id,
                    entity_id=ranking.entity_id,
                    entity_name=company.canonical_name,
                    action=action,
                    reasoning=recommendation.reasoning,
                    evidence_summary=[item.claim for item in evidence],
                    rank=ranking.rank,
                    confidence=ranking.confidence,
                    source_urls=[
                        urls[observation_events[observation_id]]
                        for item in evidence
                        for observation_id in item.observation_ids
                    ],
                    fired_at=self.at(23 + index),
                )
            )


def build_dataset(seed: int = DEFAULT_SEED, *, companies: int = DEFAULT_COMPANIES) -> DemoDataset:
    """Build the synthetic dataset in memory; deterministic for ``seed``."""
    return _Builder(seed).build(companies)


def write_dataset(dataset: DemoDataset, database: Database) -> None:
    """Write ``dataset`` through the public ``Database`` methods."""
    for event in dataset.events:
        database.write_event(event)
        # Records below are already projected; keep the pipeline from re-deriving them.
        for worker in PIPELINE_WORKERS:
            database.mark_processed(event.event_id, worker)
    for company in dataset.companies:
        database.write_company(company)
    for person in dataset.persons:
        database.write_person(person)
    for canonical_id, kind, source, source_id in dataset.links:
        database.write_entity_link(canonical_id, kind, source, source_id, 0.9)
    for observation in dataset.observations:
        database.write_observation(observation)
    for evidence in dataset.evidence:
        database.write_evidence(evidence)
    for entity_id, entity_type, evidence_count in dataset.validations:
        database.write_entity_validation(
            entity_id,
            entity_type,
            reason="synthetic demo record",
            evidence_count=evidence_count,
            provenance=_provenance(dataset.seed),
        )
    for signal in dataset.signals:
        database.write_signal(signal)
    for ranking in dataset.rankings:
        database.write_ranking(ranking)
    for node in dataset.graph_nodes:
        database.write_graph_node(node)
    for edge in dataset.graph_edges:
        database.write_graph_edge(edge)
    for record in dataset.crm_records:
        database.write_crm(record)
    for crm_event in dataset.crm_events:
        database.write_crm_event(crm_event)
    for recommendation in dataset.recommendations:
        database.write_recommendation(recommendation)
    for alert in dataset.alerts:
        database.write_alert(alert)


def seed_database(
    path: str | Path,
    *,
    seed: int = DEFAULT_SEED,
    companies: int = DEFAULT_COMPANIES,
) -> dict[str, Any]:
    """Write the synthetic dataset to ``path`` and return a summary."""
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    dataset = build_dataset(seed, companies=companies)
    write_dataset(dataset, Database(str(target)))
    return {
        "status": "seeded",
        "synthetic": True,
        "dataset": DATASET_ID,
        "seed": seed,
        "database": str(target),
        "counts": dataset.counts(),
        "digest": dataset.digest(),
        "notice": NOTICE,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", required=True, help="SQLite path to write.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)
    print(json.dumps(seed_database(args.database, seed=args.seed), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
