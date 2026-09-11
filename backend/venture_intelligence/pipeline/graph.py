"""
pipeline/graph.py — Knowledge Graph Builder (§15).

Translates CanonicalEvents into nodes and edges in the temporal knowledge
graph (SQLite adjacency tables in V1; isomorphic to Neo4j for V2 migration).

Every edge records when its evidence was observed. Historical validity is
recorded only when the source event supplies an explicit validity interval;
observation time is never promoted into historical fact time.

Node upsert strategy:
  Nodes are upserted (INSERT OR REPLACE) so multiple events about the
  same entity accumulate properties rather than creating duplicates.

Edge upsert strategy (§15 "temporal edges"):
  Edges are upserted with ON CONFLICT UPDATE.  If an edge already exists,
  the confidence is updated to max(existing, new) and the evidence_ids
  list is extended.  This means a person who co-founded two companies
  gets two separate FOUNDED edges — one per company.

Event → graph mapping:
  COMPANY_LISTED        → Company node
  FOUNDER_DETECTED      → Person node; Person–FOUNDED–Company edge if company known
  REPO_CREATED          → Repository node; corroborated Company–MENTIONED_IN edge
  COMMIT_VELOCITY_HIGH  → Repository node properties updated (commits_last_7d)
  JOB_POSTED            → JobPosting node; Company–HIRED–JobPosting edge
  SHOW_HN_POSTED        → Person–MENTIONED_IN–Community(HackerNews) edge
  HN_MENTIONED          → Person–MENTIONED_IN–Community(HackerNews) edge
  PAPER_PUBLISHED       → ResearchPaper node; Person–AUTHORED–ResearchPaper edge
"""

import json
import logging
import re

from venture_intelligence.pipeline.entity_integrity import related_company
from venture_intelligence.programs import ProgramRegistry, get_program_registry
from venture_intelligence.shared.models import (
    CanonicalEvent,
    EventType,
    GraphEdge,
    GraphEdgeType,
    GraphNode,
    GraphNodeType,
)
from venture_intelligence.storage.db import Database

logger = logging.getLogger(__name__)

# Stable node ID for the HackerNews community node — shared across all HN events
_HN_COMMUNITY_NODE_ID = "community__hacker_news"


