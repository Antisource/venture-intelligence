"""Generic corroboration connector for programs without a public directory."""

from __future__ import annotations

from xml.etree import ElementTree

from venture_intelligence.connectors.base import BaseConnector
from venture_intelligence.crawler import StealthCrawler
from venture_intelligence.programs import ProgramConfig, ProgramRegistry
from venture_intelligence.shared.models import CanonicalEvent, EntityType, EventType, Source


class TriangulationConnector(BaseConnector):
    source_name = Source.TRIANGULATION.value

    def __init__(self, registry: ProgramRegistry, crawler: StealthCrawler | None = None) -> None:
        self.registry = registry
        self.crawler = crawler or StealthCrawler()

    async def scan(self) -> list[CanonicalEvent]:
        events: list[CanonicalEvent] = []
        for program in self.registry.active():
            if program.detection_strategy != "triangulation":
                continue
            if program.talent_page_url:
                events.extend(await self._scan_text_page(program, program.talent_page_url))
            if program.substack_rss_url:
                events.extend(await self._scan_rss(program, program.substack_rss_url))
        return events

    async def investigate(self, trigger: CanonicalEvent) -> list[CanonicalEvent]:
        # Triangulation produces company-membership corroboration. A founder
        # name appearing on a program page is not a company identity and must
        # stay on the person/evidence path.
        if trigger.entity_type is not EntityType.COMPANY:
            return []
        name = str(
            trigger.raw_payload.get("name") or trigger.metadata.get("entity_name") or ""
        ).strip()
        if not name:
            return []
        program = self.registry.get(str(trigger.metadata.get("program_id", "")))
        programs = (
            [program]
            if program
            else [
                item
                for item in self.registry.active()
                if item.detection_strategy == "triangulation"
            ]
        )
        return [
            event
            for item in programs
            if item is not None
            for event in await self._scan_program_for_name(item, name, trigger)
        ]

    def can_investigate(self, event: CanonicalEvent) -> bool:
        source = getattr(event.source, "value", event.source)
        return event.entity_type is EntityType.COMPANY and source != self.source_name and bool(
            event.raw_payload.get("name") or event.metadata.get("entity_name")
        )

    async def close(self) -> None:
        await self.crawler.close()

    async def _scan_program_for_name(
        self, program: ProgramConfig, name: str, trigger: CanonicalEvent
    ) -> list[CanonicalEvent]:
        urls = [url for url in (program.talent_page_url, program.substack_rss_url) if url]
        results = []
        for url in urls:
            response = await self.crawler.fetch_text(url)
            if response.ok and response.text and name.casefold() in response.text.casefold():
                results.append(self._event(program, name, response.url, trigger))
        return results

    async def _scan_text_page(self, program: ProgramConfig, url: str) -> list[CanonicalEvent]:
        response = await self.crawler.fetch_text(url)
        if not response.ok or not response.text:
            return []
        # The page itself is a single environmental observation until a
        # connector can resolve company identities with sufficient confidence.
        return []

    async def _scan_rss(self, program: ProgramConfig, url: str) -> list[CanonicalEvent]:
        response = await self.crawler.fetch_text(url)
        if not response.ok or not response.text:
            return []
        try:
            root = ElementTree.fromstring(response.text)
        except ElementTree.ParseError:
            return []
        events: list[CanonicalEvent] = []
        for item in root.findall(".//item")[:20]:
            title = (item.findtext("title") or "").strip()
            if title and self.registry.resolve_program_for_text(title) == program:
                # An RSS title alone does not identify an entity. It remains a
                # source for later targeted corroboration rather than evidence.
                continue
        return events

    def _event(
        self, program: ProgramConfig, name: str, url: str, trigger: CanonicalEvent
    ) -> CanonicalEvent:
        confidence = min(0.9, max(0.5, program.base_confidence))
        return CanonicalEvent(
            **self.child_event(
                trigger,
                event_type=EventType.COMPANY_LISTED,
                entity_type=EntityType.COMPANY,
                entity_id=self.registry.entity_id(program.id, name),
                source=Source.TRIANGULATION,
                source_url=url,
                confidence=confidence,
                raw_payload={"name": name, "program_id": program.id},
                metadata={
                    "program_id": program.id,
                    "evidence_tier": "corroborated_public_source",
                    "triggered_by": trigger.event_id,
                    "investigation_depth": trigger.metadata.get("investigation_depth", 0) + 1,
                },
            )
        )
