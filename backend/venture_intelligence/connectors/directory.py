"""Generic public accelerator-directory connector."""

from __future__ import annotations

import asyncio
import html
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import quote, urlencode, urljoin, urlsplit
from uuid import NAMESPACE_URL, uuid5

from venture_intelligence.connectors.base import BaseConnector
from venture_intelligence.crawler import StealthCrawler
from venture_intelligence.observability.metrics import metrics
from venture_intelligence.pipeline.entity_integrity import is_generic_label, validate_candidate
from venture_intelligence.programs import ProgramRegistry
from venture_intelligence.shared.models import CanonicalEvent, EntityType, EventType, Source


class _DirectoryData(HTMLParser):
    """Read explicit public company containers, never arbitrary hydration objects."""

    def __init__(self) -> None:
        super().__init__()
        self.companies: list[dict] = []
        self._script: list[str] | None = None

    def _collect(self, value: str, *, next_data: bool = False) -> None:
        try:
            data = json.loads(value)
            props = data.get("props", data)
            if next_data:
                props = props.get("pageProps", {})
            company = props.get("company")
            if isinstance(company, dict):
                self.companies.append(company)
            collection = props.get("companies", props)
            results = collection.get("results") if isinstance(collection, dict) else None
            if isinstance(results, list):
                self.companies.extend(item for item in results if isinstance(item, dict))
        except (ValueError, AttributeError):
            return

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("data-page"):
            self._collect(attributes["data-page"])
        if tag == "script" and attributes.get("id") == "__NEXT_DATA__":
            self._script = []

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            self._collect("".join(self._script), next_data=True)
            self._script = None


