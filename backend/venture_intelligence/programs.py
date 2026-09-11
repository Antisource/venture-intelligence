"""Configuration-driven accelerator program registry."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from venture_intelligence.config import settings


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise ValueError("entity name must include at least one letter or number")
    return slug


class ProgramBatchConfig(BaseModel):
    """One supported batch beneath a canonical accelerator identity."""

    id: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=120)
    directory_url: str | None = None
    keywords: list[str] = Field(default_factory=list)
    historical: bool = False

    @field_validator("keywords")
    @classmethod
    def unique_batch_keywords(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class ProgramConfig(BaseModel):
    """All connector-facing program data, loaded from the program registry."""

    id: str
    name: str
    batch: str
    canonical_program_id: str | None = None
    current_batch: str | None = None
    supported_batches: list[ProgramBatchConfig] = Field(default_factory=list)
    active: bool = True
    detection_strategy: Literal["directory", "triangulation"]
    directory_url: str | None = None
    directory_search_index: str | None = None
    directory_detail_url_template: str | None = None
    talent_page_url: str | None = None
    substack_rss_url: str | None = None
    blog_urls: list[str] = Field(default_factory=list)
    website_urls: list[str] = Field(default_factory=list)
    social_search_urls: list[str] = Field(default_factory=list)
    discord_servers: list[str] = Field(default_factory=list)
    demo_day: str | None = None
    base_confidence: float = Field(ge=0, le=1)
    keywords: list[str] = Field(min_length=1)
    entity_id_prefix: str
    color: str = "#64748b"

    @field_validator("directory_search_index")
    @classmethod
    def valid_directory_search_index(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", normalized):
            raise ValueError("directory_search_index must be a plain public index name")
        return normalized

    @field_validator("directory_detail_url_template")
    @classmethod
    def valid_directory_detail_url_template(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        parsed = urlsplit(normalized)
        if parsed.scheme != "https" or not parsed.hostname or normalized.count("{slug}") != 1:
            raise ValueError("directory_detail_url_template must be HTTPS with one {slug}")
        return normalized

    @field_validator("id", "entity_id_prefix")
    @classmethod
    def valid_identifier(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
            raise ValueError("identifiers must be lowercase letters, numbers, and underscores")
        return value

    @field_validator("keywords")
    @classmethod
    def unique_keywords(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if not normalized:
            raise ValueError("at least one non-empty keyword is required")
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def normalize_batch_contract(self) -> "ProgramConfig":
        self.canonical_program_id = self.canonical_program_id or self.id
        self.current_batch = self.current_batch or self.batch
        if not self.supported_batches:
            self.supported_batches = [
                ProgramBatchConfig(
                    id=self.batch,
                    name=self.batch,
                    directory_url=self.directory_url,
                    keywords=list(self.keywords),
                )
            ]
        supported = {item.id.casefold(): item for item in self.supported_batches}
        selected = supported.get(self.current_batch.casefold())
        if selected is None:
            raise ValueError("current_batch must identify one supported batch")
        if self.batch.casefold() != self.current_batch.casefold():
            raise ValueError("batch is the compatibility alias for current_batch")
        return self


class ProgramRegistry:
    """The single source of truth for active accelerator programs."""

    def __init__(self, programs: list[ProgramConfig]) -> None:
        ids = [program.id for program in programs]
        if len(ids) != len(set(ids)):
            raise ValueError("program ids must be unique")
        active_prefixes = [program.entity_id_prefix for program in programs if program.active]
        if len(active_prefixes) != len(set(active_prefixes)):
            raise ValueError("active canonical programs must use unique entity_id_prefix values")
        self._programs = {program.id: program for program in programs}

    @classmethod
    def from_yaml(cls, path: Path) -> "ProgramRegistry":
        with path.open("r", encoding="utf-8") as config_file:
            document = yaml.safe_load(config_file) or {}
        records = document.get("programs")
        if not isinstance(records, list):
            raise ValueError("programs.yaml must contain a top-level 'programs' list")
        return cls([ProgramConfig.model_validate(record) for record in records])

    @classmethod
    def load(cls, path: str | Path | None = None) -> "ProgramRegistry":
        configured = Path(path or settings.programs_config_path)
        if not configured.is_absolute():
            project_root = Path(__file__).resolve().parents[2]
            configured = project_root / configured
        return cls.from_yaml(configured)

    def active(self) -> list[ProgramConfig]:
        return [program for program in self._programs.values() if program.active]

    def all(self) -> list[ProgramConfig]:
        """Return configured programs in registry order, including inactive entries."""
        return list(self._programs.values())

    def get(self, program_id: str) -> ProgramConfig | None:
        return self._programs.get(program_id)

    def by_batch(self, batch: str) -> ProgramConfig | None:
        normalized = batch.casefold().strip()
        return next(
            (
                program
                for program in self._programs.values()
                if any(item.id.casefold() == normalized for item in program.supported_batches)
            ),
            None,
        )

    def batch(self, program_id: str, batch_id: str | None = None) -> ProgramBatchConfig | None:
        """Resolve a current or historical batch without duplicating the program."""
        program = self.get(program_id)
        if program is None:
            return None
        selected = (batch_id or program.current_batch or program.batch).casefold()
        return next(
            (item for item in program.supported_batches if item.id.casefold() == selected),
            None,
        )

    def resolve_program_for_text(self, text: str) -> ProgramConfig | None:
        """Return the best active keyword match, case-insensitively."""
        haystack = text.casefold()
        matches = [
            (len(keyword), program)
            for program in self.active()
            for keyword in [
                *program.keywords,
                *(
                    value
                    for batch in program.supported_batches
                    for value in [batch.id, batch.name, *batch.keywords]
                ),
            ]
            if keyword.casefold() in haystack
        ]
        return max(matches, key=lambda item: item[0])[1] if matches else None

    def entity_id(self, program_id: str, entity_name: str) -> str:
        program = self.get(program_id)
        if program is None:
            raise KeyError(f"unknown program id: {program_id}")
        return f"{program.entity_id_prefix}__{_slug(entity_name)}"

    def resolve_context(
        self,
        *,
        program_id: str | None = None,
        batch: str | None = None,
        entity_id: str | None = None,
        source_url: str | None = None,
        text: str | None = None,
    ) -> ProgramConfig | None:
        """Resolve program identity only from configured, auditable context.

        This is the compatibility boundary for older events that predate an
        explicit ``program_id``. It never assumes a particular accelerator or
        batch: configured ids, batches, prefixes, URLs and keywords are the
        only accepted signals.
        """
        if program_id:
            matched = self.get(program_id)
            if matched is not None:
                return matched
        if batch:
            matched = self.by_batch(batch)
            if matched is not None:
                return matched
        if entity_id:
            prefix = entity_id.casefold().split("__", 1)[0]
            matched = next(
                (
                    program
                    for program in self._programs.values()
                    if program.entity_id_prefix.casefold() == prefix
                ),
                None,
            )
            if matched is not None:
                return matched
        if source_url:
            host = (urlsplit(source_url).hostname or "").casefold()
            if host:
                for program in self._programs.values():
                    configured_urls = (
                        program.directory_url,
                        program.talent_page_url,
                        program.substack_rss_url,
                        *program.blog_urls,
                        *program.website_urls,
                    )
                    if any(
                        (urlsplit(url).hostname or "").casefold() == host
                        for url in configured_urls
                        if url
                    ):
                        return program
        return self.resolve_program_for_text(text) if text else None

    def serializable(self) -> list[dict]:
        return [program.model_dump(mode="json") for program in self.active()]


@lru_cache(maxsize=1)
def get_program_registry() -> ProgramRegistry:
    """Load once per process; tests may call ``cache_clear`` after changing paths."""
    return ProgramRegistry.load()
