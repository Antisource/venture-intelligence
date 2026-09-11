"""Generic public-source connectors used for entity corroboration.

Source-specific URLs are isolated here, while program vocabulary always comes
from :class:`ProgramRegistry`. These connectors emit only an environmental
observation; they do not turn a search hit into authoritative membership.
"""

from __future__ import annotations

import json
import re
from urllib.parse import quote_plus, urlsplit

from venture_intelligence.config import Settings, settings
from venture_intelligence.connectors.base import BaseConnector
from venture_intelligence.crawler import CrawlResult, StealthCrawler
from venture_intelligence.programs import ProgramRegistry
from venture_intelligence.shared.models import (
    CanonicalEvent,
    EntityType,
    EventType,
    Source,
    SourceAccessMode,
)


class PublicSearchConnector(BaseConnector):
    source: Source
    event_type: EventType
    endpoint: str
    confidence: float = 0.55
    result_entity_type: EntityType = EntityType.COMPANY

    def __init__(
        self,
        registry: ProgramRegistry,
        crawler: StealthCrawler | None = None,
        runtime_settings: Settings = settings,
    ) -> None:
        self.registry = registry
        self.crawler = crawler or StealthCrawler()
        self.settings = runtime_settings

    async def scan(self) -> list[CanonicalEvent]:
        # Without an entity identity, public search result pages are too noisy
        # to create evidence safely. Discovery is handled by configured program
        # sources; this connector corroborates a named target on demand.
        return []

    async def investigate(self, trigger: CanonicalEvent) -> list[CanonicalEvent]:
        name = str(
            trigger.raw_payload.get("name") or trigger.metadata.get("entity_name") or ""
        ).strip()
        if not name:
            return []
        # A company-name search cannot establish a founder. LinkedIn results
        # remain discovery-only until a typed person event is the trigger.
        if (
            self.event_type == EventType.FOUNDER_DETECTED
            and trigger.entity_type != EntityType.PERSON
        ):
            return []
        url = self.endpoint.format(query=quote_plus(name))
        result = await self._fetch(url)
        if not result.ok or not result.text or name.casefold() not in result.text.casefold():
            return []
        program = self.registry.get(str(trigger.metadata.get("program_id", "")))
        program = program or self.registry.resolve_program_for_text(result.text)
        if program is None:
            return []
        return [
            CanonicalEvent(
                **self.child_event(
                    trigger,
                    event_type=self.event_type,
                    entity_type=self.result_entity_type,
                    entity_id=self._result_entity_id(program.id, name),
                    source=self.source,
                    source_url=url,
                    confidence=self.confidence,
                    raw_payload={"name": name, "program_id": program.id},
                    metadata={"program_id": program.id, "evidence_tier": "environmental"},
                )
            )
        ]

    def _result_entity_id(self, program_id: str, name: str) -> str:
        """Create a stable source-shaped ID without reusing a company ID."""
        if self.result_entity_type == EntityType.COMPANY:
            return self.registry.entity_id(program_id, name)
        slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "result"
        return f"{self.source_name}__{slug}"

    async def _fetch(self, url: str) -> CrawlResult:
        return await self.crawler.fetch_text(url)

    def can_investigate(self, event: CanonicalEvent) -> bool:
        source = getattr(event.source, "value", event.source)
        return source != self.source_name and bool(
            event.raw_payload.get("name") or event.metadata.get("entity_name")
        )

    async def close(self) -> None:
        await self.crawler.close()