class GraphBuilder:
    """
    Processes validated events into graph nodes and edges.

    Called by the pipeline after entity resolution so all node IDs
    are canonical (not source-specific).
    """

    def __init__(self, db: Database, registry: ProgramRegistry | None = None) -> None:
        self._db = db
        self._registry = registry or get_program_registry()

    def process(self, event: CanonicalEvent, canonical_id: str) -> None:
        """
        Main entry point — dispatch to the right handler by event type.

        canonical_id is the resolved canonical entity ID (person_id or
        company_id) from the entity resolution step.
        """
        dispatch = {
            EventType.COMPANY_LISTED: self._on_company_listed,
            EventType.COMPANY_WEBSITE_FOUND: self._on_company_website_found,
            EventType.FUNDING_ANNOUNCED: self._on_funding_announced,
            EventType.FOUNDER_DETECTED: self._on_founder_detected,
            EventType.YC_BATCH_ADDED: self._on_yc_batch_added,
            EventType.PROGRAM_MEMBERSHIP_ADDED: self._on_yc_batch_added,
            EventType.REPO_CREATED: self._on_repo_created,
            EventType.COMMIT_VELOCITY_HIGH: self._on_commit_velocity,
            EventType.JOB_POSTED: self._on_job_posted,
            EventType.HIRING_VELOCITY_HIGH: self._on_hiring_velocity,
            EventType.SHOW_HN_POSTED: self._on_hn_event,
            EventType.HN_MENTIONED: self._on_hn_event,
            EventType.PAPER_PUBLISHED: self._on_paper_published,
            EventType.PRODUCT_LAUNCHED: self._on_product_launched,
        }
        handler = dispatch.get(event.event_type)
        if handler:
            try:
                handler(event, canonical_id)
                logger.debug(
                    "graph updated: event_type=%s canonical_id=%s",
                    event.event_type,
                    canonical_id,
                )
            except Exception as exc:
                logger.error("graph builder error event_id=%s: %s", event.event_id, exc)

    # ═══════════════════════════════════════════════════════════════════════════
    # Event handlers
    # ═══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _edge_temporal_fields(event: CanonicalEvent) -> dict[str, object]:
        """Map event time semantics to an edge without inventing validity."""
        fields: dict[str, object] = {"observed_at": event.observed_at}
        if event.validity is not None:
            fields.update(
                valid_from=event.validity.valid_from,
                valid_to=event.validity.valid_to,
                validity_precision=event.validity.precision,
                temporal_status=event.validity.status,
            )
        return fields

    def _on_company_website_found(self, event: CanonicalEvent, canonical_id: str) -> None:
        """Add public website provenance to the canonical company node."""
        payload = event.raw_payload
        self._upsert_node(
            GraphNode(
                node_id=canonical_id,
                node_type=GraphNodeType.COMPANY,
                label=payload.get("name", canonical_id),
                properties={
                    "website": event.source_url,
                    "evidence_tier": "environmental",
                    "program_id": event.metadata.get("program_id"),
                },
            )
        )

    def _on_funding_announced(self, event: CanonicalEvent, canonical_id: str) -> None:
        """Project evidence-backed investor relationships into the temporal graph."""
        payload = event.raw_payload
        self._upsert_node(
            GraphNode(
                node_id=canonical_id,
                node_type=GraphNodeType.COMPANY,
                label=payload.get("name", canonical_id),
                properties={
                    "funding_amount": payload.get("amount"),
                    "funding_round": payload.get("round"),
                    "funding_source_url": event.source_url,
                    "investment_attention_pct": payload.get("investment_attention_pct", 0),
                },
            )
        )
        lead = str(payload.get("lead_investor") or "").strip()
        relationships = payload.get("investor_relationships", {})
        partners = payload.get("investor_partners", {})
        for investor in payload.get("investors", []):
            investor_name = str(investor).strip()
            if not investor_name:
                continue
            partner_name = str(partners.get(investor_name) or "").strip()
            investor_id = ("person__" if partner_name else "organization__") + re.sub(
                r"[^a-z0-9]+", "-", investor_name.casefold()
            ).strip("-")
            self._upsert_node(
                GraphNode(
                    node_id=investor_id,
                    node_type=(
                        GraphNodeType.PERSON if partner_name else GraphNodeType.ORGANIZATION
                    ),
                    label=investor_name,
                    properties={"source_url": event.source_url, "role": "investor"},
                )
            )
            self._upsert_edge(
                GraphEdge(
                    source_id=investor_id,
                    target_id=canonical_id,
                    edge_type=(
                        GraphEdgeType.LED_INVESTMENT_IN
                        if relationships.get(investor_name) in {"led", "co_led"}
                        or investor_name == lead
                        else GraphEdgeType.INVESTED_IN
                    ),
                    confidence=event.confidence,
                    evidence_ids=[event.event_id],
                    properties={
                        "amount": payload.get("amount"),
                        "round": payload.get("round"),
                        "investor_relationship": relationships.get(investor_name, "invested"),
                    },
                    **self._edge_temporal_fields(event),
                )
            )

    def _on_company_listed(self, event: CanonicalEvent, canonical_id: str) -> None:
        """
        Company node + ACCEPTED_BY edge to the configured program node.

        Properties include name, description, batch, and the source URL
        so partners can click through directly from any graph viewer.
        """
        payload = event.raw_payload
        batch = self._event_batch(event)
        program = self._program_for_event(event)
        self._upsert_node(
            GraphNode(
                node_id=canonical_id,
                node_type=GraphNodeType.COMPANY,
                label=payload.get("name", canonical_id),
                properties={
                    "description": payload.get("description"),
                    "batch": batch,
                    "program_source_url": event.source_url,
                    "slug": payload.get("slug"),
                    "tags": payload.get("tags", []),
                    "program_id": program.id if program else None,
                },
            )
        )

        # Company –[ACCEPTED_BY]→ configured program.
        program_node_id = self._ensure_program_node(event)
        if program_node_id is not None:
            self._upsert_edge(
                GraphEdge(
                    source_id=canonical_id,
                    target_id=program_node_id,
                    edge_type=GraphEdgeType.ACCEPTED_BY,
                    confidence=event.confidence,
                    evidence_ids=[event.event_id],
                    properties={"batch": batch},
                    **self._edge_temporal_fields(event),
                )
            )

    def _on_founder_detected(self, event: CanonicalEvent, canonical_id: str) -> None:
        """
        Person node + optional FOUNDED edge to the company (if resolvable).

        The company is identified via the headline's company_name field.
        If the company already exists in the graph, we link them immediately.
        If not, the edge will be created when the company's COMPANY_LISTED
        event is processed (the edge builder checks the reverse direction).
        """
        payload = event.raw_payload
        batch = self._event_batch(event)
        program = self._program_for_event(event)
        self._upsert_node(
            GraphNode(
                node_id=canonical_id,
                node_type=GraphNodeType.PERSON,
                label=payload.get("name", canonical_id),
                properties={
                    "headline": payload.get("headline"),
                    "linkedin_url": payload.get("profile_url"),
                    "github_username": payload.get("github_username"),
                    "batch": batch,
                    "program_id": program.id if program else None,
                },
            )
        )

        # Attempt to resolve company and draw FOUNDED edge
        company_name = payload.get("company_name")
        if company_name:
            source_id = payload.get("company_source_id")
            company_id = (
                self._db.resolve_entity_id(source_id)
                if source_id
                else self._find_company_node_by_name(company_name)
            )
            if company_id:
                self._upsert_edge(
                    GraphEdge(
                        source_id=canonical_id,
                        target_id=company_id,
                        edge_type=GraphEdgeType.FOUNDED,
                        confidence=event.confidence,
                        evidence_ids=[event.event_id],
                        properties={"batch": batch},
                        **self._edge_temporal_fields(event),
                    )
                )
                company = self._db.get_company(company_id)
                if company and canonical_id not in company.founder_ids:
                    company.founder_ids.append(canonical_id)
                    self._db.write_company(company)

    def _on_yc_batch_added(self, event: CanonicalEvent, canonical_id: str) -> None:
        """Compatibility event name for a person signalling program membership."""
        batch = self._event_batch(event)
        program_node_id = self._ensure_program_node(event)
        self._upsert_node(
            GraphNode(
                node_id=canonical_id,
                node_type=GraphNodeType.PERSON,
                label=event.raw_payload.get("name", canonical_id),
                properties={
                    "batch": batch,
                    "program_id": (
                        program.id if (program := self._program_for_event(event)) else None
                    ),
                },
            )
        )
        if program_node_id is None:
            return
        self._upsert_edge(
            GraphEdge(
                source_id=canonical_id,
                target_id=program_node_id,
                edge_type=GraphEdgeType.ACCEPTED_BY,
                confidence=event.confidence,
                evidence_ids=[event.event_id],
                **self._edge_temporal_fields(event),
            )
        )

    def _on_repo_created(self, event: CanonicalEvent, canonical_id: str) -> None:
        """
        Repository node plus a website-corroborated company association.

        canonical_id here is the repo's source entity_id (e.g.
        "github__owner__repo") since Repository entities are not resolved
        to a separate canonical UUID. Username equality never creates an
        ownership/person edge; the shared official-domain guard is required.
        """
        payload = event.raw_payload
        owner = payload.get("owner", "")

        self._upsert_node(
            GraphNode(
                node_id=canonical_id,
                node_type=GraphNodeType.REPOSITORY,
                label=f"{owner}/{payload.get('repo', '')}",
                properties={
                    "stars": payload.get("stars", 0),
                    "forks": payload.get("forks", 0),
                    "language": payload.get("language"),
                    "topics": payload.get("topics", []),
                    "created_at": payload.get("created_at"),
                    "pushed_at": payload.get("pushed_at"),
                },
            )
        )

        # A shared username is not authorship or identity proof. Only a
        # company-domain cross-reference produces an association, not ownership.
        related_id = related_company(event, self._db)
        if related_id:
            self._upsert_edge(
                GraphEdge(
                    source_id=related_id,
                    target_id=canonical_id,
                    edge_type=GraphEdgeType.MENTIONED_IN,
                    confidence=event.confidence,
                    evidence_ids=[event.event_id],
                    properties={
                        "language": payload.get("language"),
                        "association_basis": event.metadata.get("association_basis"),
                    },
                    **self._edge_temporal_fields(event),
                )
            )

    def _on_commit_velocity(self, event: CanonicalEvent, canonical_id: str) -> None:
        """
        Update Repository node with commit velocity data.

        Does not create a new node — just enriches existing properties.
        The signal worker reads these properties when computing
        technical_depth and product_momentum scores.
        """
        payload = event.raw_payload
        # Fetch the existing node and update its properties
        with self._db._connect() as conn:
            row = conn.execute(
                "SELECT properties FROM graph_nodes WHERE node_id=?",
                (canonical_id,),
            ).fetchone()
            if row:
                props = json.loads(row["properties"])
                props["commits_last_7d"] = payload.get("commits_last_7d", 0)
                props["velocity_updated_at"] = event.observed_at.isoformat()
                conn.execute(
                    "UPDATE graph_nodes SET properties=?, updated_at=? WHERE node_id=?",
                    (json.dumps(props), event.observed_at.isoformat(), canonical_id),
                )

    def _on_job_posted(self, event: CanonicalEvent, canonical_id: str) -> None:
        """
        JobPosting node + Company–HIRED–JobPosting edge.

        The company node must already exist (the company would have been
        discovered before it starts hiring).  If not, the edge is skipped
        and will be created on a future event.
        """
        payload = event.raw_payload
        job_id = f"job__{event.entity_id}"
        self._upsert_node(
            GraphNode(
                node_id=job_id,
                node_type=GraphNodeType.JOB_POSTING,
                label=payload.get("title", "Engineer"),
                properties={
                    "company": payload.get("company"),
                    "source_url": event.source_url,
                    "posted_at": event.occurred_at.isoformat(),
                },
            )
        )
        self._upsert_edge(
            GraphEdge(
                source_id=canonical_id,
                target_id=job_id,
                edge_type=GraphEdgeType.HIRED,
                confidence=event.confidence,
                evidence_ids=[event.event_id],
                **self._edge_temporal_fields(event),
            )
        )

    def _on_hiring_velocity(self, event: CanonicalEvent, canonical_id: str) -> None:
        """Update company node's hiring_velocity property."""
        with self._db._connect() as conn:
            row = conn.execute(
                "SELECT properties FROM graph_nodes WHERE node_id=?",
                (canonical_id,),
            ).fetchone()
            if row:
                props = json.loads(row["properties"])
                props["open_roles_30d"] = event.raw_payload.get("open_roles", 0)
                conn.execute(
                    "UPDATE graph_nodes SET properties=? WHERE node_id=?",
                    (json.dumps(props), canonical_id),
                )

    def _on_hn_event(self, event: CanonicalEvent, canonical_id: str) -> None:
        """
        Person–MENTIONED_IN–HackerNews community node.

        The strength of this signal depends on score (upvotes): a Show HN
        with 200 points is far more significant than a comment with 3 points.
        Score is stored in the edge properties for the signal worker to read.
        """
        self._upsert_node(
            GraphNode(
                node_id=_HN_COMMUNITY_NODE_ID,
                node_type=GraphNodeType.COMMUNITY,
                label="Hacker News",
                properties={"website": "https://news.ycombinator.com"},
            )
        )
        self._upsert_edge(
            GraphEdge(
                source_id=canonical_id,
                target_id=_HN_COMMUNITY_NODE_ID,
                edge_type=GraphEdgeType.MENTIONED_IN,
                confidence=event.confidence,
                evidence_ids=[event.event_id],
                properties={
                    "hn_item_id": event.raw_payload.get("hn_item_id"),
                    "title": event.raw_payload.get("title"),
                    "score": event.raw_payload.get("score", 0),
                    "event_type": event.event_type.value,
                },
                **self._edge_temporal_fields(event),
            )
        )

    def _on_paper_published(self, event: CanonicalEvent, canonical_id: str) -> None:
        """ResearchPaper node + Person–AUTHORED–ResearchPaper edge."""
        payload = event.raw_payload
        paper_id = f"paper__{event.entity_id}"
        self._upsert_node(
            GraphNode(
                node_id=paper_id,
                node_type=GraphNodeType.RESEARCH_PAPER,
                label=payload.get("title", "Untitled Paper"),
                properties={
                    "venue": payload.get("venue"),
                    "arxiv_id": payload.get("arxiv_id"),
                    "citations": payload.get("citations", 0),
                    "year": payload.get("year"),
                },
            )
        )
        self._upsert_edge(
            GraphEdge(
                source_id=canonical_id,
                target_id=paper_id,
                edge_type=GraphEdgeType.AUTHORED,
                confidence=event.confidence,
                evidence_ids=[event.event_id],
                **self._edge_temporal_fields(event),
            )
        )

    def _on_product_launched(self, event: CanonicalEvent, canonical_id: str) -> None:
        """Update company node with launch metadata."""
        with self._db._connect() as conn:
            row = conn.execute(
                "SELECT properties FROM graph_nodes WHERE node_id=?",
                (canonical_id,),
            ).fetchone()
            if row:
                props = json.loads(row["properties"])
                props["launched_at"] = event.occurred_at.isoformat()
                props["launch_url"] = event.source_url
                conn.execute(
                    "UPDATE graph_nodes SET properties=? WHERE node_id=?",
                    (json.dumps(props), canonical_id),
                )

    # ═══════════════════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════════════════

    def _upsert_node(self, node: GraphNode) -> None:
        self._db.write_graph_node(node)

    def _upsert_edge(self, edge: GraphEdge) -> None:
        self._db.write_graph_edge(edge)

    def _find_company_node_by_name(self, name: str) -> str | None:
        """Exact-label match against Company nodes in the graph."""
        with self._db._connect() as conn:
            row = conn.execute(
                "SELECT node_id FROM graph_nodes WHERE node_type=? AND label=?",
                (GraphNodeType.COMPANY.value, name),
            ).fetchone()
        return row["node_id"] if row else None

    def _find_person_by_github(self, github_username: str) -> str | None:
        """Look up a Person node via their GitHub username property."""
        with self._db._connect() as conn:
            rows = conn.execute(
                "SELECT node_id, properties FROM graph_nodes WHERE node_type=?",
                (GraphNodeType.PERSON.value,),
            ).fetchall()
        for row in rows:
            props = json.loads(row["properties"])
            if props.get("github_username") == github_username:
                return row["node_id"]
        return None

    def _event_batch(self, event: CanonicalEvent) -> str | None:
        program = self._program_for_event(event)
        return (
            str(
                event.raw_payload.get("batch")
                or event.metadata.get("batch")
                or (program.batch if program else "")
            )
            or None
        )

    def _ensure_program_node(self, event: CanonicalEvent) -> str | None:
        program = self._program_for_event(event)
        if program is not None:
            node_id = f"accelerator__{program.entity_id_prefix}"
            website = program.directory_url or program.talent_page_url
            self._upsert_node(
                GraphNode(
                    node_id=node_id,
                    node_type=GraphNodeType.ACCELERATOR,
                    label=program.name,
                    properties={
                        "program_id": program.id,
                        "website": website,
                        "batch": program.batch,
                    },
                )
            )
            return node_id
        return None

    def _program_for_event(self, event: CanonicalEvent):
        metadata = event.metadata
        payload = event.raw_payload
        return self._registry.resolve_context(
            program_id=str(metadata.get("program_id") or payload.get("program_id") or "") or None,
            batch=str(
                metadata.get("batch_id") or metadata.get("batch") or payload.get("batch") or ""
            )
            or None,
            entity_id=event.entity_id,
            source_url=event.source_url,
            text=" ".join(
                str(value) for value in (payload.get("name"), payload.get("headline")) if value
            ),
        )
