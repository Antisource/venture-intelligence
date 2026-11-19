"""Event taxonomy (§13) and canonical event schema (§12), plus legacy
program-specific event identifiers."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from venture_intelligence.shared.models._helpers import _now, _uid, _utc

# Classes and helpers defined here keep the historical public module path.
# OpenAPI component names (e.g. venture_intelligence__shared__models__SourceAccessMode
# on a name collision), pydantic core-schema refs and pickled qualified paths all
# derive from ``__module__``, which Python takes from ``__name__`` at definition time.
__name__ = "venture_intelligence.shared.models"

# ═══════════════════════════════════════════════════════════════════════════════
# §13  Event taxonomy
# ═══════════════════════════════════════════════════════════════════════════════


class EventType(str, Enum):
    # ── Person events ──────────────────────────────────────────────────────────
    FOUNDER_DETECTED = "founder_detected"  # new person seen for first time
    TITLE_CHANGED = "title_changed"  # LinkedIn headline updated
    PROGRAM_MEMBERSHIP_ADDED = "program_membership_added"
    YC_BATCH_ADDED = "yc_batch_added"  # persisted compatibility event

    # ── Company events ─────────────────────────────────────────────────────────
    COMPANY_LISTED = "company_listed"  # appears on YC directory
    COMPANY_WEBSITE_FOUND = "company_website_found"  # domain discovered
    HIRING_PAGE_FOUND = "hiring_page_found"  # /jobs page detected
    FUNDING_ANNOUNCED = "funding_announced"

    # ── Accelerator events ─────────────────────────────────────────────────────
    BATCH_ANNOUNCED = "batch_announced"  # YC announces new batch
    DEMO_DAY = "demo_day"  # demo day detected

    # ── Repository events ──────────────────────────────────────────────────────
    REPO_CREATED = "repo_created"  # new GitHub repo
    COMMIT_VELOCITY_HIGH = "commit_velocity_high"  # >N commits in last 7 days

    # ── Research events ────────────────────────────────────────────────────────
    PAPER_PUBLISHED = "paper_published"  # arXiv or SSRN paper

    # ── Hiring events ──────────────────────────────────────────────────────────
    JOB_POSTED = "job_posted"  # new job listing
    HIRING_VELOCITY_HIGH = "hiring_velocity_high"  # >N open roles in 30 days

    # ── Community events ───────────────────────────────────────────────────────
    HN_MENTIONED = "hn_mentioned"  # HN comment or story mention
    SHOW_HN_POSTED = "show_hn_posted"  # Show HN launch post
    PRODUCT_LAUNCHED = "product_launched"  # PH or HN launch
    SOCIAL_MENTION = "social_mention"  # public social profile/post
    NEWSLETTER_MENTION = "newsletter_mention"  # public RSS/newsletter item
    BLOG_MENTION = "blog_mention"  # public blog post
    COMMUNITY_MEMBERSHIP = "community_membership"  # public community widget signal
    RESEARCH_DISCOVERED = "research_discovered"  # bounded public research result

    # ── CRM events (§20: partner actions become first-class events) ────────────
    PARTNER_INTERESTED = "partner_interested"
    PARTNER_PASSED = "partner_passed"
    MEETING_SCHEDULED = "meeting_scheduled"
    PARTNER_INTRODUCED = "partner_introduced"
    INVESTED = "invested"


class EntityType(str, Enum):
    PERSON = "person"
    COMPANY = "company"
    REPOSITORY = "repository"
    PAPER = "paper"


class Source(str, Enum):
    YC_WEBSITE = "yc_website"
    LINKEDIN = "linkedin"
    GITHUB = "github"
    HACKER_NEWS = "hacker_news"
    ARXIV = "arxiv"
    PRODUCT_HUNT = "product_hunt"
    WELLFOUND = "wellfound"
    DIRECTORY = "directory"
    TRIANGULATION = "triangulation"
    NEWSLETTER = "newsletter"
    BLOG = "blog"
    WEBSITE = "website"
    SOCIAL = "social"
    DISCORD = "discord"
    FIRECRAWL = "firecrawl"
    RESEARCH_AGENT = "research_agent"
    API = "api"
    CRM = "crm"  # partner-generated events


# ═══════════════════════════════════════════════════════════════════════════════
# §12  Canonical event schema
# ═══════════════════════════════════════════════════════════════════════════════


class TemporalPrecision(str, Enum):
    """How precisely a real-world timestamp or validity bound is known."""

    EXACT = "exact"
    DAY = "day"
    MONTH = "month"
    APPROXIMATE = "approximate"
    OBSERVED_FALLBACK = "observed_fallback"
    UNKNOWN = "unknown"


class TemporalStatus(str, Enum):
    """Whether a claim or relationship is current at collection time."""

    CURRENT = "current"
    HISTORICAL = "historical"
    STALE = "stale"
    CONTRADICTED = "contradicted"
    UNKNOWN = "unknown"


class TemporalValidity(BaseModel):
    """Explicit world-time bounds for a relationship asserted by evidence."""

    valid_from: datetime | None = None
    valid_to: datetime | None = None
    precision: TemporalPrecision = TemporalPrecision.UNKNOWN
    status: TemporalStatus = TemporalStatus.UNKNOWN

    @field_validator("valid_from", "valid_to")
    @classmethod
    def validate_aware_timestamp(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_interval(self) -> "TemporalValidity":
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to < self.valid_from
        ):
            raise ValueError("valid_to must not precede valid_from")
        if (
            self.valid_from is None
            and self.valid_to is None
            and self.precision != TemporalPrecision.UNKNOWN
        ):
            raise ValueError("temporal precision requires at least one validity bound")
        return self


class CanonicalEvent(BaseModel):
    """
    The immutable unit of information in the system (§6.1 Event First).

    Once written to the database an event is never modified.  All downstream
    state is derived from events; no state is mutated in place.

    Six timestamps capture the full lifecycle (§6.5 Temporal by Default):
      occurred_at  — when the thing happened in the external world
      observed_at  — when MFC's connector first noticed it
      ingested_at  — when it was written to the database
      processed_at — when the pipeline workers finished with it
      ranked_at    — when the ranking engine updated scores for its entity
      notified_at  — when a partner alert was sent (None until then)
    """

    schema_version: str = "1.1"
    event_id: str = Field(default_factory=_uid)
    event_type: EventType
    entity_type: EntityType
    # Stable cross-source identifier: "<source>__<slug>"
    # e.g. "yc__stripe" or "github__stripe-inc"
    entity_id: str
    source: Source
    source_url: str

    occurred_at: datetime  # world time — required, set by connector
    occurred_at_precision: TemporalPrecision = TemporalPrecision.UNKNOWN
    observed_at: datetime = Field(default_factory=_now)
    ingested_at: datetime = Field(default_factory=_now)
    processed_at: datetime | None = None
    ranked_at: datetime | None = None
    notified_at: datetime | None = None
    validity: TemporalValidity | None = None

    # 0.0–1.0 how sure we are this event is real and correctly parsed
    confidence: float = 0.90
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "occurred_at",
        "observed_at",
        "ingested_at",
        "processed_at",
        "ranked_at",
        "notified_at",
    )
    @classmethod
    def validate_event_timestamp(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_lifecycle_order(self) -> "CanonicalEvent":
        if self.ingested_at < self.observed_at:
            raise ValueError("ingested_at must not precede observed_at")
        return self


# ═══════════════════════════════════════════════════════════════════════════════
# a16z Speedrun — additional event and signal types
# ═══════════════════════════════════════════════════════════════════════════════
# These extend the existing taxonomy; no existing code changes.

# Added to EventType enum — done as module-level patches so the enum stays intact
# Real usage: import SpeedrunEventType and treat identically to EventType


class SpeedrunEventType(str):
    """
    Speedrun-specific event type strings.  Written as plain strings (not extending
    the EventType Enum) so the event bus and DB accept them without schema changes.
    Stored in events.event_type alongside regular EventType values.
    """

    SPEEDRUN_LISTED = "speedrun_listed"  # company confirmed in SR batch
    SPEEDRUN_BATCH_ADDED = "speedrun_batch_added"  # founder added SR batch to profile


class AcceleratorProgram(str):
    """Deprecated persisted identifiers; ProgramRegistry owns current ids."""

    YC_F26 = "yc_f26"
    SPEEDRUN_SR007 = "speedrun_sr007"
