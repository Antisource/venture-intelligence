"""
pipeline/entity_resolution.py — Entity Resolution Engine (§14).

Problem: the same real-world company or person arrives under different
identifiers from different sources.

  YC website   → entity_id "yc__stripe"
  LinkedIn     → entity_id "linkedin_co__stripe-inc"
  GitHub       → entity_id "github__stripe__stripe-ios"

All three refer to the same company.  The resolver maps each source-
specific entity_id to a single canonical entity_id (either a Company or
Person UUID in the database).

Resolution strategy (applied in priority order):

  For COMPANIES:
    1. URL match      — same yc_url or website in DB → exact match
    2. Slug match     — same slug (e.g. "stripe") across sources
    3. Fuzzy name     — SequenceMatcher similarity >= threshold (default 0.82)
    4. Create new     — no match found → new Company written to DB

  For PERSONS:
    1. Attributed public profile AND exact normalized name
    2. Create new — names and usernames alone have no merge authority

After resolution, the entity_link (source_id → canonical_id) is written
so future events from the same source skip the matching step entirely.

The resolver is intentionally conservative: it prefers creating a new
entity over incorrectly merging two different entities.  Merges are
surfaced in the CRM as "Possible duplicate" notes for human review.
"""

import logging
from difflib import SequenceMatcher
from typing import Callable
from uuid import UUID

from venture_intelligence.config import settings
from venture_intelligence.pipeline.entity_integrity import (
    GENERIC_ENTITY_LABELS,
    validate_candidate,
)
from venture_intelligence.programs import (
    ProgramConfig,
    ProgramRegistry,
    get_program_registry,
)
from venture_intelligence.shared.models import (
    CanonicalEvent,
    Company,
    EntityType,
    Person,
    Source,
)
from venture_intelligence.storage.db import Database

logger = logging.getLogger(__name__)

_GENERIC_ENTITY_LABELS = GENERIC_ENTITY_LABELS


def _is_generic_entity_label(value: str) -> bool:
    words = {item.casefold() for item in value.split() if item.strip()}
    return bool(words) and words <= _GENERIC_ENTITY_LABELS


def _normalized_identity(value: str) -> str:
    """Conservative exact-identity key; never a fuzzy similarity score."""
    return " ".join(value.casefold().replace("-", " ").split())


def _directory_base_source_id(source_id: str) -> str | None:
    """Return the registry source ID when a directory URL digest was appended."""
    base, separator, suffix = source_id.rpartition("__")
    if not separator:
        return None
    try:
        UUID(suffix)
    except ValueError:
        return None
    return base or None


