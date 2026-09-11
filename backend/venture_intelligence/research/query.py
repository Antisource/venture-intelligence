"""Deterministic query families and bounded follow-up discovery."""

from __future__ import annotations

import re

from venture_intelligence.shared.models import (
    ResearchDiscoveryMethod,
    ResearchEvidence,
    ResearchQuery,
    ResearchQueryIntent,
    ResearchTarget,
)

_DEFAULT_DOMAINS = (
    "linkedin.com/company",
    "x.com",
    "github.com",
    "medium.com",
    "substack.com",
)
_TOPIC_SUFFIXES = (
    ("founder", "founder"),
    ("cofounder", "cofounder"),
    ("funding", "funding"),
    ("raised", "funding"),
    ("hiring", "hiring"),
    ("launch", "launch"),
    ("research", "research"),
    ("accelerator", "accelerator"),
    ("demo", "demo"),
    ("repository", "repository"),
)
_GENERIC_NAMES = {
    "twitter",
    "media",
    "keyframes",
    "founder",
    "company",
    "startup",
    "unknown",
}


def _clean(value: str) -> str:
    return " ".join(value.split()).strip()


def normalize_query_text(value: str) -> str:
    """Return one canonical unquoted search term.

    Query families may be passed between generators and provider adapters.  By
    stripping quote characters at this one boundary, an already quoted exact
    query cannot become ``""Acme""`` when a provider adds a site scope.
    """
    return _clean(value).replace('"', "")


def normalize_structured_query(value: str) -> str:
    """Normalize whitespace while preserving search operators and phrases."""
    normalized = _clean(value)
    while '""' in normalized:
        normalized = normalized.replace('""', '"')
    return normalized


def quote_query_term(value: str) -> str:
    """Quote a normalized term exactly once."""
    return f'"{normalize_query_text(value)}"'


def scoped_query(
    scope: str,
    *terms: str,
    suffix: str = "",
    structured_query: str = "",
) -> str:
    """Compose a normalized site/program query without nested quoting."""
    parts = [
        scope.strip(),
        *(quote_query_term(term) for term in terms if normalize_query_text(term)),
    ]
    if structured_query.strip():
        parts.append(normalize_structured_query(structured_query))
    if suffix.strip():
        parts.append(normalize_query_text(suffix))
    return _clean(" ".join(parts))


def is_probable_person_name(value: str) -> bool:
    """Reject generic labels before they can become person candidates."""
    words = re.findall(r"[^\W\d_](?:[^\W\d_]|[.'’-])*", value)
    if len(words) < 2 or len(words) > 4:
        return False
    normalized = {word.casefold() for word in words}
    if normalized & _GENERIC_NAMES:
        return False
    return all(len(word) >= 2 for word in words)


class QueryGenerator:
    """Build reusable, deduplicated search queries without provider calls."""

    def __init__(self, domains: tuple[str, ...] = _DEFAULT_DOMAINS) -> None:
        self.domains = tuple(
            dict.fromkeys(domain.strip().lower() for domain in domains if domain.strip())
        )

    def generate(self, target: ResearchTarget) -> list[ResearchQuery]:
        names = [target.name, *target.aliases]
        domains = tuple(dict.fromkeys([*target.domains, *self.domains]))
        candidates: list[ResearchQuery] = []
        for name in names:
            clean = _clean(name)
            if not clean:
                continue
            candidates.append(self._query(quote_query_term(clean), "exact"))
            for domain in domains:
                candidates.append(self._query(f"site:{domain} {quote_query_term(clean)}", "site"))
            for suffix, family in _TOPIC_SUFFIXES:
                candidates.append(self._query(f"{quote_query_term(clean)} {suffix}", family))
        return self._dedupe(candidates)

    def follow_ups(
        self,
        target: ResearchTarget,
        evidence: ResearchEvidence,
        next_depth: int,
    ) -> list[ResearchQuery]:
        """Derive only high-signal, bounded follow-ups from visible snippets."""
        if next_depth <= evidence.depth:
            return []
        text = f"{evidence.title} {evidence.snippet}"
        queries: list[ResearchQuery] = []
        for match in re.finditer(
            r"(?:founder|co[- ]founder|cofounder)[^A-Za-z]{0,8}"
            r"([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3}?)"
            r"(?=\s*[,;.!?]|\s+(?:github|linkedin|x\.com)\b|$)",
            text,
            re.IGNORECASE,
        ):
            person = _clean(match.group(1))
            if is_probable_person_name(person):
                queries.append(
                    self._query(
                        f"{quote_query_term(person)} {quote_query_term(target.name)}",
                        "founder_follow_up",
                        next_depth,
                    )
                )
                queries.append(
                    self._query(
                        f"site:github.com {quote_query_term(person)}",
                        "github_follow_up",
                        next_depth,
                    )
                )
        for username in re.findall(r"github\.com/([A-Za-z0-9-]{2,39})", text, re.I):
            queries.append(
                self._query(
                    f"site:github.com {quote_query_term(username)}",
                    "github_follow_up",
                    next_depth,
                )
            )
        return self._dedupe(queries)

    @staticmethod
    def _query(query: str, family: str, depth: int = 0) -> ResearchQuery:
        intent = {
            "research": ResearchQueryIntent.PUBLICATION,
            "github_follow_up": ResearchQueryIntent.TECHNICAL,
        }.get(family, ResearchQueryIntent.WEB_DISCOVERY)
        return ResearchQuery(
            query=query[:500],
            family=family,
            depth=depth,
            discovery_method=ResearchDiscoveryMethod.SEARCH_ENGINE,
            intent=intent,
        )

    @staticmethod
    def _dedupe(queries: list[ResearchQuery]) -> list[ResearchQuery]:
        seen: set[str] = set()
        result: list[ResearchQuery] = []
        for item in queries:
            key = " ".join(item.query.casefold().split())
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result
