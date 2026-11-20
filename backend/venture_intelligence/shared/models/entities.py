"""Canonical resolved entities (§14)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from venture_intelligence.shared.models._helpers import _now, _uid

# Classes and helpers defined here keep the historical public module path.
# OpenAPI component names (e.g. venture_intelligence__shared__models__SourceAccessMode
# on a name collision), pydantic core-schema refs and pickled qualified paths all
# derive from ``__module__``, which Python takes from ``__name__`` at definition time.
__name__ = "venture_intelligence.shared.models"

# ═══════════════════════════════════════════════════════════════════════════════
# §14  Entity models
# ═══════════════════════════════════════════════════════════════════════════════


class Person(BaseModel):
    """
    A deduplicated, canonical representation of a founder (§14).

    A single real person may arrive via LinkedIn (linkedin__john-doe),
    GitHub (github__johndoe), and YC page (yc__acme).  The Entity
    Resolution Engine merges these into one Person with all three
    source IDs in properties["source_ids"].
    """

    person_id: str = Field(default_factory=_uid)
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    linkedin_url: str | None = None
    github_username: str | None = None
    twitter_handle: str | None = None
    current_title: str | None = None
    current_company_id: str | None = None
    batch: str | None = None
    identity_confidence: float = 0.90
    first_seen_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    properties: dict[str, Any] = Field(default_factory=dict)


class Company(BaseModel):
    """
    A deduplicated, canonical representation of a startup (§14).
    """

    company_id: str = Field(default_factory=_uid)
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    slug: str | None = None
    yc_url: str | None = None
    website: str | None = None
    description: str | None = None
    batch: str | None = None
    tags: list[str] = Field(default_factory=list)
    founder_ids: list[str] = Field(default_factory=list)
    identity_confidence: float = 0.90
    first_seen_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    properties: dict[str, Any] = Field(default_factory=dict)
