# Operational source validation

The source acceptance runner validates public-source behavior without weakening
the deterministic offline suite. It is local-only, read-only, explicitly gated,
and bounded. It is not invoked by API startup, monitoring, or the frontend.

## Safety and isolation

Both `VENTURE_INTELLIGENCE_LIVE_TESTS=1` and `--allow-network` are required.
The runner uses three independent bounded contexts:

- known-positive connector controls: at most 24 application-managed requests;
- one normal venture-discovery run: at most 24 application-managed requests;
- Maigret and Sherlock: three explicitly named sites per installed tool, a
  five-second per-site timeout, no recursion, extraction, auto-update, proxy,
  Tor, CAPTCHA/Cloudflare bypass, browser, or AI mode, and a 45-second process
  deadline with termination/reaping.

The normal run uses a disposable SQLite database and no `EventBus`. It cannot
create a person, company, CRM record, ranking, graph node, alert, or
notification. The only persistent output is the atomic JSON report configured
by `SOURCE_QUALITY_REPORT_PATH`. GitHub authorization is optional; when
configured it is sent only to `api.github.com` and is removed on cross-origin
redirects. Reports expose configuration presence, never secret values.

```powershell
$env:VENTURE_INTELLIGENCE_LIVE_TESTS="1"
uv run venture-intelligence --json sources check `
  --program yc_current --allow-network `
  --max-queries 8 --max-requests 24
```

`GET /api/v1/source-quality/contract` documents the execution boundary. `GET
/api/v1/source-quality` and the React Sources workspace only read the last
report and cannot start collection.

## What the report means

Health, yield, and candidate coverage are separate:

- a known-positive control establishes whether a connector can return a stable
  public result;
- a healthy zero means the connector worked but the venture query produced no
  relevant evidence;
- parser/source drift means HTTP succeeded but the result contract did not;
- budget exhaustion means not attempted/completed, not source unavailability;
- candidate coverage measures evidence accepted by the strict discovery and
  identity gates, not raw links.

Every diagnostic records scope, normalized status, configuration/auth state,
requests, retries, redirects, HTTP status, rate-limit data where available,
latency, raw results, accepted observations, rejections, canonical URLs, drift,
and failure detail. The report also preserves exact provider/domain accounting
for both control and normal-run budgets.

## Latest broad bounded observation: 2026-09-02

The final run used disposable storage and made zero operational writes. The
control pool used 19 of 24 requests. The venture pool used its 21-request
discovery allocation (the remaining three requests were reserved for arXiv,
Wayback, and GitHub probes). Three retries and two redirects were charged.

Known-positive results:

| Control | Result | Evidence |
|---|---|---|
| DuckDuckGo | `SUCCESS` | 3 parsed public results |
| Bing | `SUCCESS` | 3 parsed public results |
| Yahoo | `TEMPORARILY_UNAVAILABLE` | HTTP 500 |
| Brave | `RATE_LIMITED` | HTTP 429 |
| Indexed LinkedIn | `SUCCESS` | DuckDuckGo returned actual `linkedin.com` URLs |
| Indexed X | `SUCCESS` | DuckDuckGo returned actual `x.com` URLs |
| Indexed Instagram | not demonstrated | DuckDuckGo 202; Bing unrelated results/drift; Yahoo 500 |
| Indexed Threads | not demonstrated | DuckDuckGo 202; Bing unrelated results/drift; Yahoo 500 |
| GitHub official API | `SUCCESS` | `torvalds/linux`, HTTP 200; token not configured |
| Hacker News Algolia | `SUCCESS` | 3 JSON hits |
| arXiv official API | `SUCCESS` | 3 canonical papers |
| TechCrunch RSS | `SUCCESS` | 3 canonical items |
| Newsletter/Substack RSS | `SUCCESS` | 3 canonical items |
| Wayback CDX | `UNAVAILABLE` | no usable control response in the final run |

The normal venture run retrieved eight items. Three passed topical relevance,
but all three lacked an entity mention; five were irrelevant. It created zero
observations, hypotheses, canonical promotions, CRM records, graph nodes, or
alerts. This is a degraded zero, not proof that no batch candidates exist.
Precision, recall, false-positive rate, and lead time remain unavailable because
no independent timestamped ground truth was supplied.

Normal-run source behavior was volatile but structured: Bing public and indexed
search succeeded; DuckDuckGo returned HTTP 202 then opened its circuit; Brave
was rate-limited; Yahoo became unavailable; TechCrunch returned one query item;
newsletter was a healthy empty; the VC watchlist returned a bounded partial;
arXiv returned three papers; GitHub was a healthy empty for the venture query;
and Wayback was unavailable. No direct LinkedIn, X, Instagram, or Threads
request occurred.

## Dedicated OSINT tools

Both executables were installed and invoked through the application boundary
with the safe public username `torvalds` and sites GitHub, Reddit, and Medium.

- Sherlock: `LIVE_VALIDATED`; 3 public profiles normalized into 3 ambiguous
  profile clues with provenance. Identity effect remained `none`.
- Maigret: `LIVE_VALIDATED`; the initial async-DNS run failed on Windows. After
  the application adapter selected Maigret's documented threaded resolver, a
  bounded one-site GitHub known-positive produced 2 ambiguous profile clues.

Username equality never creates or merges a canonical founder. If both tools
return the same profile URL, it is one evidence origin with two acquisition
paths. A restricted-platform URL discovered by either tool remains a clue and
does not authorize direct crawling.

See [SOURCE_ACCEPTANCE_MATRIX.md](SOURCE_ACCEPTANCE_MATRIX.md) for the complete
implemented connector and official-API inventory.

## Credentialed convergence validation: 2026-09-03

One subsequent 12-request isolated run validated the locally configured official
integrations without operational database writes or secret serialization:

- Product Hunt GraphQL known-positive: `SUCCESS`, HTTP 200, one normalized
  result/origin through the application connector.
- GitHub repository search known-positive: `SUCCESS`, HTTP 200, authenticated,
  one canonical repository URL, with 29 search requests remaining after the
  control. The normal venture query was a truthful authenticated `EMPTY` and
  reported 28 remaining.

The run stopped at `max_total_requests_reached`, retained partial diagnostics,
made zero direct restricted-social requests, and did not promote a candidate.
