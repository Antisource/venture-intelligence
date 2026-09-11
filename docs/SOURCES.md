# Public sources

Connectors are generic and configured by `config/programs.yaml`. The active
factory includes accelerator directories, public websites, newsletters/blogs,
indexed X/Instagram/LinkedIn discovery, Discord public widgets, GitHub, Hacker
News, arXiv, Product Hunt, and Wellfound. The default monitor does not contact
LinkedIn, X, or Instagram directly. Ordinary direct public access is available
only under the explicit `approved_public_web` access mode.

The crawler attempts ordinary public HTTP only for sources allowed by policy.
Policy checks use the destination host. A permitted search-provider URL may
contain `site:linkedin.com`, `site:x.com`, or `site:instagram.com` as query text;
that does not authorize direct requests to those platform hosts.
HTTP 202, login-shaped 200 responses, login redirects, 403/429, CAPTCHA markers,
timeouts, and circuit-open states become structured failures; they never become
evidence. Private Discord access, credentialed scraping,
CAPTCHA solving, stealth, proxy rotation, and authentication bypass are not
implemented.

Research discovery defaults to DuckDuckGo, Bing, Yahoo, and Brave public HTML
plus the official arXiv API via
the [`arxiv.py` package](https://pypi.org/project/arxiv/). arXiv results retain
canonical paper URLs, title, authors, abstract, publication dates, metadata,
and API provenance. The provider interface is extensible for free RSS/Atom,
Wayback, GitHub, and other public adapters.

Google Scholar is an opt-in discovery adapter only. Because ordinary public
Scholar access can be blocked or rate-limited, those outcomes are structured
as source failures and the rest of the run continues. No credentials, paid API,
CAPTCHA bypass, proxy rotation, or stealth browser is used.

Product Hunt's official GraphQL boundary is implemented but disabled until its
enable flag and developer token are configured. It is targeted corroboration
only. Upstream deployment/use restrictions remain the operator's responsibility;
Firecrawl and paid enrichment remain optional and are not required for startup.

## Milestone 1 machine-readable policy

`GET /api/v1/sources` is the authoritative, non-secret source inventory for
the current application boundary. It returns separate `providers` and
`connectors` arrays. Every registered research provider and monitor connector
has a stable ID, capability list, acquisition method, allowed/default access
mode, discovery/verification role, enabled state, credential requirement,
restricted hosts, and safe fallback where relevant.

Research providers currently declared are DuckDuckGo, Bing, Yahoo, Brave,
indexed public program announcements, arXiv, optional Google Scholar, Wayback,
configured RSS, TechCrunch, newsletter/Substack watchlists, and public VC pages.
`runtime` records enabled state, independently measured health, last checks and
incremental item counts. `PUT /api/v1/sources/{source_id}` persists a manual
override; a temporary outage never silently disables a source.
The connector inventory matches the monitor factory exactly. LinkedIn's direct
connector is `permission_required` and disabled by default; the social
connector is search-discovery-only by default. This preserves the intended
boundary:

```text
permitted search provider -> indexed LinkedIn/X/Instagram/Threads result (discovery)
venture-intelligence -> direct LinkedIn/X/Instagram/Threads automation (disabled)
```

Search responses, Research Agent source summaries, and gather outcomes expose
the shared `SourceOperationStatus` enum. `PARTIAL` means usable evidence was
preserved even though collection did not complete; `EMPTY` means a successful
operation produced no evidence. Legacy transport status remains available where
needed but is not the normalized API outcome.

M9 adds a separate operational observation layer. `GET
/api/v1/source-quality/contract` describes its two-part local CLI gate and hard
request ceiling. `GET /api/v1/source-quality` returns the last atomic report and
never contacts a source. The React Sources workspace displays this report next
to policy metadata so configured capability is not confused with live health.
See [`OPERATIONAL_VALIDATION.md`](OPERATIONAL_VALIDATION.md).

M2 adds the historical foundation: Wayback CDX output retains typed
snapshot/retrieval time, precision, original/archive URLs, provenance, and
conservative URL-path lifecycle signals. The adapter remains opt-in and does
not prove current identity. The M9 acceptance boundary may run installed
Maigret and Sherlock tools only behind the explicit live gate, with an explicit
three-site allowlist and process deadline. Their output remains ambiguous
profile clues, never identity or accelerator-membership authority.

## M8 discovery-first source boundary

`GET /api/v1/batch-discovery/contract` adds the M8-specific role/access matrix
without replacing `/api/v1/sources`. Public search and indexed program
announcements are DISCOVERY sources. The official program directory is
VERIFICATION-only and cannot seed unknown candidates. Direct public pages can
verify identity under the existing target/redirect/budget policy. Technical and
publication sources are IDENTITY/ENRICHMENT; Wayback is HISTORICAL. Manual
Maigret/Sherlock JSON remains available as an offline import. Installed tools
may additionally run through the bounded gated diagnostic; Venture Intelligence
never treats username equality as identity proof. In the final 2026-09-02
control, Sherlock completed end to end with three ambiguous profile clues.
After the Windows adapter selected Maigret's documented threaded DNS resolver,
a bounded one-site GitHub known-positive produced two ambiguous profile clues.
Their results are not program, funding, or
canonical-founder authority.
[`LATEST_BATCH_DISCOVERY.md`](LATEST_BATCH_DISCOVERY.md).

The complete source/access/official-API inventory is
[`SOURCE_ACCEPTANCE_MATRIX.md`](SOURCE_ACCEPTANCE_MATRIX.md).

## Connector lifecycle and safety

All monitor connectors implement the same bounded `scan`, `investigate`,
`can_investigate`, and `close` lifecycle. The coordinator isolates failures,
deduplicates entity/source work, and caps follow-up depth. Program-specific
URLs and vocabulary live in `config/programs.yaml`; the connector layer does
not define accelerator-specific classes.

Redirect targets are revalidated before contact. Equivalent host aliases share
request budgets and circuit state. Rendered access is limited to an approved
top-level navigation; Playwright/Crawlee abort redirects and subresources where
browser-managed fan-out cannot be charged exactly. Crawlee children are
terminated and reaped on timeout. A transport success with no usable evidence
is `EMPTY`, while preserved evidence from an incomplete operation is `PARTIAL`.

Repositories and papers remain their own entity types. Indexed social results,
directory layout text, usernames, connector names, and generic labels cannot be
retyped as founders or companies. Tests use fake transports; live availability
is documented only as a dated operational observation.
