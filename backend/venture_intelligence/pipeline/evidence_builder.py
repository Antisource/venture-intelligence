"""
pipeline/evidence_builder.py — builds the Observation → Evidence chain (§17).

For every validated event, this worker:
  1. Creates one or more Observations (atomic facts from the event)
  2. Groups related observations into Evidence (supported claims)

The Evidence records are what the Signal Worker reads — it never looks at
raw events directly.  This indirection means the signal worker is source-
agnostic: it doesn't care whether "Has active GitHub presence" came from
a direct GitHub scan or a cross-connector investigation.

Claim vocabulary is program-generic. Program and batch identity remain in the
source event metadata and feature lineage rather than being embedded in text.
  "Has active GitHub presence"
  "Is building actively"          — high commit velocity
  "Has public community signal"   — HN/Show HN
  "Is actively hiring"
  "Has published research"
  "Has public product launch"
"""

import logging

from venture_intelligence.pipeline.entity_integrity import related_company
from venture_intelligence.programs import ProgramRegistry, get_program_registry
from venture_intelligence.shared.models import (
    CanonicalEvent,
    EventType,
    Evidence,
    Observation,
    Source,
)
from venture_intelligence.storage.db import Database

logger = logging.getLogger(__name__)


class EvidenceBuilder:
    """Converts raw events into structured observations and evidence claims."""

    def __init__(self, db: Database, registry: ProgramRegistry | None = None) -> None:
        self._db = db
        self._registry = registry or get_program_registry()

    def process(self, event: CanonicalEvent, canonical_id: str) -> None:
        """
        Build observations and evidence for one event.

        canonical_id — the resolved entity UUID (from EntityResolver).
        All observations and evidence rows use this ID as their entity_id
        so downstream workers operate on canonical entities, not source slugs.
        """
        obs = self._make_observation(event, canonical_id)
        if obs:
            self._db.write_observation(obs)
            ev = self._make_evidence(event, canonical_id, obs)
            if ev:
                self._db.write_evidence(ev)
                logger.debug("evidence written: entity=%s claim=%r", canonical_id, ev.claim)

        # Technical enrichment may reference an already validated company via
        # its official website, never via a matching username or search term.
        related_source = event.metadata.get("related_entity_source_id")
        if related_source and canonical_id == event.entity_id:
            related = related_company(event, self._db)
            if related:
                self.process(event, related)

    # ── Observation builder ───────────────────────────────────────────────────

    def _make_observation(self, event: CanonicalEvent, canonical_id: str) -> Observation | None:
        """
        Produce a single human-readable Observation from the event payload.

        The `fact` string is written for a human reader — it becomes part
        of the alert's evidence_summary that partners receive.
        """
        p = event.raw_payload

        program = self._program_for_event(event)
        batch = event.metadata.get("batch_id") or event.metadata.get("batch") or p.get("batch")
        association = (
            f"{program.name} {batch or program.batch}" if program else "an accelerator/program"
        )
        facts: dict[EventType, str] = {
            EventType.COMPANY_LISTED: (
                f"Company '{p.get('name', canonical_id)}' "
                f"listed for {association} via {event.source.value}"
            ),
            EventType.COMPANY_WEBSITE_FOUND: (
                f"Public website observed for '{p.get('name', canonical_id)}': {event.source_url}"
            ),
            EventType.FUNDING_ANNOUNCED: (
                f"Funding announced for '{p.get('name', canonical_id)}': "
                f"{p.get('amount') or 'amount undisclosed'} "
                f"{p.get('round') or 'round unspecified'}"
            ),
            EventType.FOUNDER_DETECTED: (
                f"'{p.get('name', canonical_id)}' detected as a founder "
                f"associated with {association} "
                f"(headline: {p.get('headline', 'unknown')!r})"
            ),
            EventType.YC_BATCH_ADDED: (
                f"'{p.get('name', canonical_id)}' added {association} to a public profile"
            ),
            EventType.PROGRAM_MEMBERSHIP_ADDED: (
                f"'{p.get('name', canonical_id)}' added {association} to a public profile"
            ),
            EventType.REPO_CREATED: (
                f"GitHub repo {p.get('owner', '')}/{p.get('repo', '')} created "
                f"— {p.get('stars', 0)} stars, language: {p.get('language', 'unknown')}"
            ),
            EventType.COMMIT_VELOCITY_HIGH: (
                f"{p.get('commits_last_7d', 0)} commits in last 7 days "
                f"on {p.get('owner', '')}/{p.get('repo', '')}"
            ),
            EventType.JOB_POSTED: (
                f"Job posting detected: {p.get('title', 'Engineer')} "
                f"at {p.get('company', canonical_id)}"
            ),
            EventType.HIRING_VELOCITY_HIGH: (
                f"{p.get('open_roles', 0)} open roles in last 30 days"
            ),
            EventType.SHOW_HN_POSTED: (
                f"Show HN: {p.get('title', '')!r} — score {p.get('score', 0)}"
            ),
            EventType.HN_MENTIONED: (
                f"Mentioned in HN: {p.get('title', '')!r} — score {p.get('score', 0)}"
            ),
            EventType.PAPER_PUBLISHED: (
                f"Paper published: {p.get('title', 'Untitled')} ({p.get('venue', 'unknown venue')})"
            ),
            EventType.PRODUCT_LAUNCHED: (f"Product launched publicly: {event.source_url}"),
            EventType.SOCIAL_MENTION: (
                "Public social mention observed for "
                f"'{p.get('name', canonical_id)}': {event.source_url}"
            ),
            EventType.NEWSLETTER_MENTION: (
                "Public newsletter mention observed for "
                f"'{p.get('name', canonical_id)}': {event.source_url}"
            ),
            EventType.BLOG_MENTION: (
                "Public blog mention observed for "
                f"'{p.get('name', canonical_id)}': {event.source_url}"
            ),
            EventType.COMMUNITY_MEMBERSHIP: (
                "Public community membership signal observed for "
                f"'{p.get('name', canonical_id)}': {event.source_url}"
            ),
            EventType.RESEARCH_DISCOVERED: (
                "Bounded public research evidence discovered for "
                f"'{p.get('name', canonical_id)}': {event.source_url}"
            ),
        }

        fact = facts.get(event.event_type)
        if not fact:
            return None  # CRM events and others don't need evidence observations

        return Observation(
            event_id=event.event_id,
            entity_id=canonical_id,
            source=event.source,
            fact=fact,
            value=_extract_numeric_value(event),
            observed_at=event.observed_at,
            confidence=event.confidence,
        )

    # ── Evidence builder ──────────────────────────────────────────────────────

    def _make_evidence(
        self,
        event: CanonicalEvent,
        canonical_id: str,
        obs: Observation,
    ) -> Evidence | None:
        """
        Map an event type to its corresponding Evidence claim.

        The claim vocabulary is fixed — the Signal Worker pattern-matches
        on these exact strings to compute signal values.
        """
        claim_map: dict[EventType, str] = {
            # Program confirmation (highest confidence claim).
            EventType.COMPANY_LISTED: (
                "Has confirmed program membership"
                if event.source in {Source.YC_WEBSITE, Source.DIRECTORY}
                else "Has supported program association"
            ),
            EventType.COMPANY_WEBSITE_FOUND: "Has a public company website",
            EventType.FUNDING_ANNOUNCED: "Has public funding evidence",
            EventType.FOUNDER_DETECTED: "Has supported founder program association",
            EventType.YC_BATCH_ADDED: "Has confirmed founder program membership",
            EventType.PROGRAM_MEMBERSHIP_ADDED: "Has confirmed founder program membership",
            # Technical signals
            EventType.REPO_CREATED: "Has active GitHub presence",
            EventType.COMMIT_VELOCITY_HIGH: "Is building actively",
            # Hiring signals
            EventType.JOB_POSTED: "Is actively hiring",
            EventType.HIRING_VELOCITY_HIGH: "Has high hiring velocity",
            # Community signals
            EventType.SHOW_HN_POSTED: "Has public product launch",
            EventType.HN_MENTIONED: "Has public community signal",
            EventType.PRODUCT_LAUNCHED: "Has public product launch",
            EventType.COMPANY_WEBSITE_FOUND: "Has a public company website",
            EventType.SOCIAL_MENTION: "Has public environmental signal",
            EventType.NEWSLETTER_MENTION: "Has public environmental signal",
            EventType.BLOG_MENTION: "Has public environmental signal",
            EventType.COMMUNITY_MEMBERSHIP: "Has public environmental signal",
            EventType.RESEARCH_DISCOVERED: "Has public research evidence",
            # Research signals
            EventType.PAPER_PUBLISHED: "Has published research",
        }

        claim = claim_map.get(event.event_type)
        if not claim:
            return None

        return Evidence(
            entity_id=canonical_id,
            claim=claim,
            observation_ids=[obs.observation_id],
            confidence=event.confidence,
            observed_at=event.observed_at,
        )

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


# ── Module helper ─────────────────────────────────────────────────────────────


def _extract_numeric_value(event: CanonicalEvent):
    """
    Pull the most meaningful numeric value from an event's payload.
    Stored in Observation.value for the Signal Worker to use directly
    without re-parsing raw_payload strings.
    """
    p = event.raw_payload
    if event.event_type == EventType.COMMIT_VELOCITY_HIGH:
        return p.get("commits_last_7d", 0)
    if event.event_type == EventType.REPO_CREATED:
        return p.get("stars", 0)
    if event.event_type in (EventType.SHOW_HN_POSTED, EventType.HN_MENTIONED):
        return p.get("score", 0)
    if event.event_type == EventType.HIRING_VELOCITY_HIGH:
        return p.get("open_roles", 0)
    return None
