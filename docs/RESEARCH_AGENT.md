# Bounded Research Agent

The Research Agent is a credential-free, AI-assisted research boundary for
public venture intelligence. It is deliberately conservative: it discovers
public evidence and records uncertainty; it does not prove accelerator
admission, identity, employment, funding, or investment suitability.

## What is implemented

`backend/venture_intelligence/research/agent.py` orchestrates a deterministic,
multi-provider discovery loop. The default API route uses
`DuckDuckGoSearchProvider`, `BingSearchProvider`, `YahooSearchProvider`,
`BraveSearchProvider`, `PublicProgramAnnouncementProvider`, and
`ArxivSearchProvider`. The announcement/query families issue a small, bounded set
of ordinary `site:` queries for public LinkedIn, X, Instagram, and Threads
announcement results associated with Y Combinator and a16z Speedrun. It never
opens social profiles, logs in, bypasses CAPTCHAs, or treats a social result as
identity proof. Search providers are acquisition paths only;
arXiv uses the official [`arxiv.py` package](https://pypi.org/project/arxiv/)
and official API metadata. The agent does not open arbitrary result URLs,
submit forms, use browser profiles, bypass CAPTCHAs, or access private/social
accounts.

The API entry point is `POST /api/v1/research`:

```json
{
  "entity_name": "Acme AI",
  "entity_type": "company",
  "aliases": ["Acme"],
  "domains": ["acme.ai"],
  "budget": {
    "max_queries": 8,
    "max_depth": 2,
    "max_results_per_query": 5,
    "max_runtime_seconds": 20,
    "max_total_requests": 16,
    "max_requests_per_provider": 8,
    "max_retry_requests": 4,
    "max_requests_per_domain": 12
  }
}
```

The response is the JSON-native `ResearchRun` Pydantic contract. Its target,
queries, source statuses, evidence, entities, hypotheses, identity status,
program status, independent evidence-origin count, budget, timing, stop reason,
and emitted-event count are all serializable without provider-specific objects.
Each evidence item is explicitly `attached`, `unbound`, or `contradictory` and
records why, its canonical origin, and its acquisition providers.

This target-based agent is not M8's proof of unknown-candidate discovery.
M8 uses the separate pre-canonical `batch_discovery/` pipeline, which accepts no
founder/company seed, treats official directories as verification only, models
negative and derivative evidence, and enriches only after a hypothesis exists.
See [`LATEST_BATCH_DISCOVERY.md`](LATEST_BATCH_DISCOVERY.md).

Batch-discovery query aliases are alternatives, not simultaneous requirements;
the selected cohort remains explicit. Membership classification uses registered
cohort IDs/full names. Older-cohort evidence cannot support the current cohort;
mixed cohorts remain ambiguous and unspecified cohorts remain neutral. A program
name or query keyword is not membership evidence.
An explicit single-token company `has joined [registered program]` statement may
create an unpromoted company hypothesis without a founder name. Ambiguous subjects
still require additional identity evidence; the canonical promotion boundary is
unchanged.

Public watchlist pages and batch-discovery direct verification extract document
text before applying excerpt limits, excluding head, script, style, and template
content. This does not execute JavaScript or establish identity from page code.
Layout block boundaries remain clause separators so adjacent name/degree fields
or headings/navigation cannot silently become one prose identity or investor claim.
Result titles and snippets also remain separate clauses during candidate
classification; a title must not become part of the first person's name.
Source-authored image `alt` descriptions are retained as labelled, separate text
clauses; images are not fetched or interpreted by this extraction step. Only an
explicit founder/person/company relationship can supply founder attribution.
Compound founder roles and explicit role-first statements are supported, including
Unicode letters in names. Additional explicit same-company founder-role claims
populate the existing cofounder field without adding independent source origins.
Bylines, separate role/name blocks, and program badges do not establish those
relationships or membership. These extraction improvements do not relax promotion
requirements. Search URL deduplication prefers the actual headline/excerpt over a
breadcrumb, but unavailable or irrelevant indexes remain a live-source limitation.
It is not a rendered-visibility engine: CSS-driven visibility and client-only
content still require separate treatment. Raw evidence is not rewritten.

## Query strategy

`QueryGenerator` creates bounded, deduplicated queries in three groups:

1. Exact quoted target queries (`"Acme AI" founder`, `"Acme AI" funding`,
   `"Acme AI" launch`, and similar public topics).
2. Site-restricted queries for configured target domains and public high-signal
   domains such as GitHub, arXiv, Hacker News, and accelerator directories.
3. Topic queries for aliases and configured domains.

Search snippets can trigger adaptive follow-ups at the next depth. Only
conservative founder/co-founder names and GitHub usernames are promoted into
follow-up queries. Generic labels such as `company`, `startup`, `founder`,
`twitter`, `media`, and `keyframes` are rejected; the agent never creates a
person entity merely because a snippet contains a generic label.

## Source tiers and failure states

The default factory enables four credential-free public search paths under one
request budget. A failure in one provider does not prevent later providers from
running. The boundary also ships credential-free Wayback CDX, incremental
RSS/Atom, and public watchlist-page adapters.
They can be selected by an embedding service without changing the response
contract:

| Tier | Meaning | Current behavior |
|---|---|---|
| 1 | Official/public first-party page or public structured source | A result from a recognized official domain receives a higher base confidence. |
| 2 | Public secondary source | Search-discovered evidence is retained with lower calibrated confidence. |
| 3 | Search-only or auth-sensitive discovery | The URL is recorded as `search_discoverable`; the agent does not follow it. |

Every source has a structured status: `available`, `search_discoverable`,
`auth_required`, `blocked`, `rate_limited`, `temporarily_unavailable`,
`unavailable`, `failed`, or `budget_exhausted`.
HTTP 401/403/429/404/410 and login, CAPTCHA, forbidden, or rate-limit errors
are represented in the run and do not abort unrelated queries. Empty or
failed runs still return a complete contract with a stop reason and retry or
fallback next action.

Discovery method is separate from source status. `public_api` identifies the
official arXiv API result path; `direct_public` means a
future/optional adapter read a public page or endpoint directly; it is not a
claim that this default agent followed a result. `search_engine` (the current
implementation) means the evidence came from a public search result/snippet.
`rss` is reserved for a public RSS/Atom adapter and `public_api` for a
credential-free endpoint such as a public GitHub, Hacker News, or arXiv API.
`auth_required`, `blocked`, and `rate_limited` describe access outcomes, not
discovery methods: a search result can remain useful while its destination is
one of those restricted states.

### arXiv metadata

`ArxivSearchProvider` uses `arxiv.Client` and `arxiv.Search`, bounded by the
run's result/time budget. Each result retains a canonical HTTPS `/abs/` URL,
title, authors, abstract, publication/update dates, categories, DOI/journal
metadata when present, and provenance naming `arxiv.py`, the API endpoint, the
query, entry ID, and retrieval time. API access failures are structured and do
not prevent DuckDuckGo or other configured providers from returning evidence.

### Google Scholar boundary

`GoogleScholarSearchProvider` is disabled by default because Scholar's public
HTML endpoint is not a reliable service contract. If explicitly enabled, it
attempts only ordinary public HTTP, parses clean public paper links when
available, and records `BLOCKED`, `RATE_LIMITED`, CAPTCHA, or unavailable
responses as structured failures. It never logs in, rotates proxies, uses
stealth/fingerprint tricks, solves CAPTCHAs, or treats Scholar snippets as
verified paper metadata. No Scholar client is required for startup.

## Budgets, caching, and deduplication

The run uses one request-budget authority shared by provider fan-out, retries,
and crawler redirect hops. Reservation occurs at the HTTP transport boundary
for crawler-backed providers, so logical-query and actual-request limits are
enforced separately without double counting:

- `max_queries` is the maximum number of unique searches;
- `max_depth` bounds adaptive follow-ups;
- `max_results_per_query` bounds retained results;
- `max_runtime_seconds` is enforced around each provider call and across the
  complete run;
- `max_total_requests` counts every outbound provider attempt, including retries;
- `max_requests_per_provider` bounds fan-out from one adapter;
- `max_retry_requests` bounds retry amplification across all providers;
- `max_requests_per_domain` bounds calls to one external host.

Domain accounting canonicalizes host case, trailing dots, and default ports.
Each redirect is checked against public-target and restricted-source policy
before its budget reservation and network request. A search provider cannot
redirect collection into direct LinkedIn/X/Instagram or local/private access.

Settings defaults are `20`, `3`, `5`, `30` seconds, `30`, `12`, `8`, and `20`.
They are configurable
with `RESEARCH_MAX_QUERIES`, `RESEARCH_MAX_DEPTH`,
`RESEARCH_MAX_RESULTS_PER_QUERY`, `RESEARCH_MAX_RUNTIME_SECONDS`,
`RESEARCH_MAX_TOTAL_REQUESTS`, `RESEARCH_MAX_REQUESTS_PER_PROVIDER`,
`RESEARCH_MAX_RETRY_REQUESTS`, and `RESEARCH_MAX_REQUESTS_PER_DOMAIN`.

DuckDuckGo treats HTTP 202 and 429 as retryable temporary failures. It performs
at most three bounded attempts with exponential backoff, jitter, and a capped
`Retry-After` value; a final failure remains structured and produces no
evidence. Each run reports query successes/failures, retries, redirect requests,
sources checked, follow-ups, query/request budget remaining, provider/domain
request counts, and an explicit stop reason. A low total request budget stops
redirect hops and scoped-query fan-out
instead of allowing each provider or retry loop to reinterpret the limit.
When a provider returns valid evidence and then reports `BUDGET_EXHAUSTED`, the
evidence remains in the run and records a successful evidence outcome; the
status and stop reason still truthfully report why collection ended.

Quoted terms pass through one canonical normalization boundary. Exact queries,
already quoted terms, special characters, and site-scoped announcement queries
therefore receive one layer of quoting and never produce nested forms such as
`""Acme Labs""`.

`WaybackSearchProvider` records original/archive URLs, CDX snapshot time,
retrieval time, timestamp precision, provenance, and conservative URL-path
lifecycle signals such as team/careers/docs/pricing pages. Resulting evidence is
`temporal_status="historical"`; it never presents an archived page as current or
uses a historical snapshot to prove a current identity without corroboration.
`RssSearchProvider` parses explicitly configured public RSS/Atom feeds. Both
are opt-in extension providers and are safe when unavailable.

`ResearchCache` is an in-process TTL cache keyed by normalized query text. Only
available provider responses are reusable; budget exhaustion, rate limits,
timeouts, blocks, and other transient/run-specific failures are not cached. URL,
query and canonical evidence-origin fingerprints prevent duplicate work within
a run. Different search providers finding the same underlying page remain
acquisition paths to one evidence origin, not independent corroboration. The cache
is intentionally not presented as durable truth; restart the process to
clear it, or lower `RESEARCH_CACHE_TTL_SECONDS` when source freshness matters.

## Pipeline integration and entity safety

For company, person, and product targets, only evidence attributed to the exact
target identity can emit
a typed `RESEARCH_DISCOVERED` `CanonicalEvent` with source
`research_agent`, a real result URL, confidence, query/discovery metadata, and
the JSON evidence payload. Product observations map to the existing company
pipeline; `program` and `other` targets produce no fabricated pipeline entity.
The events enter the existing SQLite `EventBus`, validator, resolver, graph,
evidence, and downstream queue topology. Name-only matches remain unbound in the
research/knowledge investigation view and cannot reach canonical entity-derived
state. Research confidence is intentionally low-to-moderate and triangulation
only increases it for independent domains.

The agent does not infer a person, repository, paper, or program admission from
a generic snippet. Existing connector-specific event taxonomies remain the
source of truth for those specialized entity types.

## Future Optional Paid Integrations

Commercial search/SERP, enrichment, social-data, or crawling providers could
be added later behind the `SearchProvider` interface. They are not implemented,
not enabled, not required, have no runtime dependency, require no credentials
for the current application, and are not used by tests. This section is a
design boundary only; no paid client is part of the active runtime path.

## Optional integrations and live validation

No paid provider or credential is required. Firecrawl settings remain a
reserved, optional boundary because the historical specification did not
define a mandatory provider contract. The conversational analyst is a separate,
optional local/provider-neutral boundary; this research agent does not invoke a
cloud model. Optional Ollama extraction belongs to environmental connectors,
not this public search boundary.

The credential-free test suite uses fake providers and deterministic HTML/result
fixtures. It verifies budgets, adaptive queries, cache deduplication, source
failure classification, confidence triangulation, JSON serialization, and
typed EventBus publication. A live DuckDuckGo run or a third-party source is an
operational check, not a prerequisite for the unit suite and must be reported
separately from implementation coverage.

## Extension points

Add a free/public provider by implementing the `SearchProvider` protocol and
returning `SearchResponse`/`SearchResult` models. Keep each adapter bounded,
close its resources, preserve source status and provenance, and add offline
fixtures for success, empty, auth, blocked, rate-limited, and malformed cases.
Do not add credentials, private-source access, paid-provider calls, or an
unbounded crawler to this boundary.
