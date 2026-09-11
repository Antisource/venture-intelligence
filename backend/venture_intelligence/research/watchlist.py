"""Config-driven public intelligence watchlist and incremental source state."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


class WatchlistEntry(BaseModel):
    id: str
    name: str
    kind: Literal["publication", "newsletter", "vc_firm", "vc_partner", "journalist"]
    organization: str | None = None
    aliases: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    public_urls: list[str] = Field(default_factory=list)
    feed_urls: list[str] = Field(default_factory=list)
    social_urls: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    active: bool = True


class IntelligenceWatchlist(BaseModel):
    version: int = 1
    description: str = ""
    entries: list[WatchlistEntry] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "IntelligenceWatchlist":
        value = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.model_validate(value)

    def active(self, *kinds: str) -> list[WatchlistEntry]:
        accepted = set(kinds)
        return [
            item for item in self.entries if item.active and (not accepted or item.kind in accepted)
        ]

    def resolve(self, value: str, *kinds: str) -> WatchlistEntry | None:
        """Resolve a configured public-intelligence name or alias exactly."""
        needle = value.strip().casefold()
        return next(
            (
                item
                for item in self.active(*kinds)
                if needle
                in {
                    item.id.casefold(),
                    item.name.casefold(),
                    (item.organization or "").casefold(),
                    *(alias.casefold() for alias in item.aliases),
                }
            ),
            None,
        )


class SourceIncrementalState(BaseModel):
    source_id: str
    last_checked_at: str | None = None
    most_recent_item_at: str | None = None
    last_successful_fetch_at: str | None = None
    parse_health: str = "not_checked"
    duplicate_item_keys: list[str] = Field(default_factory=list)
    new_item_count: int = 0
    etag: str | None = None
    last_modified: str | None = None
    content_hash: str | None = None
    last_change_at: str | None = None


class SourceStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, SourceIncrementalState]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {
            key: SourceIncrementalState.model_validate(value)
            for key, value in raw.items()
            if isinstance(value, dict)
        }

    def save(self, states: dict[str, SourceIncrementalState]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(
                {key: value.model_dump(mode="json") for key, value in states.items()},
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def record(
        self,
        source_id: str,
        *,
        successful: bool,
        item_keys: list[str],
        newest_item_at: str | None = None,
        content: str = "",
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> SourceIncrementalState:
        states = self.load()
        previous = states.get(source_id, SourceIncrementalState(source_id=source_id))
        now = datetime.now(timezone.utc).isoformat()
        digest = hashlib.sha256(content.encode()).hexdigest() if content else previous.content_hash
        new_keys = [key for key in item_keys if key not in previous.duplicate_item_keys]
        changed = digest is not None and digest != previous.content_hash
        updated = previous.model_copy(
            update={
                "last_checked_at": now,
                "most_recent_item_at": newest_item_at or previous.most_recent_item_at,
                "last_successful_fetch_at": now
                if successful
                else previous.last_successful_fetch_at,
                "parse_health": "healthy" if successful else "failed",
                "duplicate_item_keys": list(
                    dict.fromkeys([*previous.duplicate_item_keys, *item_keys])
                )[-500:],
                "new_item_count": len(new_keys),
                "etag": etag or previous.etag,
                "last_modified": last_modified or previous.last_modified,
                "content_hash": digest,
                "last_change_at": now if changed else previous.last_change_at,
            }
        )
        states[source_id] = updated
        self.save(states)
        return updated


class SourceSettingsStore:
    """Persist manual enable overrides separately from transient health."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, bool]:
        if not self.path.exists():
            return {}
        try:
            raw: Any = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return (
            {str(key): bool(value) for key, value in raw.items()} if isinstance(raw, dict) else {}
        )

    def enabled(self, source_id: str, default: bool) -> bool:
        return self.load().get(source_id, default)

    def set_enabled(self, source_id: str, enabled: bool) -> None:
        values = self.load()
        values[source_id] = enabled
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(json.dumps(values, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)


__all__ = [
    "IntelligenceWatchlist",
    "SourceIncrementalState",
    "SourceSettingsStore",
    "SourceStateStore",
    "WatchlistEntry",
]
