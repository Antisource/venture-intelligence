"""Free/public search discovery with structured source failures."""

from __future__ import annotations

import asyncio
import base64
import html
import json
import random
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import (
    parse_qs,
    parse_qsl,
    quote,
    unquote,
    urlencode,
    urlparse,
    urlsplit,
    urlunsplit,
)
from xml.etree import ElementTree

from pydantic import BaseModel, Field, model_validator

from venture_intelligence.crawler import CrawlResult, StealthCrawler
from venture_intelligence.programs import get_program_registry
from venture_intelligence.research.provider_policy import (
    ARXIV_POLICY,
    BING_POLICY,
    BRAVE_POLICY,
    DUCKDUCKGO_POLICY,
    GOOGLE_SCHOLAR_POLICY,
    PUBLIC_PROGRAM_OSINT_POLICY,
    RSS_POLICY,
    VC_WATCHLIST_POLICY,
    WAYBACK_POLICY,
    YAHOO_POLICY,
    ProviderPolicy,
    SourceOperationStatus,
    normalize_source_operation_status,
)
from venture_intelligence.research.query import scoped_query
from venture_intelligence.research.request_budget import RequestBudgetTracker
from venture_intelligence.research.watchlist import SourceStateStore, WatchlistEntry
from venture_intelligence.shared.models import (
    HistoricalWebObservation,
    ResearchSourceStatus,
    TemporalPrecision,
    WebsiteLifecycleSignal,
)