class GitHubConnector(PublicSearchConnector):
    source_name = Source.GITHUB.value
    source = Source.GITHUB
    event_type = EventType.REPO_CREATED
    endpoint = "https://api.github.com/search/repositories?q={query}"
    confidence = 0.62
    result_entity_type = EntityType.REPOSITORY

    def set_request_budget(self, tracker) -> None:
        self.request_budget = tracker

    async def investigate(self, trigger: CanonicalEvent) -> list[CanonicalEvent]:
        name = str(trigger.raw_payload.get("name") or "").strip()
        if not name:
            return []
        result = await self._fetch(self.endpoint.format(query=quote_plus(name)) + "&per_page=3")
        if not result.ok or not result.text:
            return []
        try:
            payload = json.loads(result.text)
        except ValueError:
            return []
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            return []
        events = []
        website = urlsplit(str(trigger.raw_payload.get("website") or "")).hostname
        for repo in payload["items"][:3]:
            if not isinstance(repo, dict) or repo.get("private") is not False:
                continue
            full_name = str(repo.get("full_name") or "")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name):
                continue
            url = "https://github.com/" + full_name
            if repo.get("html_url") != url:
                continue
            owner, repo_name = full_name.split("/")
            homepage = urlsplit(str(repo.get("homepage") or "")).hostname
            # Public company website and repository homepage provide a domain
            # cross-reference. A name/username search alone provides none.
            associated = bool(website and homepage and website.casefold() == homepage.casefold())
            events.append(
                CanonicalEvent(
                    **self.child_event(
                        trigger,
                        event_type=EventType.REPO_CREATED,
                        entity_type=EntityType.REPOSITORY,
                        entity_id=f"github__{owner}__{repo_name}",
                        source=Source.GITHUB,
                        source_url=url,
                        confidence=self.confidence,
                        raw_payload={
                            "owner": owner,
                            "repo": repo_name,
                            "name": full_name,
                            "stars": repo.get("stargazers_count", 0),
                            "language": repo.get("language"),
                            "homepage": repo.get("homepage"),
                            "description": repo.get("description"),
                            "created_at": repo.get("created_at"),
                            "pushed_at": repo.get("pushed_at"),
                        },
                        metadata={
                            "triggered_by": trigger.event_id,
                            "investigation_depth": trigger.metadata.get("investigation_depth", 0)
                            + 1,
                            "acquisition_url": result.url,
                            "related_entity_source_id": trigger.entity_id if associated else None,
                            "association_basis": "official_website_repository_homepage"
                            if associated
                            else None,
                            "association_url": trigger.source_url if associated else None,
                            "accelerator_membership_authority": False,
                        },
                    )
                )
            )
        return events

    async def _fetch(self, url: str) -> CrawlResult:
        token = self.settings.github_token
        if getattr(self, "request_budget", None) is not None:
            headers = {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            if token and token.get_secret_value().strip():
                headers["Authorization"] = f"Bearer {token.get_secret_value()}"
            return await self.crawler.fetch_text_budgeted(
                url, self.request_budget, self.source_name, headers=headers
            )
        if token is None or not token.get_secret_value().strip():
            return await self.crawler.fetch_text(url)
        return await self.crawler.fetch_text_authorized(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token.get_secret_value()}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )


class HackerNewsConnector(PublicSearchConnector):
    source_name = Source.HACKER_NEWS.value
    source = Source.HACKER_NEWS
    event_type = EventType.HN_MENTIONED
    endpoint = "https://hn.algolia.com/api/v1/search?query={query}"
    confidence = 0.58


class ArxivConnector(PublicSearchConnector):
    source_name = Source.ARXIV.value
    source = Source.ARXIV
    event_type = EventType.PAPER_PUBLISHED
    endpoint = "https://export.arxiv.org/api/query?search_query=all:{query}"
    confidence = 0.58
    result_entity_type = EntityType.PAPER


