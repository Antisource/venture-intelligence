"""Public environmental-signal connectors.

These connectors are intentionally conservative. A mention can trigger
corroboration, but receives a lower confidence than an official directory.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlsplit

from venture_intelligence.agents.environmental_extractor import EnvironmentalExtractor
from venture_intelligence.config import Settings, settings
from venture_intelligence.connectors.base import BaseConnector
from venture_intelligence.crawler import StealthCrawler
from venture_intelligence.pipeline.entity_integrity import is_generic_label
from venture_intelligence.programs import ProgramRegistry
from venture_intelligence.shared.models import (
    CanonicalEvent,
    EntityType,
    EventType,
    Source,
    SourceAccessMode,
)


class _PublicMentionConnector(BaseConnector):
    event_type: EventType
    source: Source
    url_field: str
    confidence: float

    def __init__(
        self,
        registry: ProgramRegistry,
        crawler: StealthCrawler | None = None,
        extractor: EnvironmentalExtractor | None = None,
        runtime_settings: Settings = settings,
    ) -> None:
        self.registry = registry
        self.crawler = crawler or StealthCrawler()
        self.extractor = extractor or EnvironmentalExtractor()
        self.settings = runtime_settings

    async def scan(self) -> list[CanonicalEvent]:
        # Collection sources are scanned only where URLs have deliberately been
        # configured. Empty configuration is a valid no-op, not a failure.
        return []

    async def investigate(self, trigger: CanonicalEvent) -> list[CanonicalEvent]:
        name = str(
            trigger.raw_payload.get("name") or trigger.metadata.get("entity_name") or ""
        ).strip()
        if not name:
            return []
        selected = self.registry.get(str(trigger.metadata.get("program_id", "")))
        programs = [selected] if selected else self.registry.active()
        events: list[CanonicalEvent] = []
        for program in programs:
            if program is None:
                continue
            urls = getattr(program, self.url_field)
            for template in urls:
                url = template.replace("{query}", quote_plus(name))
                if not self._url_allowed(url):
                    continue
                result = await self.crawler.fetch_text(url)
                if result.ok and result.text and name.casefold() in result.text.casefold():
                    events.append(
                        self._event(
                            program.id,
                            name,
                            url,
                            trigger,
                            await self._extracted_payload(result.text, self.source_name, url),
                        )
                    )
        return events

    def _url_allowed(self, url: str) -> bool:
        return True

    def can_investigate(self, event: CanonicalEvent) -> bool:
        source = getattr(event.source, "value", event.source)
        return source != self.source_name and bool(
            event.raw_payload.get("name") or event.metadata.get("entity_name")
        )

    async def close(self) -> None:
        await self.crawler.close()

    async def _extracted_payload(self, text: str, platform: str, url: str) -> dict:
        signal = await self.extractor.extract_with_fallback(text, platform, url)
        return {"environmental_extraction": signal.model_dump() if signal else None}

    def _event(
        self,
        program_id: str,
        name: str,
        url: str,
        trigger: CanonicalEvent,
        extra_payload: dict | None = None,
    ) -> CanonicalEvent:
        return CanonicalEvent(
            **self.child_event(
                trigger,
                event_type=self.event_type,
                entity_type=EntityType.COMPANY,
                entity_id=self.registry.entity_id(program_id, name),
                source=self.source,
                source_url=url,
                confidence=self.confidence,
                raw_payload={"name": name, "program_id": program_id, **(extra_payload or {})},
                metadata={
                    "program_id": program_id,
                    "evidence_tier": "environmental",
                    "triggered_by": trigger.event_id,
                },
            )
        )


class NewsletterConnector(_PublicMentionConnector):
    source_name = Source.NEWSLETTER.value
    source = Source.NEWSLETTER
    event_type = EventType.NEWSLETTER_MENTION
    url_field = "substack_rss_url"
    confidence = 0.6

    async def investigate(self, trigger: CanonicalEvent) -> list[CanonicalEvent]:
        # RSS URLs are one per program rather than a list of templates.
        name = str(
            trigger.raw_payload.get("name") or trigger.metadata.get("entity_name") or ""
        ).strip()
        if not name:
            return []
        selected = self.registry.get(str(trigger.metadata.get("program_id", "")))
        programs = [selected] if selected else self.registry.active()
        events: list[CanonicalEvent] = []
        for program in programs:
            if program is None or not program.substack_rss_url:
                continue
            result = await self.crawler.fetch_text(program.substack_rss_url)
            if result.ok and result.text and name.casefold() in result.text.casefold():
                events.append(
                    self._event(
                        program.id,
                        name,
                        program.substack_rss_url,
                        trigger,
                        await self._extracted_payload(
                            result.text, self.source_name, program.substack_rss_url
                        ),
                    )
                )
        return events


class BlogConnector(_PublicMentionConnector):
    source_name = Source.BLOG.value
    source = Source.BLOG
    event_type = EventType.BLOG_MENTION
    url_field = "blog_urls"
    confidence = 0.58


class WebsiteConnector(_PublicMentionConnector):
    """Fetch configured public personal/company websites on investigation."""

    source_name = Source.WEBSITE.value
    source = Source.WEBSITE
    event_type = EventType.COMPANY_WEBSITE_FOUND
    url_field = "website_urls"
    confidence = 0.56


class SocialConnector(_PublicMentionConnector):
    """Low-confidence public social connector.

    It never authenticates or bypasses access controls.
    """

    source_name = Source.SOCIAL.value
    source = Source.SOCIAL
    event_type = EventType.SOCIAL_MENTION
    url_field = "social_search_urls"
    confidence = 0.5

    async def scan(self) -> list[CanonicalEvent]:
        """Search configured public X/Instagram pages using bounded rendered text."""
        if not self._direct_public_web_enabled:
            return []
        events: list[CanonicalEvent] = []
        for program in self.registry.active():
            for keyword in program.keywords[:2]:
                query = quote_plus(keyword)
                urls = [
                    ("x", f"https://x.com/search?q={query}&f=live"),
                    ("instagram", f"https://www.instagram.com/explore/tags/{self._tag(keyword)}/"),
                ]
                for platform, url in urls:
                    result = await self.crawler.fetch_rendered_text(url)
                    if not result.ok or not result.text:
                        continue
                    for handle in self._handles(result.text)[:5]:
                        # A platform/navigation handle is not a founder. Drop
                        # generic labels at extraction time so they never even
                        # become candidate events for the EventBus to reject.
                        if is_generic_label(handle):
                            continue
                        events.append(
                            CanonicalEvent(
                                event_type=EventType.SOCIAL_MENTION,
                                entity_type=EntityType.PERSON,
                                entity_id=self.registry.entity_id(program.id, handle),
                                source=Source.SOCIAL,
                                source_url=url,
                                occurred_at=datetime.now(timezone.utc),
                                confidence=0.5,
                                raw_payload={
                                    "name": handle,
                                    f"{platform}_handle": handle,
                                    "program_id": program.id,
                                },
                                metadata={
                                    "program_id": program.id,
                                    "platform": platform,
                                    "evidence_tier": "environmental",
                                },
                            )
                        )
        return events

    async def investigate(self, trigger: CanonicalEvent) -> list[CanonicalEvent]:
        handle = (
            trigger.raw_payload.get("x_handle")
            or trigger.raw_payload.get("twitter_handle")
            or trigger.raw_payload.get("instagram_handle")
        )
        if handle and self._direct_public_web_enabled:
            name = str(
                trigger.raw_payload.get("name") or trigger.metadata.get("entity_name") or handle
            )
            if is_generic_label(str(name)):
                return []
            platform = "instagram" if trigger.raw_payload.get("instagram_handle") else "x"
            base_url = "https://www.instagram.com" if platform == "instagram" else "https://x.com"
            url = f"{base_url}/{str(handle).lstrip('@')}"
            result = await self.crawler.fetch_rendered_text(url)
            if result.ok and result.text:
                program = self.registry.resolve_program_for_text(result.text)
                if program:
                    payload = await self._extracted_payload(result.text, platform, url)
                    return [
                        self._event(program.id, name, url, trigger, payload).model_copy(
                            update={"confidence": 0.55}
                        )
                    ]
        return await super().investigate(trigger)

    def can_investigate(self, event: CanonicalEvent) -> bool:
        if self._direct_public_web_enabled:
            return super().can_investigate(event)
        has_safe_discovery = any(program.social_search_urls for program in self.registry.active())
        return has_safe_discovery and super().can_investigate(event)

    def _url_allowed(self, url: str) -> bool:
        if self._direct_public_web_enabled:
            return True
        destination = (urlsplit(url).hostname or "").casefold().rstrip(".")
        return not any(
            destination == host or destination.endswith(f".{host}")
            for host in ("linkedin.com", "x.com", "instagram.com")
        )

    @property
    def _direct_public_web_enabled(self) -> bool:
        return self.settings.restricted_social_access_mode == SourceAccessMode.APPROVED_PUBLIC_WEB

    @staticmethod
    def _tag(keyword: str) -> str:
        return re.sub(r"[^a-z0-9]", "", keyword.casefold())[:50] or "startup"

    @staticmethod
    def _handles(text: str) -> list[str]:
        return list(dict.fromkeys(re.findall(r"@([A-Za-z0-9_]{2,30})", text)))


class DiscordConnector(BaseConnector):
    """Reads only public Discord widgets enabled by their server owners."""

    source_name = Source.DISCORD.value

    def __init__(self, registry: ProgramRegistry, crawler: StealthCrawler | None = None) -> None:
        self.registry = registry
        self.crawler = crawler or StealthCrawler()

    async def scan(self) -> list[CanonicalEvent]:
        # Widget usernames are presence clues, not canonical people. Autonomous
        # scans therefore never manufacture founder/person events.
        return []

    async def _members(self) -> list[tuple[str, str, str]]:
        collected: list[tuple[str, str, str]] = []
        for program in self.registry.active():
            for guild_id in program.discord_servers:
                url = f"https://discord.com/api/guilds/{guild_id}/widget.json"
                result = await self.crawler.fetch_text(url)
                if not result.ok or not result.text:
                    continue
                try:
                    payload_members = json.loads(result.text).get("members", [])
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload_members, list):
                    continue
                for member in payload_members[:25]:
                    if not isinstance(member, dict):
                        continue
                    name = str(member.get("username") or "").strip()
                    if not name:
                        continue
                    collected.append((program.id, name, url))
        return collected

    async def investigate(self, trigger: CanonicalEvent) -> list[CanonicalEvent]:
        name = str(trigger.raw_payload.get("name") or "").casefold()
        if not name:
            return []
        events: list[CanonicalEvent] = []
        for program_id, member_name, url in await self._members():
            if member_name.casefold() != name:
                continue
            events.append(
                CanonicalEvent(
                    **self.child_event(
                        trigger,
                        event_type=EventType.COMMUNITY_MEMBERSHIP,
                        entity_type=trigger.entity_type,
                        entity_id=trigger.entity_id,
                        source=Source.DISCORD,
                        source_url=url,
                        confidence=0.35,
                        raw_payload={"name": member_name, "program_id": program_id},
                        metadata={
                            "program_id": program_id,
                            "public_widget": True,
                            "evidence_tier": "environmental",
                            "identity_authority": False,
                        },
                    )
                )
            )
        return events

    def can_investigate(self, event: CanonicalEvent) -> bool:
        return getattr(event.source, "value", event.source) != self.source_name and bool(
            event.raw_payload.get("name")
        )

    async def close(self) -> None:
        await self.crawler.close()