class EntityResolver:
    """
    Maps source-specific entity IDs to canonical Person or Company records.

    All reads hit the in-memory candidate cache (built once per resolver
    instance).  Use reset_cache() at the start of each pipeline run so
    new entities created during the run are visible to subsequent events.
    """

    def __init__(
        self,
        db: Database,
        *,
        entity_id_factory: Callable[[str, CanonicalEvent], str] | None = None,
        registry: ProgramRegistry | None = None,
    ) -> None:
        self._db = db
        self._entity_id_factory = entity_id_factory
        self._registry = registry or get_program_registry()
        self._company_cache: list[Company] = []
        self._person_cache: list[Person] = []
        self._cache_valid = False

    def resolve(self, event: CanonicalEvent) -> str:
        """
        Return the canonical entity ID for this event's entity.

        Side effects:
          • May create a new Person or Company in the database.
          • Always writes an entity_link row mapping source_id → canonical_id.

        The returned ID is a stable UUID (person_id or company_id) that
        downstream workers (graph builder, signal worker, etc.) use as
        their primary key for all entity-level operations.
        """
        validation = validate_candidate(event, self._db)
        if not validation.accepted:
            if validation.reason == "generic_label":
                kind = "person" if event.entity_type == EntityType.PERSON else "company"
                raise ValueError(f"generic {kind} label rejected: {validation.label}")
            raise ValueError(f"candidate rejected before resolution: {validation.reason}")

        # Fast path: entity_id already linked in a prior run.  Structured
        # directory company records are the exception: older releases linked
        # every item from a collection endpoint to the first company.  Recheck
        # the explicit company name before trusting such a link so a replay or
        # reconciliation can self-heal without merging by name or URL alone.
        existing = self._db.resolve_entity_id(event.entity_id)
        if (
            existing
            and event.entity_type == EntityType.COMPANY
            and event.source == Source.DIRECTORY
        ):
            company = self._db.get_company(existing)
            event_name = str(event.raw_payload.get("name") or "")
            linked_name = _normalized_identity(company.canonical_name) if company else None
            if linked_name != _normalized_identity(event_name):
                logger.warning(
                    "ignoring stale directory source link source_id=%s canonical_id=%s",
                    event.entity_id,
                    existing,
                )
                existing = None
        if existing:
            logger.debug("entity_id=%s resolved via link → %s", event.entity_id, existing)
            self._mark_validated(existing, event)
            return existing

        self._warm_cache()

        if event.entity_type == EntityType.COMPANY:
            canonical_id = self._resolve_company(event)
        elif event.entity_type == EntityType.PERSON:
            canonical_id = self._resolve_person(event)
        else:
            # Repository and Paper entities use the source entity_id directly
            canonical_id = event.entity_id

        # Record the link so future events skip matching
        self._db.write_entity_link(
            canonical_id=canonical_id,
            entity_kind=event.entity_type.value,
            source=event.source.value,
            source_id=event.entity_id,
            confidence=event.confidence,
        )
        self._mark_validated(canonical_id, event)
        return canonical_id

    def reset_cache(self) -> None:
        """Invalidate the in-memory candidate cache.  Call once per pipeline cycle."""
        self._cache_valid = False

    # ── Private ───────────────────────────────────────────────────────────────

    def _warm_cache(self) -> None:
        """Load all known persons and companies into memory for fuzzy matching."""
        if self._cache_valid:
            return
        self._company_cache = self._db.all_companies()
        self._person_cache = self._db.all_persons()
        self._cache_valid = True

    def _resolve_company(self, event: CanonicalEvent) -> str:
        """
        Find or create a canonical Company for this event.

        Matching priority (§14):
          1. yc_url exact match  — strongest signal
          2. slug exact match    — reliable for YC-sourced slugs
          3. fuzzy name match    — catches "Stripe Inc" vs "Stripe"
          4. create new          — conservative fallback
        """
        payload = event.raw_payload
        name = payload.get("name", "")
        if isinstance(name, str) and _is_generic_entity_label(name):
            raise ValueError(f"generic company label rejected: {name!r}")
        slug = payload.get("slug", "")
        yc_url = event.source_url if event.source == Source.YC_WEBSITE else None

        if event.source == Source.DIRECTORY:
            # A detail URL plus exact name is authoritative. Collection APIs use
            # one acquisition URL for many companies, so URL equality alone must
            # never collapse the collection into its first company.
            normalized_name = _normalized_identity(name)
            for company in self._company_cache:
                if (
                    company.properties.get("program_source_url") == event.source_url
                    and _normalized_identity(company.canonical_name) == normalized_name
                ):
                    return self._update_company(company, event)
            base_source_id = _directory_base_source_id(event.entity_id)
            base_canonical_id = (
                self._db.resolve_entity_id(base_source_id) if base_source_id else None
            )
            base_company = (
                self._db.get_company(base_canonical_id) if base_canonical_id else None
            )
            program = self._program_for_event(event)
            event_batch = self._batch_for_event(event, program)
            if (
                base_company is not None
                and _normalized_identity(base_company.canonical_name) == normalized_name
                and base_company.properties.get("program_id")
                == (program.id if program else None)
                and base_company.batch == event_batch
            ):
                return self._update_company(base_company, event)
            return self._create_company(event)

        # Priority 1 — YC URL exact match
        if yc_url:
            for company in self._company_cache:
                if company.yc_url == yc_url:
                    logger.debug("company resolved via YC URL: %s", company.company_id)
                    return self._update_company(company, event)

        # Priority 2 — slug exact match (slugs are reliably stable on YC)
        if slug:
            for company in self._company_cache:
                if company.slug and company.slug.lower() == slug.lower():
                    logger.debug("company resolved via slug=%s: %s", slug, company.company_id)
                    return self._update_company(company, event)

        # M8 hypotheses have already been conservatively clustered.  Preserve that
        # identity boundary here: exact normalized names may converge, but a fuzzy
        # match must never merge two distinct discovered companies.
        if name and event.metadata.get("candidate_hypothesis_id"):
            normalized_name = _normalized_identity(name)
            exact = next(
                (
                    company
                    for company in self._company_cache
                    if _normalized_identity(company.canonical_name) == normalized_name
                ),
                None,
            )
            if exact is not None:
                return self._update_company(exact, event)

        # Priority 3 — fuzzy name match for established non-hypothesis flows
        if name and not event.metadata.get("candidate_hypothesis_id"):
            match, score = _best_fuzzy_match(
                name,
                [(c.canonical_name, c) for c in self._company_cache],
            )
            if match and score >= settings.name_match_threshold:
                logger.info(
                    "company resolved via name fuzzy match: %r ≈ %r (%.2f)",
                    name,
                    match.canonical_name,
                    score,
                )
                return self._update_company(match, event)
            elif match and score >= settings.name_match_threshold - 0.10:
                # Near-miss: create new but log for human review
                logger.warning(
                    "near-miss company name: %r vs %r (%.2f) — creating new entity",
                    name,
                    match.canonical_name,
                    score,
                )

        # Priority 4 — create new company
        return self._create_company(event)

    def _resolve_person(self, event: CanonicalEvent) -> str:
        """
        Find or create a canonical Person for this event.

        Merge only an exact attributed profile URL plus exact normalized name.
        A username or similar name alone is not an identity key.
        """
        payload = event.raw_payload
        name = payload.get("name", "")
        if isinstance(name, str) and _is_generic_entity_label(name):
            raise ValueError(f"generic person label rejected: {name!r}")
        li_url = payload.get("profile_url") or payload.get("linkedin_url")
        # A source-attributed public profile plus the exact name may corroborate
        # an identity. Username equality, common names and fuzzy matches may not.
        if li_url:
            for person in self._person_cache:
                if person.linkedin_url == li_url and _normalized_identity(
                    person.canonical_name
                ) == _normalized_identity(name):
                    logger.debug("person resolved via LinkedIn URL")
                    return self._update_person(person, event)

        # Priority 4 — create new person
        return self._create_person(event)

    def _update_company(self, company: Company, event: CanonicalEvent) -> str:
        """
        Enrich an existing Company with new data from this event.

        Adds the event's name to aliases (if new), and updates YC URL,
        description, and slug from sources we trust more than the current value.
        """
        payload = event.raw_payload
        changed = False
        program = self._program_for_event(event)
        event_batch = self._batch_for_event(event, program)

        # Add new name variant as an alias
        new_name = payload.get("name", "")
        if new_name and new_name not in company.aliases and new_name != company.canonical_name:
            company.aliases.append(new_name)
            changed = True

        # Trust YC URL and slug from the YC website connector
        if event.source == Source.YC_WEBSITE:
            if not company.yc_url and event.source_url:
                company.yc_url = event.source_url
                changed = True

        if event.source == Source.DIRECTORY and payload.get("website") and not company.website:
            company.website = payload["website"]
            changed = True

        source_ids = company.properties.setdefault("source_ids", [])
        if event.entity_id not in source_ids:
            source_ids.append(event.entity_id)
            changed = True

        if program is not None:
            if company.batch != event_batch:
                company.batch = event_batch
                changed = True
            if company.properties.get("program_id") != program.id:
                company.properties["program_id"] = program.id
                changed = True
            if not company.slug and payload.get("slug"):
                company.slug = payload["slug"]
                changed = True
            if not company.description and payload.get("description"):
                company.description = payload["description"]
                changed = True

        if changed:
            company.updated_at = event.observed_at
            self._db.write_company(company)
            # Invalidate cache so subsequent events in this run see the update
            self._cache_valid = False

        return company.company_id

    def _update_person(self, person: Person, event: CanonicalEvent) -> str:
        """Enrich an existing Person with new data from this event."""
        payload = event.raw_payload
        changed = False
        program = self._program_for_event(event)
        event_batch = self._batch_for_event(event, program)

        new_name = payload.get("name", "")
        if new_name and new_name not in person.aliases and new_name != person.canonical_name:
            person.aliases.append(new_name)
            changed = True

        if event.source == Source.GITHUB:
            gh_user = payload.get("github_username")
            if gh_user and not person.github_username:
                person.github_username = gh_user
                changed = True

        if event.source == Source.LINKEDIN:
            li_url = payload.get("profile_url")
            if li_url and not person.linkedin_url:
                person.linkedin_url = li_url
                changed = True

        if program is not None:
            if person.batch != event_batch:
                person.batch = event_batch
                changed = True
            if person.properties.get("program_id") != program.id:
                person.properties["program_id"] = program.id
                changed = True
            title = payload.get("headline")
            if title and not person.current_title:
                person.current_title = title
                changed = True

        if changed:
            person.updated_at = event.observed_at
            self._db.write_person(person)
            self._cache_valid = False

        return person.person_id

    def _create_company(self, event: CanonicalEvent) -> str:
        """
        Create a new canonical Company from this event's payload.

        Uses the most specific name available (raw_payload["name"] preferred
        over slug-derived name).
        """
        payload = event.raw_payload
        name = payload.get("name") or _name_from_entity_id(event.entity_id)

        identity = (
            {"company_id": self._entity_id_factory("company", event)}
            if self._entity_id_factory is not None
            else {}
        )
        program = self._program_for_event(event)
        event_batch = self._batch_for_event(event, program)
        company = Company(
            **identity,
            canonical_name=name,
            slug=payload.get("slug"),
            yc_url=event.source_url if event.source == Source.YC_WEBSITE else None,
            website=payload.get("website"),
            description=payload.get("description"),
            batch=event_batch,
            tags=payload.get("tags", []),
            identity_confidence=event.confidence,
            properties={
                "source_ids": [event.entity_id],
                "program_id": program.id if program else None,
                "program_source_url": event.source_url
                if event.source == Source.DIRECTORY
                else None,
            },
            first_seen_at=event.observed_at,
            updated_at=event.observed_at,
        )
        self._db.write_company(company)
        self._company_cache.append(company)
        logger.info("new company created: %r → %s", name, company.company_id)
        return company.company_id

    def _create_person(self, event: CanonicalEvent) -> str:
        """Create a new canonical Person from this event's payload."""
        payload = event.raw_payload
        name = payload.get("name") or _name_from_entity_id(event.entity_id)

        identity = (
            {"person_id": self._entity_id_factory("person", event)}
            if self._entity_id_factory is not None
            else {}
        )
        program = self._program_for_event(event)
        event_batch = self._batch_for_event(event, program)
        person = Person(
            **identity,
            canonical_name=name,
            github_username=payload.get("github_username"),
            linkedin_url=payload.get("profile_url") or payload.get("linkedin_url"),
            current_title=payload.get("headline") or payload.get("current_title"),
            batch=event_batch,
            identity_confidence=event.confidence,
            properties={
                "source_ids": [event.entity_id],
                "program_id": program.id if program else None,
                "founder_role": payload.get("headline"),
                "company_source_id": payload.get("company_source_id"),
            },
            first_seen_at=event.observed_at,
            updated_at=event.observed_at,
        )
        self._db.write_person(person)
        self._person_cache.append(person)
        logger.info("new person created: %r → %s", name, person.person_id)
        return person.person_id

    def _program_for_event(self, event: CanonicalEvent):
        payload = event.raw_payload
        metadata = event.metadata
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

    def _batch_for_event(
        self,
        event: CanonicalEvent,
        program: ProgramConfig | None,
    ) -> str | None:
        """Preserve an explicitly configured historical batch during resolution."""
        if program is None:
            return None
        payload = event.raw_payload
        metadata = event.metadata
        requested = str(
            metadata.get("batch_id") or metadata.get("batch") or payload.get("batch") or ""
        ).strip()
        configured = self._registry.batch(program.id, requested) if requested else None
        # An older/newly published cohort can predate registry configuration.
        # Preserve the source's explicit claim; never silently relabel it current.
        return configured.id if configured is not None else requested or program.batch

    def _mark_validated(self, canonical_id: str, event: CanonicalEvent) -> None:
        """Persist the promotion decision for ranking/API integrity gates."""
        if event.entity_type not in {EntityType.COMPANY, EntityType.PERSON}:
            return
        self._db.write_entity_validation(
            canonical_id,
            event.entity_type.value,
            evidence_count=1,
            provenance={
                "event_id": event.event_id,
                "source": event.source.value,
                "source_url": event.source_url,
            },
        )


# ── Module helpers ────────────────────────────────────────────────────────────


def _best_fuzzy_match(
    query: str,
    candidates: list[tuple[str, object]],
) -> tuple[object | None, float]:
    """
    Return the candidate with the highest SequenceMatcher ratio to `query`.

    SequenceMatcher is used instead of an external library to keep
    dependencies minimal (§5: local-first in V1).

    Returns (None, 0.0) if candidates is empty.
    """
    if not candidates:
        return None, 0.0

    best_obj: object = None
    best_score: float = 0.0
    query_lower = query.strip().lower()

    for label, obj in candidates:
        score = SequenceMatcher(None, query_lower, label.strip().lower()).ratio()
        if score > best_score:
            best_score = score
            best_obj = obj

    return best_obj, best_score


def _name_from_entity_id(entity_id: str) -> str:
    """
    Derive a display name from an entity_id as a last resort.

    "linkedin_co__neural-stack" → "Neural Stack"
    """
    slug_part = entity_id.split("__", 1)[-1]
    return slug_part.replace("-", " ").replace("_", " ").title()