class ProductHuntConnector(PublicSearchConnector):
    """Credential-gated official Product Hunt GraphQL integration.

    Product Hunt evidence is a launch/company signal. It never establishes
    accelerator membership and never runs through a browser or HTML scraper.
    """

    source_name = Source.PRODUCT_HUNT.value
    source = Source.PRODUCT_HUNT
    event_type = EventType.PRODUCT_LAUNCHED
    endpoint = "https://api.producthunt.com/v2/api/graphql"
    confidence = 0.55

    def set_request_budget(self, tracker) -> None:
        self.request_budget = tracker

    async def scan(self) -> list[CanonicalEvent]:
        return []

    @property
    def configured(self) -> bool:
        token = self.settings.product_hunt_access_token
        return bool(
            self.settings.product_hunt_api_enabled
            and token is not None
            and token.get_secret_value().strip()
        )

    async def fetch_post(
        self,
        *,
        post_id: str | None = None,
        slug: str | None = None,
        target_name: str | None = None,
    ) -> tuple[dict | None, CrawlResult | None]:
        """Return one normalized official post without promoting an entity."""
        if not self.configured:
            return None, None
        if post_id or slug:
            selector = "id" if post_id else "slug"
            selector_type = "ID" if post_id else "String"
            query = f"""
                query ProductHuntPost($selector: {selector_type}!) {{
                  post({selector}: $selector) {{
                    id name slug tagline description url website createdAt featuredAt
                    makers {{ id name username headline url }}
                    topics(first: 10) {{ nodes {{ id name slug url }} }}
                  }}
                }}
            """
            variables = {"selector": post_id or slug}
            result_key = "post"
        else:
            query = """
                query ProductHuntRecentPosts {
                  posts(first: 20, order: NEWEST) {
                    nodes {
                      id name slug tagline description url website createdAt featuredAt
                      makers { id name username headline url }
                      topics(first: 10) { nodes { id name slug url } }
                    }
                  }
                }
            """
            variables = {}
            result_key = "posts"
        token = self.settings.product_hunt_access_token
        assert token is not None
        response = await self.crawler.post_json_text(
            self.endpoint,
            {"query": query, "variables": variables},
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token.get_secret_value()}",
            },
            request_budget=getattr(self, "request_budget", None),
            provider=self.source_name,
        )
        if not response.ok or not response.text:
            return None, response
        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError:
            return None, response
        if not isinstance(payload, dict) or payload.get("errors"):
            return None, response
        data = payload.get("data")
        if not isinstance(data, dict):
            return None, response
        selected = data.get(result_key)
        if result_key == "posts":
            nodes = selected.get("nodes", []) if isinstance(selected, dict) else []
            needle = (target_name or "").casefold().strip()
            selected = next(
                (
                    node
                    for node in nodes
                    if isinstance(node, dict)
                    and needle
                    and str(node.get("name") or "").casefold().strip() == needle
                ),
                None,
            )
        return (selected if isinstance(selected, dict) else None), response

    async def investigate(self, trigger: CanonicalEvent) -> list[CanonicalEvent]:
        if not self.configured or trigger.entity_type is not EntityType.COMPANY:
            return []
        name = str(
            trigger.raw_payload.get("name") or trigger.metadata.get("entity_name") or ""
        ).strip()
        if not name:
            return []
        post, response = await self.fetch_post(
            post_id=str(trigger.metadata.get("product_hunt_post_id") or "") or None,
            slug=str(trigger.metadata.get("product_hunt_slug") or "") or None,
            target_name=name,
        )
        if post is None or response is None:
            return []
        post_name = str(post.get("name") or "").strip()
        website = str(trigger.raw_payload.get("website") or "")
        destination = str(post.get("website") or "")
        if website or post_name.casefold() != name.casefold():
            # A versioned launch name may differ, but only an official API
            # website cross-reference can associate it with the known company.
            if not website or not destination:
                return []
            expected_host = urlsplit(website).hostname
            target_host = urlsplit(destination).hostname
            if target_host in {"producthunt.com", "www.producthunt.com"}:
                tracker = getattr(self, "request_budget", None)
                resolved = (
                    await self.crawler.fetch_text_budgeted(destination, tracker, self.source_name)
                    if tracker is not None else await self.crawler.fetch_text(destination)
                )
                if not resolved.ok:
                    return []
                destination = resolved.url
                target_host = urlsplit(resolved.url).hostname
            if not expected_host or target_host != expected_host:
                return []
        program = self.registry.get(
            str(trigger.metadata.get("program_id") or trigger.raw_payload.get("program_id") or "")
        )
        source_url = str(post.get("url") or "").strip()
        if not source_url.startswith("https://"):
            return []
        topics = post.get("topics", {})
        return [
            CanonicalEvent(
                **self.child_event(
                    trigger,
                    event_type=self.event_type,
                    entity_type=EntityType.COMPANY,
                    entity_id=trigger.entity_id,
                    source=self.source,
                    source_url=source_url,
                    confidence=self.confidence,
                    raw_payload={
                        "name": name,
                        "program_id": program.id if program else None,
                        "product_hunt_post": {
                            "id": post.get("id"),
                            "name": post.get("name"),
                            "slug": post.get("slug"),
                            "tagline": post.get("tagline"),
                            "description": post.get("description"),
                            "website": post.get("website"),
                            "resolved_website": destination,
                            "created_at": post.get("createdAt"),
                            "featured_at": post.get("featuredAt"),
                            "makers": post.get("makers") or [],
                            "topics": (topics.get("nodes", []) if isinstance(topics, dict) else []),
                        },
                    },
                    metadata={
                        "program_id": program.id if program else None,
                        "evidence_tier": "environmental",
                        "access_mode": "authorized_official_api",
                        "accelerator_membership_authority": False,
                    },
                )
            )
        ]

    def can_investigate(self, event: CanonicalEvent) -> bool:
        return (
            self.configured
            and event.entity_type is EntityType.COMPANY
            and super().can_investigate(event)
        )


class WellfoundConnector(PublicSearchConnector):
    source_name = Source.WELLFOUND.value
    source = Source.WELLFOUND
    event_type = EventType.JOB_POSTED
    endpoint = "https://wellfound.com/jobs?query={query}"
    confidence = 0.55


class LinkedInConnector(PublicSearchConnector):
    """Explicitly approved direct-public access only; disabled by default."""

    source_name = Source.LINKEDIN.value
    source = Source.LINKEDIN
    event_type = EventType.FOUNDER_DETECTED
    endpoint = "https://www.linkedin.com/search/results/people/?keywords={query}"
    confidence = 0.52
    result_entity_type = EntityType.PERSON

    async def investigate(self, trigger: CanonicalEvent) -> list[CanonicalEvent]:
        if not self._direct_public_web_enabled:
            return []
        return await super().investigate(trigger)

    def can_investigate(self, event: CanonicalEvent) -> bool:
        return self._direct_public_web_enabled and super().can_investigate(event)

    @property
    def _direct_public_web_enabled(self) -> bool:
        return self.settings.restricted_social_access_mode == SourceAccessMode.APPROVED_PUBLIC_WEB