class _PublicTextParser(HTMLParser):
    """Extract document text without treating executable/template code as claims."""

    _NON_CONTENT = {"head", "script", "style", "template"}
    _BLOCKS = {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
        "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
        "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p",
        "pre", "section", "table", "td", "th", "tr", "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._NON_CONTENT:
            self.ignored.append(tag)
        elif not self.ignored and tag == "img":
            description = dict(attrs).get("alt")
            if description and description.strip():
                # Source-authored text, not image recognition or a verified identity.
                # Keep its origin explicit and never join it to adjacent name/role blocks.
                self.parts.extend(["; Image description:", description.strip(), ";"])
        elif not self.ignored and tag in self._BLOCKS:
            self.parts.append(";")

    def handle_endtag(self, tag: str) -> None:
        if self.ignored and self.ignored[-1] == tag:
            self.ignored.pop()
        elif not self.ignored and tag in self._BLOCKS:
            self.parts.append(";")

    def handle_data(self, data: str) -> None:
        if not self.ignored:
            self.parts.append(data)


def public_document_text(document: str) -> str:
    """Normalize public HTML before excerpt limits; never execute page scripts."""
    parser = _PublicTextParser()
    parser.feed(document)
    parser.close()
    text = re.sub(r"\s+", " ", " ".join(parser.parts))
    # Keep a hard clause boundary between layout blocks. Flattening it into a
    # space can turn a name + degree, or a heading + navigation, into a claim.
    return re.sub(r"(?:\s*;\s*)+", "; ", text).strip(" ;")


class SearchResult(BaseModel):
    """JSON-native result record returned by free search providers."""

    url: str
    title: str = ""
    snippet: str = ""
    source_domain: str = ""
    published_at: str | None = None
    discovery_method: str = "search_engine"
    direct_public: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    """JSON-native provider response; no provider-specific object leaks out."""

    query: str
    status: ResearchSourceStatus
    http_status: int | None = None
    results: list[SearchResult] = Field(default_factory=list)
    retrieved_at: str
    error: str | None = None
    attempts: int = 1
    retry_requests: int = 0
    redirect_requests: int = 0
    retryable: bool = False
    provider_domain: str | None = None
    from_cache: bool = False
    operation_status: SourceOperationStatus | None = None

    @model_validator(mode="after")
    def _normalize_operation_status(self) -> SearchResponse:
        if self.operation_status is None:
            self.operation_status = normalize_source_operation_status(
                self.status,
                has_results=bool(self.results),
                error=self.error,
            )
        return self


class SearchProvider(Protocol):
    domain: str
    policy: ProviderPolicy

    async def search(self, query: str, max_results: int) -> SearchResponse:
        """Return a JSON-compatible bounded search response."""


class _BudgetedProvider:
    """Mixin that reserves every real provider request from one shared budget."""

    domain: str

    def __init_budget__(self, budget_key: str | None = None) -> None:
        self._request_budget: RequestBudgetTracker | None = None
        self._budget_key = budget_key or self.domain

    def set_request_budget(self, tracker: RequestBudgetTracker) -> None:
        self._request_budget = tracker

    async def _reserve_request(self, *, domain: str | None = None, retry: bool = False) -> bool:
        if self._request_budget is None:
            return True
        return await self._request_budget.acquire(
            self._budget_key,
            domain or self.domain,
            retry=retry,
        )

    async def _fetch(
        self,
        crawler: Any,
        url: str,
        *,
        retry: bool = False,
    ) -> CrawlResult:
        """Use transport-level accounting when supported, with a fake-crawler fallback."""
        budgeted_fetch = getattr(crawler, "fetch_text_budgeted", None)
        if self._request_budget is not None and budgeted_fetch is not None:
            return await budgeted_fetch(
                url,
                self._request_budget,
                self._budget_key,
                retry=retry,
            )
        if not await self._reserve_request(
            domain=urlparse(url).netloc,
            retry=retry,
        ):
            return CrawlResult(
                url,
                None,
                None,
                self._request_budget.stop_reason
                if self._request_budget is not None
                else "request budget exhausted",
                source_status=ResearchSourceStatus.BUDGET_EXHAUSTED,
                original_url=url,
                request_made=False,
            )
        return await crawler.fetch_text(url)

    def _budget_response(
        self,
        query: str,
        attempts: int,
        *,
        results: list[SearchResult] | None = None,
        retry_requests: int = 0,
        redirect_requests: int = 0,
    ) -> SearchResponse:
        reason = (
            self._request_budget.stop_reason
            if self._request_budget is not None
            else "request budget exhausted"
        )
        return SearchResponse(
            query=query,
            status=ResearchSourceStatus.BUDGET_EXHAUSTED,
            results=results or [],
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=reason,
            attempts=attempts,
            retry_requests=retry_requests,
            redirect_requests=redirect_requests,
            retryable=False,
        )


class _DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._mode: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if "result__a" in classes:
            self._current = {"url": attributes.get("href") or "", "title": "", "snippet": ""}
            self._mode = "title"
        elif "result__snippet" in classes and self.results:
            self._current = self.results[-1]
            self._mode = "snippet"

    def handle_data(self, data: str) -> None:
        if self._current is None or self._mode is None:
            return
        key = self._mode
        self._current[key] = f"{self._current.get(key, '')}{data}"

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._current is None:
            return
        if self._mode == "title" and self._current not in self.results:
            self.results.append(self._current)
        self._current = None
        self._mode = None


class _PublicSearchParser(HTMLParser):
    """Conservative parser for public search-result anchors.

    It accepts only external HTTP(S) links with visible text. Provider navigation,
    tracking pixels, scripts, and relative links never become evidence candidates.
    """

    def __init__(self, provider_host: str) -> None:
        super().__init__()
        self.provider_host = provider_host.casefold()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._snippet_target: dict[str, str] | None = None
        self._snippet_tag: str | None = None
        self._in_heading = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").casefold()
        if tag in {"h2", "h3"}:
            self._in_heading = True
        if (
            tag in {"div", "p", "span"}
            and self.results
            and any(marker in classes for marker in ("snippet", "caption", "description"))
        ):
            self._snippet_target = self.results[-1]
            self._snippet_target.setdefault("snippet", "")
            self._snippet_tag = tag
        if tag != "a":
            return
        url = attributes.get("href") or ""
        if not url.startswith(("http://", "https://")):
            return
        self._current = {"url": url, "title": "", "headline": "1" if self._in_heading else ""}

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current["title"] += data
        elif self._snippet_target is not None:
            self._snippet_target["snippet"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h2", "h3"}:
            self._in_heading = False
        if tag == self._snippet_tag and self._snippet_target is not None:
            self._snippet_target["snippet"] = re.sub(
                r"\s+", " ", self._snippet_target["snippet"]
            ).strip()
            self._snippet_target = None
            self._snippet_tag = None
        if tag != "a" or self._current is None:
            return
        self._current["title"] = re.sub(r"\s+", " ", self._current["title"]).strip()
        if self._current["title"] and self._current not in self.results:
            self.results.append(self._current)
        self._current = None


def _unwrap_url(value: str) -> str:
    value = html.unescape(value)
    parsed = urlparse(value)
    if parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target) or value
    return value


def _domain(url: str) -> str:
    hostname = (urlparse(url).hostname or "").casefold()
    return hostname.removeprefix("www.")


def _acquired_result(result: SearchResult, provider_id: str) -> SearchResult:
    return result.model_copy(
        update={
            "metadata": {
                **result.metadata,
                "acquisition_provider": provider_id,
                "acquisition_providers": [provider_id],
                "evidence_origin": result.url,
            },
            "provenance": {
                **result.provenance,
                "acquisition_provider": provider_id,
                "evidence_origin": result.url,
            },
        }
    )


def _status_for_result(result: CrawlResult) -> ResearchSourceStatus:
    if result.source_status is not None:
        return result.source_status
    error = (result.error or "").casefold()
    if result.status_code == 202:
        return ResearchSourceStatus.TEMPORARILY_UNAVAILABLE
    if result.status_code == 401 or "login" in error or "authentication" in error:
        return ResearchSourceStatus.AUTH_REQUIRED
    if result.status_code == 403 or "captcha" in error or "forbidden" in error:
        return ResearchSourceStatus.BLOCKED
    if result.status_code == 429 or "rate" in error:
        return ResearchSourceStatus.RATE_LIMITED
    if result.status_code in {404, 410}:
        return ResearchSourceStatus.UNAVAILABLE
    if result.ok:
        return ResearchSourceStatus.AVAILABLE
    return ResearchSourceStatus.FAILED


class DuckDuckGoSearchProvider(_BudgetedProvider):
    """Public HTML search discovery; no API key, login, or paid provider."""

    domain = "duckduckgo.com"
    policy = DUCKDUCKGO_POLICY
    search_endpoint = "https://html.duckduckgo.com/html/?q={query}"

    def __init__(
        self,
        crawler: StealthCrawler | None = None,
        max_retries: int = 2,
        base_backoff_seconds: float = 0.25,
        budget_key: str | None = None,
    ) -> None:
        self.__init_budget__(budget_key)
        self.crawler = crawler or StealthCrawler()
        self.max_retries = max(0, min(max_retries, 5))
        self.base_backoff_seconds = max(0.0, base_backoff_seconds)

    async def search(self, query: str, max_results: int) -> SearchResponse:
        from urllib.parse import quote_plus

        url = self.search_endpoint.format(query=quote_plus(query))
        result = await self._fetch(self.crawler, url)
        attempts = int(result.request_made)
        retry_requests = 0
        redirect_requests = result.redirect_requests
        while (
            _status_for_result(result)
            in {
                ResearchSourceStatus.TEMPORARILY_UNAVAILABLE,
                ResearchSourceStatus.RATE_LIMITED,
            }
            and retry_requests < self.max_retries
        ):
            retry_after = _retry_after_seconds(result)
            delay = (
                retry_after
                if retry_after is not None
                else self.base_backoff_seconds * (2**retry_requests)
            )
            if delay:
                await asyncio.sleep(delay + random.uniform(0, min(0.25, delay / 2)))
            result = await self._fetch(self.crawler, url, retry=True)
            if result.request_made:
                attempts += 1
                retry_requests += 1
                redirect_requests += result.redirect_requests
            if result.source_status == ResearchSourceStatus.BUDGET_EXHAUSTED:
                break
        status = _status_for_result(result)
        if not result.ok or not result.text:
            return SearchResponse(
                query=query,
                status=status,
                http_status=result.status_code,
                results=[],
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                error=result.error,
                attempts=attempts,
                retry_requests=retry_requests,
                redirect_requests=redirect_requests,
                retryable=status
                in {
                    ResearchSourceStatus.TEMPORARILY_UNAVAILABLE,
                    ResearchSourceStatus.RATE_LIMITED,
                },
            )
        parser = _DuckDuckGoParser()
        parser.feed(result.text)
        results: list[SearchResult] = []
        for item in parser.results[:max_results]:
            url = _unwrap_url(item.get("url", ""))
            if url.startswith(("http://", "https://")):
                results.append(
                    _acquired_result(
                        SearchResult(
                            url=url,
                            title=re.sub(r"\s+", " ", html.unescape(item.get("title", ""))).strip(),
                            snippet=re.sub(
                                r"\s+", " ", html.unescape(item.get("snippet", ""))
                            ).strip(),
                            source_domain=_domain(url),
                        ),
                        self.policy.provider_id,
                    )
                )
        return SearchResponse(
            query=query,
            status=status,
            http_status=result.status_code,
            results=results,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=None,
            attempts=attempts,
            retry_requests=retry_requests,
            redirect_requests=redirect_requests,
        )

    async def close(self) -> None:
        await self.crawler.close()


class _PublicHtmlSearchProvider(_BudgetedProvider):
    """Shared bounded transport for credential-free public search result pages."""

    search_endpoint: str
    policy: ProviderPolicy

    def __init__(
        self,
        crawler: StealthCrawler | None = None,
        max_retries: int = 1,
        base_backoff_seconds: float = 0.25,
    ) -> None:
        self.__init_budget__(self.policy.provider_id)
        self.crawler = crawler or StealthCrawler()
        self.max_retries = max(0, min(max_retries, 3))
        self.base_backoff_seconds = max(0.0, base_backoff_seconds)

    def _unwrap_result_url(self, url: str) -> str:
        return url

    async def search(self, query: str, max_results: int) -> SearchResponse:
        from urllib.parse import quote_plus

        request_url = self.search_endpoint.format(query=quote_plus(query))
        result = await self._fetch(self.crawler, request_url)
        attempts = int(result.request_made)
        retries = 0
        redirects = result.redirect_requests
        while (
            _status_for_result(result)
            in {
                ResearchSourceStatus.TEMPORARILY_UNAVAILABLE,
                ResearchSourceStatus.RATE_LIMITED,
            }
            and retries < self.max_retries
        ):
            delay = self.base_backoff_seconds * (2**retries)
            if delay:
                await asyncio.sleep(delay)
            result = await self._fetch(self.crawler, request_url, retry=True)
            if result.request_made:
                attempts += 1
                retries += 1
                redirects += result.redirect_requests
            if result.source_status == ResearchSourceStatus.BUDGET_EXHAUSTED:
                break
        status = _status_for_result(result)
        if not result.ok or not result.text:
            return SearchResponse(
                query=query,
                status=status,
                http_status=result.status_code,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                error=result.error,
                attempts=attempts,
                retry_requests=retries,
                redirect_requests=redirects,
                retryable=status
                in {
                    ResearchSourceStatus.TEMPORARILY_UNAVAILABLE,
                    ResearchSourceStatus.RATE_LIMITED,
                },
                provider_domain=self.domain,
            )
        parser = _PublicSearchParser(self.domain)
        parser.feed(result.text)
        candidates: dict[str, dict[str, str]] = {}
        for item in parser.results:
            url = self._unwrap_result_url(html.unescape(item["url"]))
            if not url.startswith(("http://", "https://")):
                continue
            result_host = (urlparse(url).hostname or "").casefold()
            if result_host == self.domain or result_host.endswith(f".{self.domain}"):
                continue
            previous = candidates.get(url)
            if previous is None or (
                item.get("headline", ""), bool(item.get("snippet"))
            ) > (previous.get("headline", ""), bool(previous.get("snippet"))):
                candidates[url] = item
        # A result's breadcrumb may precede its heading/snippet with the same
        # URL. Deduplicate complete records before applying the result bound.
        results = [
            _acquired_result(
                SearchResult(
                    url=url,
                    title=html.unescape(item["title"]),
                    snippet=html.unescape(item.get("snippet", "")),
                    source_domain=_domain(url),
                ),
                self.policy.provider_id,
            )
            for url, item in list(candidates.items())[:max_results]
        ]
        return SearchResponse(
            query=query,
            status=status,
            http_status=result.status_code,
            results=results,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            attempts=attempts,
            retry_requests=retries,
            redirect_requests=redirects,
            provider_domain=self.domain,
        )

    async def close(self) -> None:
        await self.crawler.close()


class BingSearchProvider(_PublicHtmlSearchProvider):
    domain = "bing.com"
    policy = BING_POLICY
    search_endpoint = "https://www.bing.com/search?q={query}"

    def _unwrap_result_url(self, url: str) -> str:
        parsed = urlparse(url)
        encoded = parse_qs(parsed.query).get("u", [""])[0]
        if encoded.startswith("a1"):
            encoded = encoded[2:]
        if not encoded:
            return url
        try:
            padding = "=" * (-len(encoded) % 4)
            target = base64.urlsafe_b64decode(f"{encoded}{padding}").decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return url
        return target if target.startswith(("http://", "https://")) else url


class YahooSearchProvider(_PublicHtmlSearchProvider):
    domain = "search.yahoo.com"
    policy = YAHOO_POLICY
    search_endpoint = "https://search.yahoo.com/search?p={query}"

    def _unwrap_result_url(self, url: str) -> str:
        match = re.search(r"/RU=([^/]+)/RK=", url)
        return unquote(match.group(1)) if match else url


class BraveSearchProvider(_PublicHtmlSearchProvider):
    domain = "search.brave.com"
    policy = BRAVE_POLICY
    search_endpoint = "https://search.brave.com/search?q={query}&source=web"


class PublicProgramAnnouncementProvider:
    """Bounded indexed-public OSINT for accelerator announcements.

    This adapter queries DuckDuckGo's ordinary public HTML endpoint with
    site-restricted LinkedIn/X/Instagram searches. It never opens profiles,
    logs in, bypasses CAPTCHAs, or treats a social result as identity proof.
    Results remain search-discovered evidence with explicit OSINT provenance.
    """

    domain = "public-program-osint"
    policy = PUBLIC_PROGRAM_OSINT_POLICY

    def __init__(
        self,
        crawler: StealthCrawler | None = None,
        program_terms: tuple[str, ...] | None = None,
        max_scoped_queries: int = 4,
    ) -> None:
        self._provider = DuckDuckGoSearchProvider(
            crawler,
            max_retries=1,
            budget_key=self.domain,
        )
        configured_terms = program_terms or tuple(
            program.name for program in get_program_registry().active()
        )
        self.program_terms = tuple(item.strip() for item in configured_terms if item.strip())
        self.max_scoped_queries = max(1, min(max_scoped_queries, 6))

    def _scoped_queries(self, query: str) -> list[str]:
        scopes = (
            "site:linkedin.com",
            "site:x.com",
            "site:instagram.com",
            "site:threads.net",
        )
        queries = [
            scoped_query(
                scope,
                program,
                suffix="announcement",
                structured_query=query,
            )
            for program in self.program_terms
            for scope in scopes
        ]
        return queries[: self.max_scoped_queries]

    def set_request_budget(self, tracker: RequestBudgetTracker) -> None:
        self._provider.set_request_budget(tracker)

    async def search(self, query: str, max_results: int) -> SearchResponse:
        responses: list[SearchResponse] = []
        results: list[SearchResult] = []
        seen: set[str] = set()
        for scoped_query_text in self._scoped_queries(query):
            response = await self._provider.search(scoped_query_text, max_results)
            responses.append(response)
            if response.status == ResearchSourceStatus.BUDGET_EXHAUSTED:
                break
            for result in response.results:
                if result.url in seen:
                    continue
                seen.add(result.url)
                url = result.url.casefold()
                identity_signal = "/company/" in url or "/in/" in url
                metadata = {
                    **result.metadata,
                    "osint_layer": "indexed_public",
                    "announcement_query": scoped_query_text,
                    "identity_signal": identity_signal,
                }
                provenance = {
                    **result.provenance,
                    "adapter": "public-program-announcements",
                    "access_policy": "indexed_public_only",
                    "source_query": scoped_query_text,
                }
                results.append(
                    result.model_copy(update={"metadata": metadata, "provenance": provenance})
                )
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break
        successful = [item for item in responses if item.status == ResearchSourceStatus.AVAILABLE]
        selected = successful[0] if successful else (responses[0] if responses else None)
        budget_exhausted = any(
            item.status == ResearchSourceStatus.BUDGET_EXHAUSTED for item in responses
        )
        return SearchResponse(
            query=query,
            status=(
                ResearchSourceStatus.BUDGET_EXHAUSTED
                if budget_exhausted
                else (
                    ResearchSourceStatus.AVAILABLE
                    if successful
                    else (selected.status if selected else ResearchSourceStatus.UNAVAILABLE)
                )
            ),
            http_status=selected.http_status if selected else None,
            results=results[:max_results],
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=None
            if results
            else (selected.error if selected else "no indexed public announcements"),
            attempts=sum(item.attempts for item in responses),
            retry_requests=sum(item.retry_requests for item in responses),
            redirect_requests=sum(item.redirect_requests for item in responses),
            retryable=not bool(successful),
        )

    async def close(self) -> None:
        await self._provider.close()


def _retry_after_seconds(result: CrawlResult) -> float | None:
    value = result.headers.get("retry-after") or result.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, min(float(value), 10.0))
    except ValueError:
        return None


