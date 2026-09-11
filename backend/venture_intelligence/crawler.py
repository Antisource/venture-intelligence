"""Bounded public-web fetching shared by environmental connectors.

The historical name ``StealthCrawler`` is retained as an interface name only.
It is not an access-control evasion tool: it makes ordinary, rate-limited HTTP
requests to public URLs and reports failures instead of fabricating results.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import time
from collections.abc import Iterable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from itertools import count
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

import httpx

from venture_intelligence.config import Settings, settings
from venture_intelligence.observability.metrics import metrics
from venture_intelligence.shared.models import ResearchSourceStatus, SourceAccessMode

if TYPE_CHECKING:
    from venture_intelligence.research.request_budget import RequestBudgetTracker

logger = logging.getLogger(__name__)
_AUTH_SENSITIVE_HOSTS = {
    "linkedin.com",
    "www.linkedin.com",
    "instagram.com",
    "www.instagram.com",
    "x.com",
    "www.x.com",
    "threads.net",
    "www.threads.net",
    "producthunt.com",
    "www.producthunt.com",
    "medium.com",
    "www.medium.com",
}
_UNSAFE_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
    "host.docker.internal",
}
_MAX_REDIRECTS = 3
_RESTRICTED_SOCIAL_HOSTS = {
    "linkedin.com",
    "x.com",
    "instagram.com",
    "threads.net",
}
_ACTIVE_EVIDENCE_OPERATION: ContextVar[tuple[int, str] | None] = ContextVar(
    "active_evidence_operation",
    default=None,
)


@dataclass(frozen=True)
class CrawlResult:
    url: str
    text: str | None
    status_code: int | None
    error: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    source_status: ResearchSourceStatus | None = None
    original_url: str | None = None
    redirect_requests: int = 0
    request_made: bool = True

    @property
    def ok(self) -> bool:
        status = self.source_status
        if status is None:
            status = (
                ResearchSourceStatus.AVAILABLE
                if self.status_code is not None
                and 200 <= self.status_code < 300
                and self.status_code != 202
                else ResearchSourceStatus.FAILED
            )
        return (
            status == ResearchSourceStatus.AVAILABLE
            and self.text is not None
            and self.error is None
        )


@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: float | None = None


class StealthCrawler:
    """Shared, transparent HTTP lifecycle for public environmental sources."""

    def __init__(self, runtime_settings: Settings = settings) -> None:
        self._settings = runtime_settings
        self._client: httpx.AsyncClient | None = None
        self._circuits: dict[str, _CircuitState] = {}
        self.request_count = 0
        self.request_outcomes: list[dict[str, object]] = []
        self._request_outcome_sequence = 0
        self._operation_sequence = count(1)
        self._operation_outcomes: dict[str, list[dict[str, object]]] = {}
        self._attributed_request_ids: set[int] = set()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._settings.connector_timeout_seconds,
                headers={"User-Agent": self._settings.user_agent},
                # Redirects are followed manually so every destination gets
                # the same public-target validation as the initial URL.
                follow_redirects=False,
            )
        return self._client

    async def fetch_text(self, url: str) -> CrawlResult:
        return await self._fetch_text(
            url,
            redirect_count=0,
            original_url=url,
            redirect_requests=0,
        )

    async def fetch_text_authorized(
        self,
        url: str,
        *,
        headers: dict[str, str],
    ) -> CrawlResult:
        """Fetch one public API URL with same-origin-only authorization headers."""
        return await self._fetch_text(
            url,
            redirect_count=0,
            original_url=url,
            redirect_requests=0,
            request_headers=headers,
            header_origin_host=_canonical_host_key(urlparse(url)),
        )

    async def post_json_text(
        self,
        url: str,
        payload: dict[str, object],
        *,
        headers: dict[str, str],
        request_budget: RequestBudgetTracker | None = None,
        provider: str | None = None,
    ) -> CrawlResult:
        """POST bounded JSON to a validated public API endpoint."""
        return await self._fetch_text(
            url,
            redirect_count=0,
            original_url=url,
            redirect_requests=0,
            request_headers=headers,
            header_origin_host=_canonical_host_key(urlparse(url)),
            request_method="POST",
            request_json=payload,
            request_budget=request_budget,
            budget_provider=provider,
        )

    async def fetch_text_budgeted(
        self,
        url: str,
        request_budget: RequestBudgetTracker,
        provider: str,
        *,
        retry: bool = False,
        headers: dict[str, str] | None = None,
    ) -> CrawlResult:
        """Fetch while reserving the shared budget at the transport boundary."""
        return await self._fetch_text(
            url,
            redirect_count=0,
            original_url=url,
            redirect_requests=0,
            request_budget=request_budget,
            budget_provider=provider,
            retry=retry,
            request_headers=headers,
            header_origin_host=_canonical_host_key(urlparse(url)),
        )

    async def _fetch_text(
        self,
        url: str,
        *,
        redirect_count: int,
        original_url: str,
        redirect_requests: int,
        request_budget: RequestBudgetTracker | None = None,
        budget_provider: str | None = None,
        retry: bool = False,
        restricted_origin_approved: bool | None = None,
        request_headers: dict[str, str] | None = None,
        header_origin_host: str | None = None,
        request_method: str = "GET",
        request_json: dict[str, object] | None = None,
    ) -> CrawlResult:
        started = time.monotonic()
        parsed = urlparse(url)
        host_key = _canonical_host_key(parsed)
        if restricted_origin_approved is None:
            restricted_origin_approved = (
                _is_restricted_social_target(parsed)
                and self._settings.restricted_social_access_mode
                == SourceAccessMode.APPROVED_PUBLIC_WEB
            )
        target_error = _public_target_error(parsed)
        if target_error:
            result = CrawlResult(
                url,
                None,
                None,
                target_error,
                source_status=ResearchSourceStatus.FAILED,
                original_url=original_url,
                redirect_requests=redirect_requests,
                request_made=redirect_count > 0,
            )
            return self._finish(
                result,
                host_key,
                started,
                transport_success=False,
                request_made=False,
            )
        if (
            redirect_count > 0
            and not restricted_origin_approved
            and _is_restricted_social_target(parsed)
        ):
            return self._finish(
                CrawlResult(
                    url,
                    None,
                    None,
                    "restricted social redirect blocked by source policy",
                    source_status=ResearchSourceStatus.BLOCKED,
                    original_url=original_url,
                    redirect_requests=redirect_requests,
                    request_made=True,
                ),
                host_key,
                started,
                transport_success=False,
                request_made=False,
            )
        circuit = self._circuits.setdefault(host_key, _CircuitState())
        if circuit.opened_at is not None:
            if (
                time.monotonic() - circuit.opened_at
                < self._settings.crawler_circuit_cooldown_seconds
            ):
                metrics.set_circuit_state(host_key, "open")
                result = CrawlResult(
                    url,
                    None,
                    None,
                    "circuit open for host after repeated failures",
                    source_status=ResearchSourceStatus.UNAVAILABLE,
                    original_url=original_url,
                    redirect_requests=redirect_requests,
                    request_made=redirect_count > 0,
                )
                return self._finish(
                    result,
                    host_key,
                    started,
                    transport_success=False,
                    request_made=False,
                )
            circuit.failures = 0
            circuit.opened_at = None
        if request_budget is not None:
            reserved = await request_budget.acquire(
                budget_provider or host_key,
                host_key,
                retry=retry and redirect_count == 0,
                redirect=redirect_count > 0,
            )
            if not reserved:
                result = CrawlResult(
                    url,
                    None,
                    None,
                    request_budget.stop_reason or "request budget exhausted",
                    source_status=ResearchSourceStatus.BUDGET_EXHAUSTED,
                    original_url=original_url,
                    redirect_requests=redirect_requests,
                    request_made=redirect_count > 0,
                )
                return self._finish(
                    result,
                    host_key,
                    started,
                    transport_success=False,
                    request_made=False,
                )
        if redirect_count > 0:
            redirect_requests += 1
        try:
            self.request_count += 1
            safe_headers = (
                request_headers
                if header_origin_host is None or header_origin_host == host_key
                else None
            )
            client = await self._get_client()
            if request_method == "POST":
                response = await client.post(url, headers=safe_headers, json=request_json)
            else:
                response = await client.get(url, headers=safe_headers)
            if response.status_code in {301, 302, 307, 308}:
                metrics.record_source(
                    host_key,
                    response.status_code,
                    None,
                    (time.monotonic() - started) * 1000,
                    transport_success=True,
                    usable_success=False,
                    source_status="REDIRECT",
                )
                location = response.headers.get("location")
                if not location:
                    self._mark_failure(host_key, circuit)
                    result = CrawlResult(
                        url,
                        None,
                        response.status_code,
                        "redirect response missing location",
                        dict(response.headers),
                        ResearchSourceStatus.FAILED,
                        original_url,
                        redirect_requests,
                    )
                    return self._remember(result)
                if redirect_count >= _MAX_REDIRECTS:
                    self._mark_failure(host_key, circuit)
                    result = CrawlResult(
                        url,
                        None,
                        response.status_code,
                        "redirect limit exceeded",
                        dict(response.headers),
                        ResearchSourceStatus.FAILED,
                        original_url,
                        redirect_requests,
                    )
                    return self._remember(result)
                target = urljoin(url, location)
                next_method = request_method if response.status_code in {307, 308} else "GET"
                return await self._fetch_text(
                    target,
                    redirect_count=redirect_count + 1,
                    original_url=original_url,
                    redirect_requests=redirect_requests,
                    request_budget=request_budget,
                    budget_provider=budget_provider,
                    retry=retry,
                    restricted_origin_approved=restricted_origin_approved,
                    request_headers=request_headers,
                    header_origin_host=header_origin_host,
                    request_method=next_method,
                    request_json=request_json if next_method == "POST" else None,
                )
            response.raise_for_status()
            if response.status_code == 202:
                self._mark_failure(host_key, circuit)
                result = CrawlResult(
                    str(response.url),
                    None,
                    response.status_code,
                    "HTTP 202 accepted without usable evidence",
                    dict(response.headers),
                    ResearchSourceStatus.TEMPORARILY_UNAVAILABLE,
                    original_url,
                    redirect_requests,
                )
                return self._finish(
                    result,
                    host_key,
                    started,
                    transport_success=True,
                )
            content = response.content[: self._settings.max_response_bytes]
            text = content.decode(response.encoding or "utf-8", errors="replace")
            final_url = str(response.url)
            if _is_auth_wall(host_key, final_url, text):
                self._mark_failure(host_key, circuit)
                result = CrawlResult(
                    final_url,
                    None,
                    response.status_code,
                    "authentication required/login redirect",
                    dict(response.headers),
                    ResearchSourceStatus.AUTH_REQUIRED,
                    original_url,
                    redirect_requests,
                )
                return self._finish(
                    result,
                    host_key,
                    started,
                    transport_success=True,
                )
            if not text.strip():
                self._mark_failure(host_key, circuit)
                result = CrawlResult(
                    final_url,
                    None,
                    response.status_code,
                    "empty response body",
                    dict(response.headers),
                    ResearchSourceStatus.FAILED,
                    original_url,
                    redirect_requests,
                )
                return self._finish(
                    result,
                    host_key,
                    started,
                    transport_success=True,
                )
            circuit.failures = 0
            result = CrawlResult(
                final_url,
                text,
                response.status_code,
                None,
                dict(response.headers),
                ResearchSourceStatus.AVAILABLE,
                original_url,
                redirect_requests,
            )
            metrics.set_circuit_state(host_key, "closed")
            return self._finish(
                result,
                host_key,
                started,
                transport_success=True,
            )
        except httpx.HTTPError as exc:
            self._mark_failure(host_key, circuit)
            logger.info("public crawl failed url=%s error=%s", url, exc)
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
            source_status = (
                ResearchSourceStatus.UNAVAILABLE
                if isinstance(exc, httpx.TimeoutException)
                else _status_for_http_failure(status_code, str(exc))
            )
            result = CrawlResult(
                url,
                None,
                status_code,
                str(exc),
                source_status=source_status,
                original_url=original_url,
                redirect_requests=redirect_requests,
            )
            return self._finish(
                result,
                host_key,
                started,
                transport_success=isinstance(exc, httpx.HTTPStatusError),
            )

    def _mark_failure(self, host: str, circuit: _CircuitState) -> None:
        circuit.failures += 1
        if circuit.failures >= self._settings.crawler_failure_threshold:
            circuit.opened_at = time.monotonic()
            metrics.set_circuit_state(host, "open")

    def _finish(
        self,
        result: CrawlResult,
        host: str,
        started: float,
        *,
        transport_success: bool,
        request_made: bool = True,
    ) -> CrawlResult:
        metrics.record_source(
            host,
            result.status_code,
            None,
            (time.monotonic() - started) * 1000,
            transport_success=transport_success,
            usable_success=result.ok,
            source_status=(result.source_status or ResearchSourceStatus.FAILED).value,
            request_made=request_made,
        )
        return self._remember(result)

    def _remember(self, result: CrawlResult) -> CrawlResult:
        self._request_outcome_sequence += 1
        active = _ACTIVE_EVIDENCE_OPERATION.get()
        operation_id = active[1] if active and active[0] == id(self) else None
        outcome: dict[str, object] = {
            "request_id": self._request_outcome_sequence,
            "operation_id": operation_id,
            "url": result.url,
            "original_url": result.original_url,
            "status_code": result.status_code,
            "source_status": (result.source_status or ResearchSourceStatus.FAILED).value,
            "error": result.error,
            "usable": result.ok,
            "redirect_requests": result.redirect_requests,
            "request_made": result.request_made,
        }
        self.request_outcomes.append(outcome)
        if operation_id is not None:
            self._operation_outcomes.setdefault(operation_id, []).append(outcome)
        self.request_outcomes = self.request_outcomes[-100:]
        return result

    def begin_evidence_operation(
        self,
    ) -> tuple[str, Token[tuple[int, str] | None]]:
        """Start task-local request attribution for one connector operation."""
        operation_id = f"{id(self):x}-{next(self._operation_sequence)}"
        self._operation_outcomes[operation_id] = []
        token = _ACTIVE_EVIDENCE_OPERATION.set((id(self), operation_id))
        return operation_id, token

    @staticmethod
    def end_evidence_operation(token: Token[tuple[int, str] | None]) -> None:
        """End task-local attribution without consuming the recorded outcomes."""
        _ACTIVE_EVIDENCE_OPERATION.reset(token)

    def record_evidence_outcome(
        self,
        evidence_urls: Iterable[str],
        *,
        operation_id: str | None = None,
    ) -> None:
        """Record evidence outcomes for one explicit operation or legacy caller."""
        urls = list(evidence_urls)
        evidence_hosts = {_canonical_host_key(urlparse(url)) for url in urls if url}
        if operation_id is not None:
            pending = self._operation_outcomes.pop(operation_id, [])
        else:
            pending = [
                item
                for item in self.request_outcomes
                if int(item.get("request_id") or 0) not in self._attributed_request_ids
            ]
            self._attributed_request_ids.update(
                int(item.get("request_id") or 0) for item in pending
            )
        usable_hosts = {
            _canonical_host_key(urlparse(str(item.get("url") or "")))
            for item in pending
            if item.get("usable")
        }
        for host in sorted(item for item in usable_hosts if item):
            metrics.record_evidence_outcome(host, host in evidence_hosts)

    async def fetch_rendered_text(
        self,
        url: str,
        *,
        request_budget: RequestBudgetTracker | None = None,
        provider: str = "rendered-public-web",
        retry: bool = False,
    ) -> CrawlResult:
        """Render one explicitly allowed top-level page without subresources.

        Browser redirects and subresource requests are denied because they
        cannot be charged reliably against the shared transport budget.
        """
        started = time.monotonic()
        parsed = urlparse(url)
        host_key = _canonical_host_key(parsed)
        restricted_social_approved = (
            self._settings.restricted_social_access_mode == SourceAccessMode.APPROVED_PUBLIC_WEB
        )
        target_error = _public_target_error(parsed)
        if target_error:
            return self._finish(
                CrawlResult(
                    url,
                    None,
                    None,
                    target_error,
                    source_status=ResearchSourceStatus.FAILED,
                    original_url=url,
                    request_made=False,
                ),
                host_key,
                started,
                transport_success=False,
                request_made=False,
            )
        if not restricted_social_approved and _is_restricted_social_target(parsed):
            return self._finish(
                CrawlResult(
                    url,
                    None,
                    None,
                    "restricted social rendered access is disabled by policy",
                    source_status=ResearchSourceStatus.BLOCKED,
                    original_url=url,
                    request_made=False,
                ),
                host_key,
                started,
                transport_success=False,
                request_made=False,
            )
        if not self._settings.crawler_render_enabled:
            if request_budget is not None:
                return await self.fetch_text_budgeted(
                    url,
                    request_budget,
                    provider,
                    retry=retry,
                )
            return await self.fetch_text(url)
        circuit = self._circuits.setdefault(host_key, _CircuitState())
        if circuit.opened_at is not None:
            if (
                time.monotonic() - circuit.opened_at
                < self._settings.crawler_circuit_cooldown_seconds
            ):
                metrics.set_circuit_state(host_key, "open")
                return self._finish(
                    CrawlResult(
                        url,
                        None,
                        None,
                        "circuit open for host after repeated failures",
                        source_status=ResearchSourceStatus.UNAVAILABLE,
                        original_url=url,
                        request_made=False,
                    ),
                    host_key,
                    started,
                    transport_success=False,
                    request_made=False,
                )
            circuit.failures = 0
            circuit.opened_at = None
        if request_budget is not None and not await request_budget.acquire(
            provider,
            host_key,
            retry=retry,
        ):
            return self._finish(
                CrawlResult(
                    url,
                    None,
                    None,
                    request_budget.stop_reason or "request budget exhausted",
                    source_status=ResearchSourceStatus.BUDGET_EXHAUSTED,
                    original_url=url,
                    request_made=False,
                ),
                host_key,
                started,
                transport_success=False,
                request_made=False,
            )
        self.request_count += 1
        try:
            if self._settings.crawler_engine.casefold() == "crawlee":
                result = await self._fetch_with_crawlee(url)
            else:
                result = await self._fetch_with_playwright(url)
        except Exception as exc:  # Browser errors remain an ordinary source failure.
            logger.info("public rendered crawl failed url=%s error=%s", url, exc)
            result = CrawlResult(url, None, None, str(exc), original_url=url)
        return self._finish_rendered(
            result,
            host_key,
            circuit,
            started,
            url,
            restricted_social_approved=restricted_social_approved,
        )

    async def _fetch_with_playwright(self, url: str) -> CrawlResult:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return CrawlResult(url, None, None, "Playwright is not installed")
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                initial = urlparse(url)
                blocked_reason: str | None = None

                async def restrict_route(route, request) -> None:
                    nonlocal blocked_reason
                    requested = urlparse(request.url)
                    is_initial_navigation = (
                        request.is_navigation_request()
                        and request.frame == page.main_frame
                        and request.redirected_from is None
                        and _canonical_host_key(requested) == _canonical_host_key(initial)
                        and requested.scheme == initial.scheme
                    )
                    if is_initial_navigation:
                        await route.continue_()
                        return
                    blocked_reason = (
                        "rendered redirects and subresources are blocked "
                        "for bounded public crawling"
                    )
                    await route.abort()

                await page.route("**/*", restrict_route)
                try:
                    response = await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=self._settings.browser_timeout_ms,
                    )
                except Exception:
                    if blocked_reason:
                        return CrawlResult(
                            page.url,
                            None,
                            None,
                            blocked_reason,
                            source_status=ResearchSourceStatus.BLOCKED,
                            original_url=url,
                        )
                    raise
                if blocked_reason and page.url != url:
                    return CrawlResult(
                        page.url,
                        None,
                        response.status if response is not None else None,
                        blocked_reason,
                        source_status=ResearchSourceStatus.BLOCKED,
                        original_url=url,
                    )
                text = (await page.locator("body").inner_text())[
                    : self._settings.max_response_bytes
                ]
                return CrawlResult(
                    page.url,
                    text,
                    response.status if response is not None else 200,
                    original_url=url,
                )
            finally:
                await browser.close()

    def _finish_rendered(
        self,
        result: CrawlResult,
        host: str,
        circuit: _CircuitState,
        started: float,
        original_url: str,
        *,
        restricted_social_approved: bool,
    ) -> CrawlResult:
        final_target = urlparse(result.url)
        final_error = _public_target_error(final_target)
        original_target = urlparse(original_url)
        status = result.source_status
        error = result.error
        text = result.text
        if final_error:
            status = ResearchSourceStatus.FAILED
            error = f"unsafe redirect destination: {final_error}"
            text = None
        elif _canonical_host_key(final_target) != _canonical_host_key(original_target):
            status = ResearchSourceStatus.BLOCKED
            error = "rendered cross-host navigation blocked by source policy"
            text = None
        elif not restricted_social_approved and _is_restricted_social_target(final_target):
            status = ResearchSourceStatus.BLOCKED
            error = "restricted social rendered access is disabled by policy"
            text = None
        elif result.status_code == 202:
            status = ResearchSourceStatus.TEMPORARILY_UNAVAILABLE
            error = "HTTP 202 accepted without usable evidence"
            text = None
        elif result.status_code is not None and result.status_code >= 400:
            status = _status_for_http_failure(result.status_code, error or "")
            error = error or f"HTTP {result.status_code}"
            text = None
        elif _is_auth_wall(_canonical_host_key(final_target), result.url, text or ""):
            status = ResearchSourceStatus.AUTH_REQUIRED
            error = "authentication required/login redirect"
            text = None
        elif error or not (text or "").strip():
            status = _status_for_http_failure(result.status_code, error or "empty response body")
            error = error or "empty response body"
            text = None
        else:
            status = ResearchSourceStatus.AVAILABLE
        normalized = CrawlResult(
            result.url,
            text,
            result.status_code,
            error,
            result.headers,
            status,
            original_url,
            result.redirect_requests,
            result.request_made,
        )
        if normalized.ok:
            circuit.failures = 0
            metrics.set_circuit_state(host, "closed")
        else:
            self._mark_failure(host, circuit)
        return self._finish(
            normalized,
            host,
            started,
            transport_success=result.status_code is not None,
        )

    async def _fetch_with_crawlee(self, url: str) -> CrawlResult:
        """Run the optional Node Crawlee PlaywrightCrawler adapter."""
        script = self._settings_path("scripts/crawlee_public_fetcher.mjs")
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                "node",
                str(script),
                url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self._settings.connector_timeout_seconds,
            )
            if process.returncode != 0:
                return CrawlResult(
                    url,
                    None,
                    None,
                    stderr.decode(errors="replace")[:500],
                    original_url=url,
                )
            payload = json.loads(stdout.decode(errors="replace"))
            if not payload.get("ok"):
                return CrawlResult(
                    payload.get("final_url") or url,
                    None,
                    payload.get("status_code"),
                    payload.get("error"),
                    original_url=url,
                )
            return CrawlResult(
                payload.get("final_url") or url,
                payload.get("text", ""),
                payload.get("status_code", 200),
                original_url=url,
            )
        except asyncio.TimeoutError as exc:
            if process is not None:
                await self._terminate_process(process)
            return CrawlResult(
                url,
                None,
                None,
                f"rendered crawl timed out: {exc}",
                original_url=url,
            )
        except (OSError, json.JSONDecodeError) as exc:
            return CrawlResult(url, None, None, str(exc), original_url=url)

    @staticmethod
    async def _terminate_process(process) -> None:
        """Terminate and reap a timed-out renderer before returning control."""
        if process.returncode is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
            return
        except asyncio.TimeoutError:
            pass
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()

    @staticmethod
    def _settings_path(relative: str):
        from pathlib import Path

        source = Path(__file__).resolve()
        for parent in (source.parents[2], source.parents[3]):
            candidate = parent / relative
            if candidate.exists():
                return candidate
        return source.parents[2] / relative

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _status_for_http_failure(
    status_code: int | None,
    message: str,
) -> ResearchSourceStatus:
    lowered = message.casefold()
    if status_code == 401 or "login" in lowered or "authentication" in lowered:
        return ResearchSourceStatus.AUTH_REQUIRED
    if status_code == 403 or "captcha" in lowered or "forbidden" in lowered:
        return ResearchSourceStatus.BLOCKED
    if status_code == 429 or "rate" in lowered:
        return ResearchSourceStatus.RATE_LIMITED
    if status_code in {404, 410}:
        return ResearchSourceStatus.UNAVAILABLE
    if status_code is not None and status_code >= 500:
        return ResearchSourceStatus.TEMPORARILY_UNAVAILABLE
    if "timeout" in lowered:
        return ResearchSourceStatus.UNAVAILABLE
    return ResearchSourceStatus.FAILED


def _is_auth_wall(host: str, url: str, text: str) -> bool:
    normalized_host = host.casefold().rstrip(".")
    if normalized_host.startswith("["):
        normalized_host = normalized_host.split("]", 1)[0] + "]"
    elif ":" in normalized_host:
        normalized_host = normalized_host.rsplit(":", 1)[0]
    if not any(
        normalized_host == item or normalized_host.endswith(f".{item}")
        for item in _AUTH_SENSITIVE_HOSTS
    ):
        return False
    login_markers = ("/login", "/uas/login", "sign in", "log in", "authentication")
    return any(marker in value.casefold() for value in (url, text) for marker in login_markers)


def _public_target_error(parsed) -> str | None:
    """Reject non-public HTTP targets before any network or browser request."""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "only absolute http(s) URLs are supported"
    host = (parsed.hostname or "").casefold().rstrip(".")
    if not host:
        return "URL host is required"
    if host in _UNSAFE_HOSTNAMES or host.endswith(".localhost") or host.endswith(".local"):
        return "private or local URL targets are not allowed"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if not address.is_global:
        return "private or non-global URL targets are not allowed"
    return None


def _canonical_host_key(parsed) -> str:
    """Return one circuit/budget identity for equivalent URL destinations."""
    host = (parsed.hostname or "").casefold().rstrip(".")
    if not host:
        return "invalid"
    try:
        port = parsed.port
    except ValueError:
        return "invalid"
    if (
        port is None
        or (parsed.scheme == "http" and port == 80)
        or (parsed.scheme == "https" and port == 443)
    ):
        return host
    if ":" in host:
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def _is_restricted_social_target(parsed) -> bool:
    host = (parsed.hostname or "").casefold().rstrip(".")
    return any(host == item or host.endswith(f".{item}") for item in _RESTRICTED_SOCIAL_HOSTS)