class DirectoryConnector(BaseConnector):
    source_name = Source.DIRECTORY.value

    def __init__(self, registry: ProgramRegistry, crawler: StealthCrawler | None = None) -> None:
        self.registry = registry
        self.crawler = crawler or StealthCrawler()

    async def scan(self) -> list[CanonicalEvent]:
        events: list[CanonicalEvent] = []
        for program in self.registry.active():
            if not program.directory_url:
                continue
            result = await self.crawler.fetch_text(program.directory_url)
            if not result.ok or not result.text:
                status = result.source_status.value if result.source_status else "FAILED"
                metrics.record_connector_outcome(
                    self.source_name,
                    status,
                    events_found=0,
                    final_url=result.url,
                    detail=result.error,
                )
                continue
            structured = self._company_records(result.text, result.url)
            if not structured and program.directory_search_index:
                structured = await self._public_index_records(program, result.text)
            # An explicit cohort on a public detail page outranks the requested
            # collection scope. Never relabel last batch as the current batch.
            records = [
                {**item, "batch": program.batch}
                for item in structured
                if self._matches_current_batch(program.id, program.batch, item["batch"])
            ]
            names = [item["name"] for item in records]
            if not structured and not any(
                marker in result.text for marker in ("data-page=", "__NEXT_DATA__")
            ):
                names = self._candidate_names(result.text)
                records = [{"name": name, "program_id": program.id} for name in names]
            status = (
                "SUCCESS_EMPTY"
                if structured and not records
                else self._document_status(result.text, names)
            )
            metrics.record_connector_outcome(
                self.source_name,
                status,
                events_found=len(names),
                final_url=result.url,
                detail=None if names else "directory returned no extractable company cards",
            )
            for record in records:
                name = record["name"]
                record_url = record.get("program_source_url", result.url)
                event_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        (
                            f"directory:{program.id}:{record_url}:"
                            f"{name.casefold()}:{record.get('batch') or program.batch}"
                        ),
                    )
                )
                events.append(
                    CanonicalEvent(
                        event_id=event_id,
                        event_type=EventType.COMPANY_LISTED,
                        entity_type=EntityType.COMPANY,
                        entity_id=self.registry.entity_id(program.id, name)
                        + (
                            "__" + str(uuid5(NAMESPACE_URL, record["program_source_url"]))
                            if structured
                            else ""
                        ),
                        source=Source.DIRECTORY,
                        source_url=record_url,
                        occurred_at=datetime.now(timezone.utc),
                        confidence=program.base_confidence,
                        raw_payload={**record, "program_id": program.id},
                        metadata={
                            "program_id": program.id,
                            "batch": record.get("batch"),
                            "evidence_tier": "directory",
                            "identity_evidence": "explicit_public_company_record"
                            if structured
                            else "visible_directory_card",
                            "acquisition_url": result.url,
                        },
                    )
                )
                events.extend(self.founder_events(events[-1]))
        return events

    def _matches_current_batch(self, program_id: str, batch_id: str, value: str) -> bool:
        batch = self.registry.batch(program_id, batch_id)
        accepted = {batch_id.casefold()}
        if batch is not None:
            accepted.update(item.casefold() for item in [batch.id, batch.name, *batch.keywords])
        return value.strip().casefold() in accepted

    async def _public_index_records(self, program, document: str) -> list[dict]:
        """Read the official directory's page-advertised public search index."""
        options = self._public_search_options(document)
        if options is None or not program.directory_url or not program.directory_search_index:
            return []
        app_id, api_key = options
        batch = self.registry.batch(program.id, program.batch)
        if batch is None:
            return []
        endpoint = (
            f"https://{app_id.lower()}-dsn.algolia.net/1/indexes/"
            f"{program.directory_search_index}/query"
        )
        params = urlencode(
            {
                "hitsPerPage": 100,
                "facetFilters": json.dumps(
                    [f"batch:{batch.name}"], separators=(",", ":")
                ),
            }
        )
        response = await self.crawler.post_json_text(
            endpoint,
            {"params": params},
            headers={
                "x-algolia-application-id": app_id,
                "x-algolia-api-key": api_key,
                "content-type": "application/json",
            },
        )
        if not response.ok or not response.text:
            return []
        try:
            payload = json.loads(response.text)
            hits = payload.get("hits", payload.get("results", []))
        except (ValueError, AttributeError):
            return []
        if not isinstance(hits, list):
            return []
        prepared_hits = []
        for item in hits:
            if not isinstance(item, dict):
                continue
            prepared = dict(item)
            slug = prepared.get("slug")
            if (
                not prepared.get("ycdc_url")
                and program.directory_detail_url_template
                and isinstance(slug, str)
                and re.fullmatch(r"[A-Za-z0-9-]{1,160}", slug)
            ):
                prepared["ycdc_url"] = program.directory_detail_url_template.format(
                    slug=quote(slug, safe="")
                )
            prepared_hits.append(prepared)
        indexed = self._company_records(
            json.dumps({"results": prepared_hits}),
            program.directory_url,
        )
        indexed = [
            item
            for item in indexed[:100]
            if self._matches_current_batch(program.id, program.batch, item["batch"])
        ]

        semaphore = asyncio.Semaphore(6)

        async def detail(index_record: dict) -> dict:
            async with semaphore:
                result = await self.crawler.fetch_text(index_record["program_source_url"])
            if not result.ok or not result.text:
                return index_record
            candidates = self._company_records(result.text, result.url)
            return next(
                (
                    item
                    for item in candidates
                    if item["name"].casefold() == index_record["name"].casefold()
                    and self._matches_current_batch(program.id, program.batch, item["batch"])
                ),
                index_record,
            )

        return list(await asyncio.gather(*(detail(item) for item in indexed)))

    @staticmethod
    def _public_search_options(document: str) -> tuple[str, str] | None:
        match = re.search(r"window\.AlgoliaOpts\s*=\s*(\{.*?\})\s*;", document, re.S)
        if match is None:
            return None
        try:
            options = json.loads(match.group(1))
        except ValueError:
            return None
        app_id = (
            options.get("app")
            or options.get("appId")
            or options.get("appID")
            or options.get("applicationID")
        )
        api_key = options.get("key") or options.get("apiKey")
        if not isinstance(app_id, str) or not isinstance(api_key, str):
            return None
        if not re.fullmatch(r"[A-Za-z0-9-]{2,80}", app_id):
            return None
        if not re.fullmatch(r"[A-Za-z0-9+/=_-]{8,512}", api_key):
            return None
        return app_id, api_key

    @staticmethod
    def founder_events(company: CanonicalEvent) -> list[CanonicalEvent]:
        """Normalize explicit, attributable founder claims; never infer from a name.

        The parent record remains immutable. Profile URLs are identity references,
        not permission to fetch those platforms or independent corroboration.
        """
        if (
            company.source != Source.DIRECTORY
            or company.event_type != EventType.COMPANY_LISTED
            or company.metadata.get("identity_evidence") != "explicit_public_company_record"
            or not validate_candidate(company).accepted
        ):
            return []
        events = []
        for claim in company.raw_payload.get("founders", []):
            name = (
                claim.get("full_name")
                or claim.get("name")
                or " ".join(
                    str(claim.get(key) or "").strip() for key in ("first_name", "last_name")
                ).strip()
            )
            profile = claim.get("linkedin_url") or claim.get("website_url") or ""
            event = CanonicalEvent(
                event_id=str(uuid5(NAMESPACE_URL, f"{company.event_id}:founder:{name}:{profile}")),
                entity_id="directory_person__"
                + str(uuid5(NAMESPACE_URL, f"{company.source_url}:founder:{name}:{profile}")),
                event_type=EventType.FOUNDER_DETECTED,
                entity_type=EntityType.PERSON,
                source=Source.DIRECTORY,
                source_url=company.source_url,
                occurred_at=company.occurred_at,
                occurred_at_precision=company.occurred_at_precision,
                observed_at=company.observed_at,
                ingested_at=company.ingested_at,
                validity=company.validity,
                confidence=company.confidence,
                raw_payload={
                    "name": name,
                    "headline": claim.get("title"),
                    "profile_url": profile,
                    "company_name": company.raw_payload["name"],
                    "company_source_id": company.entity_id,
                    "company_source_url": company.source_url,
                    "founder_claim": claim,
                    "batch": company.raw_payload.get("batch"),
                    "program_id": company.raw_payload.get("program_id"),
                },
                metadata={
                    **company.metadata,
                    "identity_evidence": "explicit_public_founder_record",
                    "parent_event_id": company.event_id,
                    "evidence_origin": company.source_url,
                },
            )
            if validate_candidate(event).accepted:
                events.append(event)
        return list({event.event_id: event for event in events}.values())

    @staticmethod
    def _company_records(document: str, source_url: str) -> list[dict]:
        parser = _DirectoryData()
        # Some official directories expose the same public collection as a
        # top-level JSON response rather than embedding it in HTML hydration.
        parser._collect(document)
        parser.feed(document)
        records = []
        for company in parser.companies:
            name = company.get("name")
            batch = company.get("batch") or company.get("cohort")
            if not isinstance(name, str) or is_generic_label(name) or not isinstance(batch, str):
                continue
            # Only a same-origin company detail URL may carry directory authority.
            url = urljoin(source_url, str(company.get("ycdc_url") or source_url))
            if urlsplit(url).netloc.casefold() != urlsplit(source_url).netloc.casefold():
                continue
            founder_container = (
                "founders" if isinstance(company.get("founders"), list) else "founder_set"
            )
            founders = company.get(founder_container, [])
            records.append(
                {
                    "name": name.strip(),
                    "batch": batch,
                    "program_source_url": url,
                    "website": company.get("website") or company.get("website_url"),
                    "description": company.get("long_description") or company.get("description"),
                    # Preserve only public identity fields, not all hydrated user data.
                    # These are claims, not independently promoted person entities.
                    "founders": [
                        {
                            **{
                                key: value
                                for key, value in founder.items()
                                if key
                                in {
                                    "full_name",
                                    "name",
                                    "first_name",
                                    "last_name",
                                    "title",
                                    "founder_bio",
                                    "linkedin_url",
                                    "website_url",
                                }
                            },
                            "_claim_container": founder_container,
                        }
                        for founder in founders
                        if isinstance(founder, dict)
                    ]
                    if isinstance(founders, list)
                    else [],
                }
            )
        return records

    @staticmethod
    def _document_status(document: str, names: list[str]) -> str:
        if names:
            return "SUCCESS"
        lowered = document.casefold()
        if "data-program-directory" in lowered:
            return "SUCCESS_EMPTY"
        if "companies/indexpage" in lowered or "currentbatch" in lowered:
            return "SOURCE_DRIFT"
        return "PARSER_FAILURE"

    async def investigate(self, trigger: CanonicalEvent) -> list[CanonicalEvent]:
        # Directories are scanned as program collections; they cannot reliably
        # look up a generic entity without connector-specific identifiers.
        return []

    def can_investigate(self, event: CanonicalEvent) -> bool:
        return False

    async def close(self) -> None:
        await self.crawler.close()

    @staticmethod
    def _candidate_names(document: str) -> list[str]:
        """Conservative fallback parser for visible company card labels.

        Modern accelerator directories change DOM frequently, so parsing avoids
        treating arbitrary page text as companies. Tests exercise this method
        with explicit card-style links; a live selector failure simply yields no
        events and never a false success.
        """
        matches = re.findall(
            r'href=["\'][^"\']*/companies/[^"\']+["\'][^>]*>([^<]{2,100})<',
            document,
            re.I,
        )
        names = [html.unescape(re.sub(r"\s+", " ", item)).strip() for item in matches]
        return list(
            dict.fromkeys(
                name
                for name in names
                if re.search(r"[A-Za-z]", name) and not is_generic_label(name)
            )
        )