def _is_retryable_error(message: str) -> bool:
    lowered = message.casefold()
    return any(
        token in lowered for token in ("429", "202", "rate", "timeout", "temporarily", "503", "502")
    )


def _status_for_error(message: str) -> ResearchSourceStatus:
    lowered = message.casefold()
    if "429" in lowered or "rate" in lowered:
        return ResearchSourceStatus.RATE_LIMITED
    if "403" in lowered or "captcha" in lowered or "forbidden" in lowered:
        return ResearchSourceStatus.BLOCKED
    if "timeout" in lowered:
        return ResearchSourceStatus.UNAVAILABLE
    return ResearchSourceStatus.FAILED


def _iso_datetime(value: Any) -> str | None:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else None


def _canonical_arxiv_url(entry_id: str, short_id: str) -> str:
    value = entry_id or short_id
    if value.startswith("http"):
        value = value.replace("http://", "https://", 1)
        value = value.replace("/pdf/", "/abs/")
        return value.split("?", 1)[0].rstrip("/")
    return f"https://arxiv.org/abs/{value.strip('/')}"


def _canonical_scholar_url(value: str) -> str:
    value = html.unescape(value)
    if value.startswith("/"):
        value = f"https://scholar.google.com{value}"
    parts = urlsplit(value)
    query = [
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith(("utm_", "trk", "fbclid", "gclid"))
        and key.casefold() != "hl"
    ]
    return urlunsplit(
        (
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            parts.path.rstrip("/"),
            urlencode(query),
            "",
        )
    )


