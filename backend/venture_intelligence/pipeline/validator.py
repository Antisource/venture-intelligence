"""
pipeline/validator.py — Event validation layer (§7 pipeline, step 1).

Every event emitted by a connector passes through the validator before
any downstream worker touches it.  Invalid events are nack'd on the bus
and never reach the entity resolver, graph builder, or signal worker.

Validation rules (in order of application):
  1. Schema validity — Pydantic guarantees this at construction time,
     but we re-check confidence bounds and enum membership here.
  2. Temporal sanity — occurred_at must not be >1 hr in the future
     (clock skew tolerance) or >365 days in the past (stale data guard).
  3. Source URL — must be a valid https:// or http:// URL.
  4. Entity ID format — must be "<source>__<slug>", non-empty slug.
  5. Event-entity type consistency — e.g. COMPANY_LISTED must have
     entity_type=COMPANY; FOUNDER_DETECTED must have PERSON.

The validator does NOT check whether the entity already exists — that is
the entity resolver's job.  Validation is purely structural and temporal.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from venture_intelligence.shared.models import CanonicalEvent, EntityType, EventType

logger = logging.getLogger(__name__)

# Clock skew: accept events up to this far in the future
_MAX_FUTURE_SECONDS = 3600  # 1 hour

# Stale data guard: reject events older than this
_MAX_AGE_DAYS = 365

# Simple URL regex — just checks scheme and non-empty host
_URL_RE = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)

# Entity ID must be "<prefix>__<non-empty-slug>"
_ENTITY_ID_RE = re.compile(r"^[a-z_]+__.+$")

# Which entity types are valid for each event type
_EVENT_ENTITY_MAP: dict[EventType, list[EntityType]] = {
    EventType.COMPANY_LISTED: [EntityType.COMPANY],
    EventType.COMPANY_WEBSITE_FOUND: [EntityType.COMPANY],
    EventType.HIRING_PAGE_FOUND: [EntityType.COMPANY],
    EventType.FUNDING_ANNOUNCED: [EntityType.COMPANY],
    EventType.BATCH_ANNOUNCED: [EntityType.COMPANY],
    EventType.DEMO_DAY: [EntityType.COMPANY],
    EventType.FOUNDER_DETECTED: [EntityType.PERSON],
    EventType.TITLE_CHANGED: [EntityType.PERSON],
    EventType.YC_BATCH_ADDED: [EntityType.PERSON],
    EventType.PROGRAM_MEMBERSHIP_ADDED: [EntityType.PERSON],
    EventType.REPO_CREATED: [EntityType.REPOSITORY],
    EventType.COMMIT_VELOCITY_HIGH: [EntityType.REPOSITORY],
    EventType.PAPER_PUBLISHED: [EntityType.PAPER],
    EventType.JOB_POSTED: [EntityType.COMPANY, EntityType.PERSON],
    EventType.HIRING_VELOCITY_HIGH: [EntityType.COMPANY],
    EventType.HN_MENTIONED: [EntityType.PERSON, EntityType.COMPANY],
    EventType.SHOW_HN_POSTED: [EntityType.PERSON, EntityType.COMPANY],
    EventType.PRODUCT_LAUNCHED: [EntityType.COMPANY],
    EventType.RESEARCH_DISCOVERED: [
        EntityType.PERSON,
        EntityType.COMPANY,
        EntityType.REPOSITORY,
        EntityType.PAPER,
    ],
    # CRM events — entity type is flexible (person or company)
    EventType.PARTNER_INTERESTED: [EntityType.PERSON, EntityType.COMPANY],
    EventType.PARTNER_PASSED: [EntityType.PERSON, EntityType.COMPANY],
    EventType.MEETING_SCHEDULED: [EntityType.PERSON, EntityType.COMPANY],
    EventType.PARTNER_INTRODUCED: [EntityType.PERSON, EntityType.COMPANY],
    EventType.INVESTED: [EntityType.COMPANY],
}


class ValidationError(Exception):
    """Raised when an event fails one or more validation rules."""


class EventValidator:
    """
    Stateless validator — all methods are pure functions of the event.

    The validator runs as a pipeline worker:
      events = bus.consume('validator')
      for event in events:
          ok, err = validator.validate(event)
          if ok: bus.ack(event.event_id, 'validator')
          else:  bus.nack(event.event_id, 'validator', err)
    """

    def __init__(
        self,
        *,
        reference_time: datetime | None = None,
        max_age_days: int | None = _MAX_AGE_DAYS,
    ) -> None:
        self._reference_time = reference_time
        self._max_age_days = max_age_days

    def validate(self, event: CanonicalEvent) -> tuple[bool, str | None]:
        """
        Run all validation rules against the event.

        Returns (True, None) if valid, (False, reason) if not.
        Runs all checks so a single event can report multiple failures
        (helps debugging mis-configured connectors).
        """
        errors: list[str] = []

        # Rule 1 — confidence must be in [0.0, 1.0]
        if not 0.0 <= event.confidence <= 1.0:
            errors.append(f"confidence {event.confidence} is outside [0.0, 1.0]")

        # Rule 2 — temporal sanity
        now = self._reference_time or datetime.now(timezone.utc)
        age = now - event.occurred_at
        if event.occurred_at > now + timedelta(seconds=_MAX_FUTURE_SECONDS):
            errors.append(f"occurred_at {event.occurred_at.isoformat()} is in the future")
        if self._max_age_days is not None and age.days > self._max_age_days:
            errors.append(
                f"occurred_at is {age.days} days old — exceeds stale-data limit "
                f"of {self._max_age_days}"
            )

        # Rule 3 — source URL format
        if not _URL_RE.match(event.source_url):
            errors.append(f"source_url {event.source_url!r} is not a valid http(s) URL")

        # Rule 4 — entity ID format
        if not _ENTITY_ID_RE.match(event.entity_id):
            errors.append(f"entity_id {event.entity_id!r} must match '<source>__<slug>'")

        # Rule 5 — event type ↔ entity type consistency
        allowed_entity_types = _EVENT_ENTITY_MAP.get(event.event_type, [])
        if allowed_entity_types and event.entity_type not in allowed_entity_types:
            errors.append(
                f"event_type {event.event_type} expects entity_type in "
                f"{[e.value for e in allowed_entity_types]} "
                f"but got {event.entity_type}"
            )

        if errors:
            reason = "; ".join(errors)
            logger.warning("validation failed event_id=%s: %s", event.event_id, reason)
            return False, reason

        logger.debug("validation passed event_id=%s", event.event_id)
        return True, None
