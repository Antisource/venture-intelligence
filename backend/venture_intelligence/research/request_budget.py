"""Shared accounting for actual outbound research requests.

Logical query limits are useful, but a provider may fan one query into several
HTTP requests and retries.  This tracker is the single runtime authority for
those actual requests within one research run.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field

from venture_intelligence.shared.models import ResearchBudget


def _canonical_domain_key(value: str) -> str:
    """Collapse equivalent host spellings for shared budget accounting."""
    normalized = value.casefold().strip()
    if normalized.startswith("["):
        match = re.fullmatch(r"\[([^]]+)](?::(\d+))?", normalized)
        if match:
            host, port = match.groups()
            return f"[{host}]" if port in {None, "80", "443"} else f"[{host}]:{port}"
    host, separator, port = normalized.rpartition(":")
    if separator and port.isdigit() and ":" not in host:
        normalized = host if port in {"80", "443"} else f"{host}:{port}"
    return normalized.rstrip(".") or "unknown-domain"


@dataclass
class RequestBudgetTracker:
    """Atomically enforce total, retry, provider, and domain request limits."""

    budget: ResearchBudget
    requests_used: int = 0
    retry_requests_used: int = 0
    redirect_requests_used: int = 0
    provider_requests: dict[str, int] = field(default_factory=dict)
    domain_requests: dict[str, int] = field(default_factory=dict)
    stop_reason: str | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    started_at: float = field(default_factory=time.monotonic, repr=False)

    async def acquire(
        self,
        provider: str,
        domain: str,
        *,
        retry: bool = False,
        redirect: bool = False,
    ) -> bool:
        """Reserve one real outbound request or return a stable stop reason."""
        provider_key = provider.casefold().strip() or "unknown-provider"
        domain_key = _canonical_domain_key(domain)
        async with self._lock:
            reason = self._limit_reason(provider_key, domain_key, retry=retry)
            if reason is not None:
                self.stop_reason = reason
                return False
            self.requests_used += 1
            self.provider_requests[provider_key] = self.provider_requests.get(provider_key, 0) + 1
            self.domain_requests[domain_key] = self.domain_requests.get(domain_key, 0) + 1
            if retry:
                self.retry_requests_used += 1
            if redirect:
                self.redirect_requests_used += 1
            return True

    def _limit_reason(self, provider: str, domain: str, *, retry: bool) -> str | None:
        if time.monotonic() - self.started_at >= self.budget.max_runtime_seconds:
            return "timeout"
        if self.requests_used >= self.budget.max_total_requests:
            return "max_total_requests_reached"
        if self.provider_requests.get(provider, 0) >= self.budget.max_requests_per_provider:
            return f"max_provider_requests_reached:{provider}"
        if self.domain_requests.get(domain, 0) >= self.budget.max_requests_per_domain:
            return f"max_domain_requests_reached:{domain}"
        if retry and self.retry_requests_used >= self.budget.max_retry_requests:
            return "max_retry_requests_reached"
        return None

    @property
    def remaining(self) -> int:
        return max(0, self.budget.max_total_requests - self.requests_used)

    def snapshot(self) -> dict[str, object]:
        """Return JSON-native accounting suitable for the ResearchRun contract."""
        return {
            "requests_used": self.requests_used,
            "retry_requests_used": self.retry_requests_used,
            "redirect_requests_used": self.redirect_requests_used,
            "request_budget_remaining": self.remaining,
            "provider_request_counts": dict(sorted(self.provider_requests.items())),
            "domain_request_counts": dict(sorted(self.domain_requests.items())),
            "stop_reason": self.stop_reason,
        }