class ArxivSearchProvider(_BudgetedProvider):
    """Official arXiv API access through the maintained ``arxiv`` package."""

    domain = "arxiv.org"
    policy = ARXIV_POLICY
    api_url = "https://export.arxiv.org/api/query"

    def __init__(
        self,
        max_retries: int = 2,
        base_backoff_seconds: float = 0.5,
        page_delay_seconds: float = 3.0,
    ) -> None:
        self.__init_budget__()
        self.max_retries = max(0, min(max_retries, 5))
        self.base_backoff_seconds = max(0.0, base_backoff_seconds)
        self.page_delay_seconds = max(0.0, page_delay_seconds)

    async def search(self, query: str, max_results: int) -> SearchResponse:
        try:
            import arxiv
        except ImportError as error:  # pragma: no cover - dependency is declared
            return SearchResponse(
                query=query,
                status=ResearchSourceStatus.FAILED,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                error=f"arxiv package unavailable: {error}",
            )
        attempts = 0
        retry_requests = 0
        while True:
            is_retry = attempts > 0
            if not await self._reserve_request(
                domain="export.arxiv.org",
                retry=is_retry,
            ):
                return self._budget_response(
                    query,
                    attempts=attempts,
                    retry_requests=retry_requests,
                )
            attempts += 1
            if is_retry:
                retry_requests += 1
            try:
                papers = await asyncio.to_thread(self._search_sync, arxiv, query, max_results)
                retrieved_at = datetime.now(timezone.utc).isoformat()
                return SearchResponse(
                    query=query,
                    status=ResearchSourceStatus.AVAILABLE,
                    results=[self._result(paper, query, retrieved_at) for paper in papers],
                    retrieved_at=retrieved_at,
                    attempts=attempts,
                    retry_requests=retry_requests,
                )
            except Exception as error:
                message = str(error)[:500]
                retryable = _is_retryable_error(message)
                if not retryable or attempts > self.max_retries:
                    return SearchResponse(
                        query=query,
                        status=_status_for_error(message),
                        retrieved_at=datetime.now(timezone.utc).isoformat(),
                        error=message,
                        attempts=attempts,
                        retry_requests=retry_requests,
                        retryable=retryable,
                    )
                delay = self.base_backoff_seconds * (2 ** (attempts - 1))
                await asyncio.sleep(delay + random.uniform(0, min(0.25, delay / 2)))

    def _search_sync(self, arxiv_module: Any, query: str, max_results: int) -> list[Any]:
        bounded = max(1, min(max_results, 50))
        client = arxiv_module.Client(
            page_size=bounded, delay_seconds=self.page_delay_seconds, num_retries=0
        )
        search = arxiv_module.Search(
            query=query, max_results=bounded, sort_by=arxiv_module.SortCriterion.SubmittedDate
        )
        return list(client.results(search))

    def _result(self, paper: Any, query: str, retrieved_at: str) -> SearchResult:
        entry_id = str(getattr(paper, "entry_id", ""))
        canonical = _canonical_arxiv_url(entry_id, getattr(paper, "get_short_id", lambda: "")())
        authors = [str(getattr(author, "name", author)) for author in getattr(paper, "authors", [])]
        metadata = {
            "authors": authors,
            "abstract": " ".join(str(getattr(paper, "summary", "") or "").split()),
            "updated_at": _iso_datetime(getattr(paper, "updated", None)),
            "categories": list(getattr(paper, "categories", []) or []),
            "primary_category": getattr(paper, "primary_category", None),
            "journal_ref": getattr(paper, "journal_ref", None),
            "doi": getattr(paper, "doi", None),
            "comment": getattr(paper, "comment", None),
            "pdf_url": getattr(paper, "pdf_url", None),
        }
        return SearchResult(
            url=canonical,
            title=" ".join(str(getattr(paper, "title", "") or "").split()),
            snippet=metadata["abstract"],
            source_domain=self.domain,
            published_at=_iso_datetime(getattr(paper, "published", None)),
            discovery_method="public_api",
            direct_public=True,
            metadata=metadata,
            provenance={
                "provider": "arxiv.py",
                "api_url": self.api_url,
                "query": query,
                "entry_id": entry_id,
                "retrieved_at": retrieved_at,
            },
        )

    async def close(self) -> None:
        return None


class _GoogleScholarParser(HTMLParser):
    """Parse ordinary public result markup only; no browser or bypass logic."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._mode: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "h3" and "gs_rt" in classes:
            self._current = {"url": "", "title": "", "snippet": ""}
            self._mode = "title"
        elif tag == "a" and self._current is not None and self._mode == "title":
            self._current["url"] = html.unescape(attributes.get("href") or "")
        elif tag == "div" and "gs_rs" in classes and self.results:
            self._current = self.results[-1]
            self._mode = "snippet"

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._mode is not None:
            self._current[self._mode] = f"{self._current.get(self._mode, '')}{data}"

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3" and self._current is not None and self._mode == "title":
            if self._current.get("url"):
                self.results.append(self._current)
            self._current = None
            self._mode = None
        elif tag == "div" and self._mode == "snippet":
            self._current = None
            self._mode = None


class GoogleScholarSearchProvider(_BudgetedProvider):
    """Optional public Scholar discovery; blocked pages become source failures."""

    domain = "scholar.google.com"
    policy = GOOGLE_SCHOLAR_POLICY
    search_endpoint = "https://scholar.google.com/scholar?q={query}"

    def __init__(
        self,
        crawler: StealthCrawler | None = None,
        max_retries: int = 2,
        base_backoff_seconds: float = 0.25,
    ) -> None:
        self.__init_budget__()
        self.crawler = crawler or StealthCrawler()
        self.max_retries = max(0, min(max_retries, 5))
        self.base_backoff_seconds = max(0.0, base_backoff_seconds)

    async def search(self, query: str, max_results: int) -> SearchResponse:
        from urllib.parse import quote_plus

        url = self.search_endpoint.format(query=quote_plus(query))
        result = await self._fetch(self.crawler, url)
        attempts = int(result.request_made)
        retry_requests = 0
        redirect_requests = result.redirect_requests
        while (
            _status_for_result(result)
            in {ResearchSourceStatus.TEMPORARILY_UNAVAILABLE, ResearchSourceStatus.RATE_LIMITED}
            and retry_requests < self.max_retries
        ):
            retry_after = _retry_after_seconds(result)
            delay = (
                retry_after
                if retry_after is not None
                else self.base_backoff_seconds * (2**retry_requests)
            )
            if delay:
                await asyncio.sleep(delay + random.uniform(0, min(0.25, delay / 2)))
            result = await self._fetch(self.crawler, url, retry=True)
            if result.request_made:
                attempts += 1
                retry_requests += 1
                redirect_requests += result.redirect_requests
            if result.source_status == ResearchSourceStatus.BUDGET_EXHAUSTED:
                break
        status = _status_for_result(result)
        if not result.ok or not result.text:
            return SearchResponse(
                query=query,
                status=status,
                http_status=result.status_code,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                error=result.error or "Google Scholar public page unavailable",
                attempts=attempts,
                retry_requests=retry_requests,
                redirect_requests=redirect_requests,
                retryable=status
                in {
                    ResearchSourceStatus.TEMPORARILY_UNAVAILABLE,
                    ResearchSourceStatus.RATE_LIMITED,
                },
            )
        lowered = result.text.casefold()
        if any(marker in lowered for marker in ("captcha", "unusual traffic", "robot check")):
            return SearchResponse(
                query=query,
                status=ResearchSourceStatus.BLOCKED,
                http_status=result.status_code,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                error="Google Scholar public access blocked or rate-limited",
                attempts=attempts,
                retry_requests=retry_requests,
                redirect_requests=redirect_requests,
            )
        parser = _GoogleScholarParser()
        parser.feed(result.text)
        retrieved_at = datetime.now(timezone.utc).isoformat()
        items = [
            SearchResult(
                url=_canonical_scholar_url(item["url"]),
                title=" ".join(item["title"].split()),
                snippet=" ".join(item.get("snippet", "").split()),
                source_domain=self.domain,
                discovery_method="search_engine",
                direct_public=True,
                provenance={
                    "provider": "google-scholar-public-html",
                    "query": query,
                    "retrieved_at": retrieved_at,
                },
            )
            for item in parser.results[:max_results]
        ]
        return SearchResponse(
            query=query,
            status=ResearchSourceStatus.AVAILABLE,
            http_status=result.status_code,
            results=items,
            retrieved_at=retrieved_at,
            error=None if items else "no public Scholar results",
            attempts=attempts,
            retry_requests=retry_requests,
            redirect_requests=redirect_requests,
        )

    async def close(self) -> None:
        await self.crawler.close()


class WaybackSearchProvider(_BudgetedProvider):
    """Credential-free Internet Archive CDX discovery for historical pages."""

    domain = "web.archive.org"
    policy = WAYBACK_POLICY

    def __init__(self, crawler: StealthCrawler | None = None) -> None:
        self.__init_budget__()
        self.crawler = crawler or StealthCrawler()

    async def search(self, query: str, max_results: int) -> SearchResponse:
        endpoint = (
            "https://web.archive.org/cdx/search/cdx?url="
            f"{quote(query, safe='')}/*&output=json&filter=statuscode:200&"
            f"fl=timestamp,original,statuscode,mimetype&limit={max_results}"
        )
        result = await self._fetch(self.crawler, endpoint)
        status = _status_for_result(result)
        if not result.ok or not result.text:
            return SearchResponse(
                query=query,
                status=status,
                http_status=result.status_code,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                error=result.error,
                attempts=int(result.request_made),
                redirect_requests=result.redirect_requests,
            )
        try:
            rows = json.loads(result.text)
            records = rows[1:] if isinstance(rows, list) and rows else []
            results = []
            retrieved_at = datetime.now(timezone.utc)
            for row in records[:max_results]:
                if not isinstance(row, list) or len(row) < 2:
                    continue
                timestamp, original = row[0], row[1]
                archive_url = f"https://web.archive.org/web/{timestamp}/{original}"
                parsed = _wayback_timestamp(str(timestamp))
                if parsed is None:
                    continue
                snapshot_at, precision = parsed
                observation = HistoricalWebObservation(
                    original_url=original,
                    archive_url=archive_url,
                    snapshot_at=snapshot_at,
                    retrieved_at=retrieved_at,
                    precision=precision,
                    lifecycle_signals=_lifecycle_signals(original),
                    provenance={
                        "provider": "internet-archive-cdx",
                        "endpoint": endpoint,
                        "query": query,
                        "retrieved_at": retrieved_at.isoformat(),
                    },
                )
                results.append(
                    SearchResult(
                        url=archive_url,
                        title=original,
                        snippet=f"Archived at {snapshot_at.isoformat()}",
                        source_domain=self.domain,
                        published_at=snapshot_at.isoformat(),
                        discovery_method="public_api",
                        direct_public=True,
                        metadata={
                            "historical_observation": observation.model_dump(mode="json"),
                            "temporal_status": "historical",
                        },
                        provenance=observation.provenance,
                    )
                )
            return SearchResponse(
                query=query,
                status=ResearchSourceStatus.AVAILABLE,
                http_status=result.status_code,
                results=results,
                retrieved_at=retrieved_at.isoformat(),
                redirect_requests=result.redirect_requests,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            return SearchResponse(
                query=query,
                status=ResearchSourceStatus.FAILED,
                http_status=result.status_code,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                error=f"invalid wayback response: {error}",
                redirect_requests=result.redirect_requests,
            )

    async def close(self) -> None:
        await self.crawler.close()


def _lifecycle_signals(url: str) -> list[WebsiteLifecycleSignal]:
    """Classify only URL-path facts; content-change claims require page evidence."""
    path = urlsplit(url).path.casefold()
    signals = [WebsiteLifecycleSignal.WEBSITE_APPEARED]
    classifications = {
        WebsiteLifecycleSignal.TEAM_PAGE_OBSERVED: ("/team", "/about"),
        WebsiteLifecycleSignal.FOUNDER_PAGE_OBSERVED: ("/founder",),
        WebsiteLifecycleSignal.CAREERS_PAGE_OBSERVED: ("/career", "/jobs"),
        WebsiteLifecycleSignal.DOCUMENTATION_OBSERVED: ("/docs", "/documentation"),
        WebsiteLifecycleSignal.PRICING_PAGE_OBSERVED: ("/pricing",),
        WebsiteLifecycleSignal.CHANGELOG_OBSERVED: ("/changelog", "/releases"),
    }
    signals.extend(
        signal
        for signal, fragments in classifications.items()
        if any(fragment in path for fragment in fragments)
    )
    return signals


def _wayback_timestamp(
    value: str,
) -> tuple[datetime, TemporalPrecision] | None:
    formats = (
        ("%Y%m%d%H%M%S", TemporalPrecision.EXACT),
        ("%Y%m%d", TemporalPrecision.APPROXIMATE),
    )
    for timestamp_format, precision in formats:
        try:
            return (
                datetime.strptime(value, timestamp_format).replace(tzinfo=timezone.utc),
                precision,
            )
        except ValueError:
            continue
    return None


_FUNDING_TITLE = re.compile(
    r"^(?P<company>[A-Z][A-Za-z0-9&.' -]{1,70}?)\s+"
    r"(?:raises?|raised|secures?|secured|closes?|closed)\s+"
    r"(?:an?\s+)?"
    r"(?P<amount>[$€£]\s?[0-9]+(?:\.[0-9]+)?\s?(?:[KMB]|million|billion)?)",
    re.IGNORECASE,
)
_FOUNDED_BY = re.compile(
    r"\bfounded by\s+(?P<person>[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3})",
)
_LED_BY = re.compile(
    r"\bled by\s+(?P<investor>[A-Z][A-Za-z0-9&.' -]{1,80}?)"
    r"(?=\s+(?:with participation|alongside|and participation)\b|[.,;]|$)",
)
_ROUND = re.compile(r"\b(pre[- ]seed|seed|series\s+[a-z]|growth)\b", re.IGNORECASE)
_MONEY = r"(?P<amount>[$€£]\s?[0-9]+(?:\.[0-9]+)?\s?(?:[KMB]|million|billion)?)"
_TOTAL_FUNDING = re.compile(
    rf"\b(?:has\s+raised|raised)\s+{_MONEY}\s+(?:in\s+total|to\s+date)\b",
    re.IGNORECASE,
)
_TARGET_RAISE = re.compile(
    rf"\b(?:targeting|seeking|aiming\s+to\s+raise|plans?\s+to\s+raise)\s+(?:an?\s+)?{_MONEY}",
    re.IGNORECASE,
)
_VALUATION = re.compile(rf"\bvalued\s+at\s+{_MONEY}", re.IGNORECASE)
_FUND_SIZE = re.compile(
    rf"\b(?:fund|ventures|capital)\b.{{0,60}}?\b(?:raised|announced|closed)\b.{{0,20}}?{_MONEY}",
    re.IGNORECASE,
)
_REVENUE = re.compile(
    rf"\b(?:reached|hit|generated)\s+{_MONEY}\s+(?:ARR|annual recurring revenue|revenue)\b",
    re.IGNORECASE,
)
_CO_LED_BY = re.compile(
    r"\bco[- ]led by\s+(?P<investor>[A-Z][A-Za-z0-9&.' -]{1,80}?)(?=[.,;]|$)",
)
_PARTICIPATION_BY = re.compile(
    r"\b(?:participation from|participated in the round)\s+"
    r"(?P<investor>[A-Z][A-Za-z0-9&.' -]{1,80}?)(?=[.,;]|$)",
    re.IGNORECASE,
)
_BACKED_BY = re.compile(
    r"\bbacked by\s+(?P<investor>[A-Z][A-Za-z0-9&.' -]{1,80}?)(?=[.,;]|$)",
    re.IGNORECASE,
)
_INVESTED_IN = re.compile(
    r"\b(?P<investor>[A-Z][A-Za-z0-9&.' -]{1,80}?)\s+invested in\s+"
    r"(?P<company>[A-Z][A-Za-z0-9&.' -]{1,70}?)(?=[.,;]|$)",
)
_COMPANY_CLAIM = re.compile(
    r"^(?P<company>[A-Z][A-Za-z0-9&.' -]{1,70}?)\s+"
    r"(?:has raised|is targeting|is seeking|was valued|reached|hit|generated)\b"
)
_FUND_PARTICIPATED = re.compile(
    r"\b(?P<investor>[A-Z][A-Za-z0-9&.' -]{1,80}?)\s+participated in\s+"
    r"(?P<company>[A-Z][A-Za-z0-9&.' -]{1,70}?)['’]s round\b"
)
_INVESTORS_INCLUDE = re.compile(
    r"\binvestors include\s+(?P<investors>[A-Z][A-Za-z0-9&.' -]{1,100}?)"
    r"(?=[.;]|$)",
    re.IGNORECASE,
)
_PARTNER_MENTION = re.compile(
    r"\bPartner\s+(?P<partner>[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3})\b"
)
_PARTNER_LED = re.compile(
    r"\bPartner\s+(?P<partner>[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3})\s+"
    r"(?:led|co[- ]led)\s+(?P<company>[A-Z][A-Za-z0-9&.' -]{1,70}?)['’]s\s+"
    r"(?:pre[- ]seed|seed|series\s+[A-Z])\s+round\b"
)


def _normalized_round(text: str) -> str:
    match = _ROUND.search(text)
    if not match:
        return "UNKNOWN"
    value = re.sub(r"[-\s]+", " ", match.group(1)).title()
    return "Pre-Seed" if value.casefold() == "pre seed" else value


def extract_venture_metadata(
    title: str,
    text: str,
    *,
    roles: list[str],
    publication: str | None = None,
    author: str | None = None,
) -> dict[str, Any]:
    """Conservative structured extraction; never invents an identity from a keyword."""
    combined = re.sub(r"\s+", " ", f"{title} {text}").strip()
    metadata: dict[str, Any] = {
        "source_roles": roles,
        "publication": publication,
        "author": author,
    }
    funding = _FUNDING_TITLE.search(title.strip())
    if funding:
        company = funding.group("company").strip(" :-")
        if company:
            metadata.update(
                {
                    "candidate_kind": "company",
                    "company_name": company,
                    "funding_amount": funding.group("amount").replace(" ", ""),
                    "funding_claim_kind": "round_amount",
                    "funding_status": (
                        "closed"
                        if re.search(r"\b(?:closed|closes|secured|secures)\b", title, re.I)
                        else "announced"
                    ),
                }
            )
        metadata["round_type"] = _normalized_round(combined)
        metadata["funding_round"] = metadata["round_type"]
    claim_patterns = (
        (_TOTAL_FUNDING, "total_funding", "reported"),
        (_TARGET_RAISE, "target_raise", "targeting"),
        (_VALUATION, "valuation", "reported"),
        (_FUND_SIZE, "fund_size", "announced"),
        (_REVENUE, "revenue", "reported"),
    )
    for pattern, claim_kind, status in claim_patterns:
        claim_match = pattern.search(combined)
        if not claim_match:
            continue
        metadata.update(
            {
                "funding_amount": claim_match.group("amount").replace(" ", ""),
                "funding_claim_kind": claim_kind,
                "funding_status": status,
                "round_type": _normalized_round(combined),
            }
        )
        company_claim = _COMPANY_CLAIM.search(combined)
        if company_claim and claim_kind != "fund_size":
            metadata.setdefault("candidate_kind", "company")
            metadata.setdefault("company_name", company_claim.group("company").strip())
        if claim_kind != "round_amount":
            metadata.pop("funding_round", None)
        break
    founder = _FOUNDED_BY.search(combined)
    if founder and metadata.get("company_name"):
        metadata.update(
            {
                "candidate_kind": "person",
                "candidate_name": founder.group("person").strip(),
            }
        )
    investor_patterns = (
        (_CO_LED_BY, "co_led"),
        (_LED_BY, "led"),
        (_PARTICIPATION_BY, "participated"),
        (_BACKED_BY, "backed"),
    )
    for pattern, relationship in investor_patterns:
        investor = pattern.search(combined)
        if not investor:
            continue
        name = investor.group("investor").strip()
        metadata["investors"] = [name]
        metadata["investor_relationship"] = relationship
        if relationship in {"led", "co_led"}:
            metadata["lead_investor"] = name
        break
    invested = _INVESTED_IN.search(combined)
    if invested:
        metadata.setdefault("company_name", invested.group("company").strip())
        metadata.setdefault("candidate_kind", "company")
        metadata["investors"] = [invested.group("investor").strip()]
        metadata["investor_relationship"] = "invested"
    partner_led = _PARTNER_LED.search(combined)
    if partner_led:
        metadata.setdefault("company_name", partner_led.group("company").strip())
        metadata.setdefault("candidate_kind", "company")
        metadata["partner_name"] = partner_led.group("partner").strip()
        metadata["investors"] = [metadata["partner_name"]]
        metadata["lead_investor"] = metadata["partner_name"]
        metadata["investor_relationship"] = (
            "co_led" if "co-led" in partner_led.group(0).casefold() else "led"
        )
    participated = _FUND_PARTICIPATED.search(combined)
    if participated:
        metadata.setdefault("company_name", participated.group("company").strip())
        metadata.setdefault("candidate_kind", "company")
        metadata["investors"] = [participated.group("investor").strip()]
        metadata["investor_relationship"] = "participated"
    included = _INVESTORS_INCLUDE.search(combined)
    if included:
        investors = [
            value.strip()
            for value in re.split(r"\s+(?:and|&)\s+|,", included.group("investors"))
            if value.strip()
        ]
        if investors:
            metadata["investors"] = investors
            metadata["investor_relationship"] = "invested"
    partner = _PARTNER_MENTION.search(combined)
    if partner and "investors" not in metadata:
        metadata["partner_name"] = partner.group("partner").strip()
        metadata["investors"] = [metadata["partner_name"]]
        metadata["investor_relationship"] = "mentioned"
    return {key: value for key, value in metadata.items() if value not in (None, "", [])}


class RssSearchProvider(_BudgetedProvider):
    """Small RSS/Atom adapter for configured public feeds."""

    domain = "rss"
    policy = RSS_POLICY

    def __init__(
        self,
        feed_urls: list[str],
        crawler: StealthCrawler | None = None,
        *,
        provider_id: str = "rss",
        policy: ProviderPolicy = RSS_POLICY,
        state_store: SourceStateStore | None = None,
        source_metadata: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.domain = provider_id
        self.policy = policy
        self.__init_budget__(provider_id)
        self.feed_urls = list(dict.fromkeys(feed_urls))
        self.crawler = crawler or StealthCrawler()
        self.state_store = state_store
        self.source_metadata = source_metadata or {}
        self._items_cache: list[SearchResult] | None = None

    async def _load_items(self) -> tuple[list[SearchResult], int, int, bool, bool]:
        results: list[SearchResult] = []
        attempts = 0
        redirects = 0
        parsed_any = False
        budget_exhausted = False
        for feed_url in self.feed_urls:
            response = await self._fetch(self.crawler, feed_url)
            if response.request_made:
                attempts += 1
                redirects += response.redirect_requests
            if response.source_status == ResearchSourceStatus.BUDGET_EXHAUSTED:
                budget_exhausted = True
                break
            if not response.ok or not response.text:
                if self.state_store is not None:
                    self.state_store.record(feed_url, successful=False, item_keys=[])
                continue
            try:
                root = ElementTree.fromstring(response.text)
            except ElementTree.ParseError:
                if self.state_store is not None:
                    self.state_store.record(feed_url, successful=False, item_keys=[])
                continue
            parsed_any = True
            feed_results: list[SearchResult] = []
            source = self.source_metadata.get(feed_url, {})
            roles = [str(value) for value in source.get("roles", ["DISCOVERY", "TEMPORAL_NEWS"])]
            for item in root.iter():
                if item.tag.rsplit("}", 1)[-1] not in {"item", "entry"}:
                    continue
                values: dict[str, str] = {}
                for child in item:
                    key = child.tag.rsplit("}", 1)[-1]
                    value = (child.text or "").strip()
                    if key == "link" and not value:
                        value = dict(child.attrib).get("href", "")
                    values[key] = value
                link = values.get("link", "")
                if not link.startswith(("http://", "https://")):
                    continue
                published = (
                    values.get("pubDate") or values.get("published") or values.get("updated")
                )
                title = values.get("title", "")
                description = (
                    values.get("description") or values.get("summary") or values.get("content", "")
                )
                metadata = extract_venture_metadata(
                    title,
                    description,
                    roles=roles,
                    publication=str(source.get("organization") or source.get("name") or "") or None,
                    author=values.get("author") or values.get("creator") or None,
                )
                feed_results.append(
                    _acquired_result(
                        SearchResult(
                            url=link,
                            title=title,
                            snippet=re.sub(r"<[^>]+>", " ", description)[:2_000],
                            source_domain=_domain(link),
                            published_at=published or None,
                            discovery_method="rss",
                            direct_public=True,
                            metadata={
                                **metadata,
                                "feed_url": feed_url,
                                "watchlist_id": source.get("id"),
                            },
                            provenance={"feed_url": feed_url, "access_method": "rss"},
                        ),
                        self.policy.provider_id,
                    )
                )
            keys = [item.url for item in feed_results]
            previous = self.state_store.load().get(feed_url) if self.state_store else None
            seen = set(previous.duplicate_item_keys if previous else [])
            if self.state_store is not None:
                self.state_store.record(
                    feed_url,
                    successful=True,
                    item_keys=keys,
                    newest_item_at=next(
                        (item.published_at for item in feed_results if item.published_at),
                        None,
                    ),
                    content=response.text,
                )
            results.extend(item for item in feed_results if item.url not in seen)
        return results, attempts, redirects, parsed_any, budget_exhausted

    async def search(self, query: str, max_results: int) -> SearchResponse:
        if self._items_cache is None:
            (
                items,
                attempts,
                redirect_requests,
                parsed_any,
                budget_exhausted,
            ) = await self._load_items()
            self._items_cache = items
        else:
            items = self._items_cache
            attempts = 0
            redirect_requests = 0
            parsed_any = True
            budget_exhausted = False
        quoted = [value.casefold() for value in re.findall(r'"([^"]+)"', query)]
        terms = [value for value in quoted if len(value) > 1]
        if re.search(r"\bOR\b", query):
            # This is a recall-oriented feed prefilter, not a search engine.
            # A quoted alternative must not erase the unquoted alternatives.
            # Identity-only quoted lookups retain their existing exact match.
            unquoted = re.sub(r'"[^"]+"|\bsite:\S+', " ", query)
            terms.extend(
                value.casefold()
                for value in re.findall(r"[\w-]+", unquoted)
                if len(value) > 1 and value not in {"OR", "AND", "NOT"}
            )
        results = [
            item
            for item in items
            if not terms or any(term in f"{item.title} {item.snippet}".casefold() for term in terms)
        ][:max_results]
        if budget_exhausted:
            return self._budget_response(
                query,
                attempts=attempts,
                results=results,
                redirect_requests=redirect_requests,
            )
        return SearchResponse(
            query=query,
            status=ResearchSourceStatus.AVAILABLE
            if parsed_any
            else ResearchSourceStatus.UNAVAILABLE,
            results=results,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=None if parsed_any else "no configured RSS/Atom feed could be parsed",
            attempts=attempts,
            redirect_requests=redirect_requests,
            provider_domain=self.domain,
        )

    async def close(self) -> None:
        await self.crawler.close()


class PublicWatchlistPageProvider(_BudgetedProvider):
    """Bounded incremental fetcher for configured public VC/partner pages."""

    domain = "vc_watchlist"
    policy = VC_WATCHLIST_POLICY

    def __init__(
        self,
        entries: list[WatchlistEntry],
        crawler: StealthCrawler | None = None,
        *,
        state_store: SourceStateStore | None = None,
    ) -> None:
        self.__init_budget__(self.policy.provider_id)
        self.entries = [item for item in entries if item.public_urls]
        self.crawler = crawler or StealthCrawler()
        self.state_store = state_store
        self._cache: list[SearchResult] | None = None

    async def search(self, query: str, max_results: int) -> SearchResponse:
        attempts = 0
        redirects = 0
        parsed = False
        if self._cache is None:
            self._cache = []
            for entry in self.entries:
                for url in entry.public_urls:
                    response = await self._fetch(self.crawler, url)
                    if response.request_made:
                        attempts += 1
                        redirects += response.redirect_requests
                    if response.source_status == ResearchSourceStatus.BUDGET_EXHAUSTED:
                        return self._budget_response(
                            query,
                            attempts,
                            results=self._cache[:max_results],
                            redirect_requests=redirects,
                        )
                    if not response.ok or not response.text:
                        if self.state_store:
                            self.state_store.record(url, successful=False, item_keys=[])
                        continue
                    parsed = True
                    text = public_document_text(response.text)
                    previous = self.state_store.load().get(url) if self.state_store else None
                    digest = json.dumps({"url": url, "text": text[:10_000]}, sort_keys=True)
                    if self.state_store:
                        current = self.state_store.record(
                            url,
                            successful=True,
                            item_keys=[url],
                            content=digest,
                        )
                        changed = previous is None or previous.content_hash != current.content_hash
                    else:
                        changed = True
                    if not changed:
                        continue
                    metadata = extract_venture_metadata(
                        entry.name,
                        text,
                        roles=entry.roles,
                        publication=entry.organization,
                    )
                    self._cache.append(
                        _acquired_result(
                            SearchResult(
                                url=url,
                                title=entry.name,
                                snippet=text[:2_000],
                                source_domain=_domain(url),
                                discovery_method="public_watchlist",
                                direct_public=True,
                                metadata={
                                    **metadata,
                                    "watchlist_id": entry.id,
                                    "watchlist_kind": entry.kind,
                                    "organization": entry.organization,
                                },
                                provenance={"watchlist_id": entry.id},
                            ),
                            self.policy.provider_id,
                        )
                    )
        else:
            parsed = True
        return SearchResponse(
            query=query,
            status=ResearchSourceStatus.AVAILABLE if parsed else ResearchSourceStatus.UNAVAILABLE,
            results=self._cache[:max_results],
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=None if parsed else "no configured public watchlist page was available",
            attempts=attempts,
            redirect_requests=redirects,
            provider_domain=self.domain,
        )

    async def close(self) -> None:
        await self.crawler.close()


class CompositeSearchProvider:
    """Bounded fan-out over free providers with deterministic URL deduplication."""

    domain = "multi-source"

    def __init__(self, providers: list[SearchProvider]) -> None:
        self.providers = list(providers)

    def set_request_budget(self, tracker: RequestBudgetTracker) -> None:
        for provider in self.providers:
            setter = getattr(provider, "set_request_budget", None)
            if setter is not None:
                setter(tracker)

    async def search(self, query: str, max_results: int) -> SearchResponse:
        responses = await asyncio.gather(
            *(provider.search(query, max_results) for provider in self.providers),
            return_exceptions=True,
        )
        usable: list[SearchResponse] = [
            item for item in responses if isinstance(item, SearchResponse)
        ]
        results: list[SearchResult] = []
        seen: set[str] = set()
        for response in usable:
            for result in response.results:
                if result.url not in seen:
                    seen.add(result.url)
                    results.append(result)
                if len(results) >= max_results:
                    break
        successful = [item for item in usable if item.status == ResearchSourceStatus.AVAILABLE]
        selected = successful[0] if successful else (usable[0] if usable else None)
        return SearchResponse(
            query=query,
            status=ResearchSourceStatus.AVAILABLE
            if successful
            else (selected.status if selected else ResearchSourceStatus.FAILED),
            http_status=selected.http_status if selected else None,
            results=results,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=None if successful else (selected.error if selected else "all providers failed"),
            attempts=sum(item.attempts for item in usable) or 1,
            retry_requests=sum(item.retry_requests for item in usable),
            redirect_requests=sum(item.redirect_requests for item in usable),
            retryable=not bool(successful),
        )

    async def close(self) -> None:
        for provider in self.providers:
            close = getattr(provider, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result


__all__ = [
    "DuckDuckGoSearchProvider",
    "BingSearchProvider",
    "YahooSearchProvider",
    "BraveSearchProvider",
    "ArxivSearchProvider",
    "GoogleScholarSearchProvider",
    "WaybackSearchProvider",
    "RssSearchProvider",
    "PublicWatchlistPageProvider",
    "CompositeSearchProvider",
    "SearchProvider",
    "SearchResponse",
    "SearchResult",
    "_domain",
    "_status_for_result",
    "extract_venture_metadata",
]
