# Connector and official-API acceptance matrix

This inventory follows the authoritative provider and connector policy registries.
The table below retains earlier per-source coverage; the dated reconciliation
in this section is authoritative for the latest pass. “Live”
is a timestamped observation, not a permanent guarantee.
`OFFICIAL_API_NOT_SUPPORTED` means
the official API is not implemented by this connector; it does not mean the
public source itself is unsupported.

## Current P0 continuation: 2026-09-09 — closed

The current-cohort recovery gate is closed. SMTP, F26, and directory-blind
Walden results below were preserved and not repeated.

### Attribution corrections and validation

- Public image `alt` descriptions now remain labelled `Image description:` and
  separated from neighboring layout blocks. They are source-authored assertions,
  not image recognition or independent verification. Script/template descriptions
  remain excluded. A badge, logo, or program name does not assert membership.
- Explicit compound founder roles and role-first statements are recognized when
  the same clause names the person and company. Unicode letters in person names
  are no longer split by the ASCII-only name validator. Separate headings, roles,
  bylines, and nearby names are not joined to manufacture founder relations.
- Additional explicit person-first founder-role statements for the same company
  populate the existing cofounder field. Other-company statements and adjacent
  layout names are excluded. Multiple founders on one page remain **one origin**;
  no promotion or independence threshold changed.
- Search-result URL deduplication now prefers a headline/excerpt over an earlier
  breadcrumb for that same URL, before applying the result cap. This fixes an
  actual parser loss but cannot repair an irrelevant upstream response.
- Classification also keeps a clause boundary between the result title and
  snippet. A failing regression reproduced `Founding Team Dana Lee` as a person;
  the correction preserves only the explicit `Dana Lee` founder statement.

### Current SR007 controls

| Control | Actual result | Outstanding requirement |
| --- | --- | --- |
| Official SR007 collection | One bounded public API request returned 20 SR007 companies and 40 explicitly attributed founders through the generic `DirectoryConnector` and `ProgramRegistry`. | Closed: company, founders, batch and source provenance are available. The directory remains verification/ground truth, not an unknown-candidate discovery seed. |
| DRS | Isolated live application run `fe4e2626-0451-479d-aa1e-2b7b6ff5da32` combined Osman Elsöz → DRS from Mobidictum with independent company-level SR007 acceptance evidence from Swipeline. | Closed at 87% with two independent supporting origins and one promotion. The company-only source did not invent a person. |
| Lazyweb | Earlier SR007 company-only observation remains provisional. Its homepage yielded no attributable founder. | Correctly remains incomplete; it is a negative control rather than a completed recovery. |

Mecone captured-live run: `8e0fa86d-90bb-4c86-aba3-831f475fcb9b`, isolated
`vi-sr007-attribution-rov_o9_b/intel.db`. Offline reinterpretation preserved its
claim, origin, and `2026-09-08T17:22:54.781218Z` observation timestamp; database
counts were unchanged. A later one-request live cofounder check,
`90d8c6f0-cd2e-4667-a9e2-196b17a6efb9`, yielded an irrelevant result and no
observation. It was not retried and is not labelled successful recovery.

DRS's article was located with one public-site search request, not by following a
restricted-social link. Initial application run
`b54f7c21-7be1-4719-bb2c-0d06d58b88f3` rejected the explicit role-first statement.
After the regression fix, run `681b9992-dfff-4112-95bc-3b73e14b302f` used one request,
zero retries/redirects, and persisted one observation in isolated
`vi-sr007-drs-attributed-wy814m2f/intel.db`. Candidate
`062612b3-aa29-59fc-bd26-c672c6935dba` retains the public origin
`https://mobidictum.com/drs-joins-a16z-speedrun-accelerator`.
Replay selected zero observations before the cutoff and one at the observation
timestamp, repeated deterministically, and left database counts unchanged.
The later isolated run above added the independent Swipeline origin and produced
one eligible promoted company plus its attributed founder. The same article
fetched twice still does not count as independent corroboration.

Lazyweb homepage run: `2f9619b8-c3ef-49d9-86ad-cc1fe12c3a82`, isolated
`vi-sr007-lazyweb-identity-uyk2huyr/intel.db`. Its already successful membership
article was not requested again.

### Bounded social conclusion

**EXTERNAL PROVIDER RESPONSE LIMITATION in the tested window; live social
discovery NOT DEMONSTRATED.** Three additional Bing requests, zero retries,
tested `site:linkedin.com/posts "SR007"`, `site:x.com "SR007"`, and the known-control
query `site:linkedin.com/posts "Mecone" "SR007"`. The responses were HTTP 200 but
off-target. For the last request, the final URL and search-form query preserved
the requested terms; raw result headings were unrelated software/help topics and
raw HTML contained zero LinkedIn result targets. Therefore parser improvements
alone cannot recover a relevant social claim from that response. The underlying
cause of the provider's irrelevant response is not established. This is not a
claim that social evidence can never be accessed. No restricted platform was
contacted, no observations were accepted, and unavailable providers were not
hammered or replaced with access-control circumvention.

A final two-request indexed-only probe used the exact venture query
`site:linkedin.com/posts "OpenWhispr" "SR007" founder` against only the remaining
Yahoo and Brave paths. It produced zero results and stopped at the shared request
ceiling; no direct LinkedIn/X/Instagram/Threads request occurred. The conclusion
therefore remains an external provider response limitation, not parser success.

### Checkpoint tests

The affected seven-file regression set passed **135 tests**, zero failures, with
one existing Starlette/httpx TestClient deprecation warning. It includes team
extraction → persistence → Research API → cutoff replay, single-origin promotion
rejection, compound/role-first attribution, Unicode names, and all previously
selected identity/security/cohort regressions. Changed-file Ruff and compile/syntax
checks passed. Later focused regressions additionally cover Turkish company-level
program acceptance, strict founder attribution, public JSON directory collections,
shared collection URLs, and prevention of person-to-company retyping. Final
combined-gate results belong in the release report rather than this live matrix.

## Earlier preserved checkpoint: 2026-09-08 — incomplete

This section supersedes earlier readiness implications, not the immutable results
of earlier runs. No ship decision or complete final quality gate is asserted here.

### Current controls versus unknown discovery

- Three public YC detail pages were fetched through DirectoryConnector and the
  shared bounded crawler, then processed through EventBus and the canonical
  pipeline in isolated storage: Forward (Daniel Yoon, Riley Janjis, Darryl Luo),
  Sinter (Oliver Johnson, Justin Kwon), and OnePatch (Shalin Patel, Deep Utkarsh).
  All three records explicitly identify F26. The isolated store contains three
  companies, seven people, ten events, ten evidence records, and ten graph edges.
  Browser/Research acceptance of these controls is not yet complete.
- The public Speedrun company collection returned HTTP 200 using one request,
  but yielded no company-detail links or structured company records. This is
  not SR007 recovery. Three current SR007 controls remain unverified through the
  application; web-search scouting is not substituted for application evidence.
- Initial empty-state generic discovery used 20 requests: 15 retrieved items,
  eight considered relevant, zero observations/hypotheses. Separate diagnosis
  found 33 public TechCrunch feed items hidden by an erroneous quoted-only
  filter for mixed `OR` queries.
- After focused corrections, a NEW empty-state run (not a rewrite of the failed
  run) used 20 requests, one retry, and three redirects. Funnel: 36 retrieved
  item appearances, 23 considered relevant, one observation, one new provisional
  company, zero founder identities, zero promotions. Repeated query appearances
  are not distinct items or independent confirmations.
- The previously unseeded hypothesis is **Walden Robotics**, discovered by
  DuckDuckGo from a TechStartups public article. No expected company/founder names,
  directory entries, or prior operational database were supplied. Its one source
  origin and original query/snippet are retained. Identity confidence is 74%;
  program confidence is 0%. Founder and batch membership remain unresolved.
  The article's URL date is not asserted as a verified publication timestamp.
- Actual persisted-candidate API checks returned 200 for health, unscoped listing,
  candidate detail, and a separate empty YC listing. Research receives the same
  observation/provenance. Replay selected zero observations before `observed_at`
  and one at it; repeated results matched and the database SHA-256 was unchanged.
  Canonical entity count remains zero. React component integration verifies
  All programs → candidate → Research evidence and specific-program exclusion;
  this is not a real-browser acceptance claim.

### Social and SMTP

The separate SR007 indexed-social probe used six requests, including two bounded
retries. DuckDuckGo returned HTTP 202 without evidence. Other indexes returned
nine result appearances but no usable venture observations (two X and one
Instagram reference were rejected as irrelevant). LinkedIn/Threads discovery was
not demonstrated. No direct restricted-social collection was enabled.

The alias-composition defect was subsequently corrected: program aliases are OR
alternatives, with the target cohort required separately. One corrected isolated
SR007 probe used six requests, two retries, and zero redirects: Bing returned five
irrelevant results; DuckDuckGo was temporarily unavailable; Yahoo and Brave ended
with `BUDGET_EXHAUSTED`. No observations or candidates were created. Accounting
contains search-provider domains only. Social discovery remains NOT DEMONSTRATED;
corrected query syntax is not a live-yield pass. Run:
`1491e494-c015-4880-974f-81b5bd1d089e`.

The recipient was explicitly reconfirmed in this pass. One harmless application
SMTP test was sent with retries disabled: subject `Venture Intelligence test email`,
body `Email notifications are configured.` The SMTP provider acknowledged it in
one attempt; inbox delivery is not verified. Only isolated notification state was
configured, and its recipient was restored to blank. No operational email settings
were changed and no credential was printed. Do not repeat this successful send.

### Corrections and exact remaining work

Implemented with failing-then-passing regressions: mixed-OR RSS recall; all-registry
directory/talent-host exclusion at discovery entry; dictionary definition-only
relevance; generic hypothesis replay; historical SR006 configuration; optional
program listing and native All programs visibility. Confidence and canonical
promotion thresholds were not relaxed. OpenAPI/TypeScript were regenerated.

Further regressions exposed and corrected false current-cohort support: registered
older batches are `other_batch`, mixed cohort mentions are ambiguous, and
unspecified cohorts are neutral. IDs/full cohort names are matched as whole tokens;
program keywords alone do not prove current membership. Clean HTML deep links
also now preserve candidate/view query parameters through the HashRouter redirect.

Three separate known-control public pages (Mecone team, First 1000's Lazyweb
announcement, and DRS's first-party site) were then fetched by the existing public
page provider using three requests and no retries. All three excerpts were
dominated by CSS/JavaScript and produced zero observations. This was an application
defect, not a blocked-source excuse. Both watchlist and direct-verification HTML
paths now exclude head/script/style/template content before excerpt truncation.
The relevance gate also recognizes explicit `founded` and `joined` claims.
Offline regressions exercise extraction and candidate classification together;
script-contained identities must not become observations. No rendered crawling
or new provider was introduced. Initial control run:
`1972c89a-57e4-4cbb-b6a2-a19e86bd88dc`.

The corrected three-request control run recovered readable page text and marked
all three items relevant, but it is NOT successful current-batch recovery. Lazyweb
and DRS returned `NO_ENTITY_MENTION`; Mecone produced the malformed provisional
name `Xan Wesley Law` by flattening adjacent name/degree blocks. Footer headings
also consumed navigation as alleged investor names. No canonical entity was
promoted. This isolated failed run remains unchanged for diagnosis:
`0aa70b74-2a81-452f-9046-e7ec5c602e61` in temporary root
`vi-sr007-corrected-iemwyj2c`. It must not be counted as identity corroboration
despite that run's heuristic counter. No operational store was used.

A subsequent offline regression now preserves hard boundaries between HTML layout
blocks, preventing those blocks from forming identity/investor prose assertions.
Valid inline founder statements still pass; head/script/style/template claims do
not. The original Mecone layout negative has not been live-revalidated after this
boundary correction. These controls revealed a further extraction gap for explicit
company-only cohort statements, plus unresolved structured founder/team layouts. Do not patch
the acceptance harness with expected names or treat image-only backing logos as
cohort proof.

A narrow company-only extraction correction now recognizes a single-token company
subject in an explicit `has joined [registered program]` assertion, leaving the
founder unknown. Existing person/multiword ambiguity handling and generic-label
rejections remain in force; this is not automatic identity verification. Tests
cover current YC/Speedrun, old-cohort separation, and rejected platform labels.
One subsequent application request recovered **Lazyweb** from the First 1000
announcement as a `suspected` company hypothesis with one origin and no founder
or canonical promotion. This was a known-control page, NOT blind discovery.
Replay selects zero observations before observation time and one at it; repeated
results match and the database hash is unchanged. Run
`a4c11392-92f4-4e3a-8f2f-740a4689c91c`, temporary root
`vi-sr007-membership-33zu3op5`. The heuristic `identity_corroborated` counter is not
evidence of multiple independent sources: this run has exactly one origin.
Actual API reads returned 200 for health, Speedrun listing, and candidate detail;
the founder remains null, the source URL/provenance are retained, YC excludes the
candidate, and the Research HTML redirect preserves its candidate/view context.
The API context shut down cleanly. This is not browser JavaScript/WebGL acceptance.

Latest focused P0 regression result: **119 passed**, with one existing
Starlette/httpx TestClient deprecation warning. Python compile checks passed.
The one final comprehensive release gate remains intentionally pending P0 closure;
these focused results must not be reported as the complete repository suite.

Next unfinished P0 slice: correctly attributed team records and the remaining
current SR007 controls through existing generic application boundaries, with
negative layout/identity tests first, then recheck only failed controls. Preserve
the successful unknown discovery and SMTP receipt; do not rerun passed controls.
Remaining acceptance includes current-control Research/browser relationships,
dedicated Product Hunt/GitHub discovery roles (prior enrichment successes are
not discovery proof), assistant/current/provisional behavior, complete operational
and browser acceptance, focused source ablation, and the ONE final comprehensive
quality gate after demonstrated blocker fixes. Do not polish P2 while P0 is open.

## Final live acceptance continuation: 2026-09-07

The four retained public directory controls were reprocessed through the actual
EventBus/pipeline, without refetching them or changing raw evidence: Async and
Palette remain YC S26; SUN and Sparta remain Speedrun SR006. Explicit attributed
founder records produced seven canonical people and seven FOUNDED edges. Unprofiled
or non-founder team claims remain raw claims, not canonical founders. Directory
verification is not unknown-candidate discovery.

One additional public Supabase directory control (S20) established its canonical
company before enrichment. The authenticated GitHub adapter returned three real
public repository events; two matched Supabase's official website domain and
reached company Research evidence. The third stayed an unassociated repository.
Product Hunt's official GraphQL API returned the Supabase (Alpha) launch; its public
website redirect was checked using the shared bounded crawler and matched the
existing company. The launch reached canonical company evidence. These two
adapters used five shared-budget requests, excluding the directory fetch and an
earlier one-request Product Hunt diagnostic. Neither enrichment path created
founders or program-membership claims. No tokens were serialized.

The prior bounded unknown-candidate run below remains the discovery-yield result;
it was not repeated. Indexed social remains live-unavailable/drifting, not a pass.
The older per-source table is historical coverage, not a blanket readiness claim.

## Final simplification pass: 2026-09-06

Exactly one gated isolated venture-discovery run was executed. It did not mutate
operational storage. Its known-positive controls used 23 of 24 requests; discovery
used its nine-request allocation. Unknown hypotheses, promotion-eligible candidates
and canonical promotions were all zero. Precision and recall are unavailable, not
100%. The result is `degraded_zero`, not successful venture discovery.

Product Hunt official GraphQL returned one accepted normalized observation.
Authenticated GitHub REST returned one repository control; search quota was 30,
with 29 then 28 remaining. Both tokens remained server-side. arXiv, Wayback control,
Hacker News, TechCrunch and the newsletter watchlist returned control results.
DuckDuckGo/Bing/Yahoo returned general controls, but indexed social controls did
not validate: HTTP202/circuit protection or off-domain index results. Brave was
rate-limited on its control. Wayback's later venture probe was unavailable.
VC watchlist discovery exhausted its budget. No direct restricted-social requests
were enabled or made. These outcomes do not prove every connector live-operational.

Maigret and Sherlock both completed through the actual bounded application adapter
using the public test username `torvalds`, three permitted sites each. Maigret:
11.94 seconds, three returned/accepted/ambiguous clues, zero rejected. Sherlock:
15.05 seconds, three returned/accepted/ambiguous clues, zero rejected. Neither
created, merged or promoted an identity. The original report counted five origins;
this exposed the known github.com/www.github.com alias defect. After its correction,
offline reprocessing of the recorded URLs produced four origins and two cross-tool
duplicates. The immutable original live report was not rewritten to claim the
corrected code had already run live.

Generic website/blog/newsletter/directory/Wellfound/widget boundaries were not
separately live-validated in this run. RSS watchlist success is not a substitute
for those adapters' independent acceptance. Google Scholar remains disabled and
contract-only. Discord authorized API and Firecrawl remain optional, disabled
boundary-only integrations; neither blocks core startup. No new framework/source
was added. SpiderFoot, Holehe and OpenOSINT have no active implementation in the
audited backend/frontend/config/dependency/test paths.

| Connector | Source family | Role | Default enabled | Access mode | Official API available externally | Official API implementation status | Credential/config | Known-positive | Live validation | Health | Yield | Fallback | Evidence authority | Limitations |
|---|---|---|---:|---|---|---|---|---|---|---|---:|---|---|---|
| DuckDuckGo | search | DISCOVERY | yes | public HTML index | no | OFFICIAL_API_NOT_SUPPORTED | none | SUCCESS | live | healthy/volatile | 3 control | Bing/Yahoo/Brave | discovery only | HTTP 202 observed |
| Bing | search | DISCOVERY | yes | public HTML index | API not used | OFFICIAL_API_NOT_SUPPORTED | none | SUCCESS | live | healthy | 3 control | DDG/Yahoo/Brave | discovery only | index relevance varies |
| Yahoo | search | DISCOVERY | yes | public HTML index | no | OFFICIAL_API_NOT_SUPPORTED | none | HTTP 500 | live | unavailable | 0 | DDG/Bing/Brave | discovery only | upstream 500 observed |
| Brave | search | DISCOVERY | yes | public HTML index | yes | OFFICIAL_API_NOT_SUPPORTED | none | HTTP 429 | live | rate limited | 0 | DDG/Bing/Yahoo | discovery only | official API not integrated |
| Public program announcements | indexed public web | DISCOVERY, PROGRAM | yes | search discovery | no | OFFICIAL_API_NOT_SUPPORTED | program registry | not isolated | live | circuit-open in final run | 0 | search providers | discovery only | no direct social access |
| Accelerator directory | directory | IDENTITY, PROGRAM | yes | public HTML/API | varies | YC public page + Speedrun official public API | program registry URL | SUCCESS | live current SR007 | healthy in tested window | 20 companies / 40 founders | official websites | verification | never seeds unknown candidates; shared collection URL is provenance, not an identity key |
| Triangulation | public pages | DISCOVERY, PROGRAM | yes | public HTML | no | OFFICIAL_API_NOT_SUPPORTED | program URLs | not run | contract/integration | not run | 0 | search discovery | both | configured pages only |
| Website | first-party | IDENTITY, ACTIVITY | yes | public HTML | no | OFFICIAL_API_NOT_SUPPORTED | program URLs | not run | contract/integration | not run | 0 | search/archive | verification | first-party pages only |
| Blog | publication | DISCOVERY, ACTIVITY | yes | public HTML | no | OFFICIAL_API_NOT_SUPPORTED | program URLs | not run | contract/integration | not run | 0 | RSS/search | both | configured pages only |
| Newsletter connector | newsletter | DISCOVERY, ACTIVITY | yes | RSS | no | OFFICIAL_API_NOT_SUPPORTED | feed URLs | SUCCESS via watchlist | live | healthy | 3 control | public pages | both | configured feeds only |
| Generic RSS/Atom | feed | DISCOVERY, ACTIVITY | yes | RSS/Atom | no | OFFICIAL_API_NOT_SUPPORTED | feed URLs | covered by feed controls | live/contract | healthy where configured | 3+ | public page | both | per-feed drift possible |
| TechCrunch | publication/news | DISCOVERY, FUNDING, TEMPORAL | yes | RSS | no | OFFICIAL_API_NOT_SUPPORTED | watchlist | SUCCESS | live | healthy | 3 control | public article/search | both | query yield separate from feed health |
| Substack/newsletter watchlist | newsletter | DISCOVERY, FUNDING, INVESTOR | yes | RSS | no | OFFICIAL_API_NOT_SUPPORTED | watchlist | SUCCESS | live | healthy | 3 control | indexed public page | both | configured public feeds only |
| VC watchlist | investor pages | DISCOVERY, FUNDING, INVESTOR | yes | public HTML | no | OFFICIAL_API_NOT_SUPPORTED | watchlist | not isolated | live | partial | 1 normal | search/archive | both | bounded page set |
| Indexed LinkedIn | social index | DISCOVERY | yes | permitted search index | yes | OFFICIAL_API_NOT_SUPPORTED | none | parser/control only | live attempted | externally degraded | 0 useful venture claims in final window | other indexes/manual review | discovery only | direct requests zero; an indexed URL alone is not a successful social observation |
| Indexed X | social index | DISCOVERY | yes | permitted search index | yes | OFFICIAL_API_NOT_SUPPORTED | none | parser/control only | live attempted | externally degraded | 0 useful venture claims in final window | other indexes/manual review | discovery only | direct requests zero; an indexed URL alone is not a successful social observation |
| Indexed Instagram | social index | DISCOVERY | yes | permitted search index | yes | OFFICIAL_API_NOT_SUPPORTED | none | not demonstrated | attempted | drift/unavailable | 0 accepted | other indexes/manual review | discovery only | direct requests zero |
| Indexed Threads | social index | DISCOVERY | yes | permitted search index | yes | OFFICIAL_API_NOT_SUPPORTED | none | not demonstrated | attempted | drift/unavailable | 0 accepted | other indexes/manual review | discovery only | direct requests zero |
| Social direct connector | restricted social | DISCOVERY | no by policy | permission-required public web | yes | OFFICIAL_API_NOT_SUPPORTED | explicit access-mode approval | not run | disabled | not run | 0 | indexed search | discovery only | never enabled by default |
| LinkedIn direct connector | restricted social | DISCOVERY, IDENTITY | no | permission required | yes | OFFICIAL_API_NOT_SUPPORTED | explicit approval | not run | disabled | not run | 0 | indexed LinkedIn | discovery only | no official API adapter |
| Discord widget | community | DISCOVERY | yes | PUBLIC_EMBED | yes | OFFICIAL_API_IMPLEMENTED_NOT_CONFIGURED | public guild/widget IDs | not configured | contract | not configured | 0 | indexed/public announcements | discovery only | no autonomous person creation; server owner must enable widget |
| Discord authorized API | community | DISCOVERY | no | AUTHORIZED_OFFICIAL_API | yes | OFFICIAL_API_BOUNDARY_ONLY | bot token + explicit guild/channel allowlist | OPTIONAL_NOT_CONFIGURED | boundary only | not configured | 0 | public widget/indexed announcements | discovery only | read-only least privilege; no self-bots/user tokens; optional for V1 |
| GitHub | technical | IDENTITY, TECHNICAL | yes | official public API | yes | OFFICIAL_API_LIVE_VALIDATED | optional read-only `GITHUB_TOKEN` | SUCCESS | live authenticated | healthy | 1 control | public unauthenticated API | both | venture query may be empty; latest authenticated quota telemetry was 29 remaining after the control |
| Hacker News | community/news | DISCOVERY, ACTIVITY | yes | Algolia public API | no official HN API used | OFFICIAL_API_NOT_SUPPORTED | none | SUCCESS | live | healthy | 3 control | search index | both | Algolia is the acquisition API |
| arXiv | publication | PUBLICATION | yes | official API/package | yes | OFFICIAL_API_LIVE_VALIDATED | none | SUCCESS | live | healthy | 3 control | public web/search | both | enrichment cannot seed identity |
| Google Scholar | publication index | DISCOVERY, PUBLICATION | no | public HTML | no supported official API | OFFICIAL_API_NOT_SUPPORTED | explicit enable | not run | fixture/contract | not run | 0 | arXiv/public search | discovery only | no anti-bot circumvention |
| Wayback | historical | HISTORICAL | yes when invoked | official CDX API | yes | OFFICIAL_API_IMPLEMENTED_NOT_CONFIGURED | none | unavailable final run | live attempted | volatile | 0 final control | current page/search | both | adjacent normal run succeeded |
| Product Hunt | product | DISCOVERY, ACTIVITY | no until configured | AUTHORIZED_OFFICIAL_API | yes | OFFICIAL_API_LIVE_VALIDATED | developer token + enable flag | SUCCESS | live authenticated | healthy | 1 control | indexed/archive evidence | discovery only | targeted company corroboration; never program-membership authority; operator owns upstream deployment/use compliance |
| Wellfound | jobs/company | DISCOVERY, ACTIVITY | yes connector | public HTML | no public API used | OFFICIAL_API_NOT_SUPPORTED | none | not run | contract/failure fixtures | not run | 0 | indexed public evidence | discovery only | login-required becomes failure |
| Maigret | username OSINT | IDENTITY, ENRICHMENT | no; gated only | bounded local tool | no | OFFICIAL_API_NOT_SUPPORTED | executable on PATH | LIVE_VALIDATED | live one-site control | healthy | 2 clues | manual JSON import | clue only | Windows requires threaded DNS; username equality is not identity |
| Sherlock | username OSINT | IDENTITY, ENRICHMENT | no; gated only | bounded local tool | no | OFFICIAL_API_NOT_SUPPORTED | executable on PATH | LIVE_VALIDATED | live | healthy | 3 clues | manual JSON import | clue only | username equality is not identity |
| Firecrawl | optional crawler | DISCOVERY | no | official API boundary | yes | OFFICIAL_API_BOUNDARY_ONLY | URL and API key | not configured | boundary only | not configured | 0 | built-in crawler | discovery only | no registered live client |
| Local Ollama/Qwen | analyst/extractor | non-authoritative analysis | no hosted dependency | local process | n/a | OFFICIAL_API_NOT_SUPPORTED | optional local Ollama | outside source pass | optional | not assessed | n/a | deterministic core | non-authoritative | never required for collection |

The machine-readable forms are `GET /api/v1/sources` (declared policy) and `GET
/api/v1/source-quality` (latest measured report). Neither endpoint performs live
collection.

## Non-secret configuration presence in the final run

| Setting | Presence/state | Effect |
|---|---|---|
| `GITHUB_TOKEN` | configured | Optional fine-grained read-only token used only with the official API; quota telemetry retained and secret omitted |
| `PRODUCT_HUNT_ACCESS_TOKEN` | configured | Official Product Hunt GraphQL known-positive returned HTTP 200; secret omitted |
| `DISCORD_BOT_TOKEN` | not configured | Optional authorized official-API boundary remains disabled; public-widget behavior is unchanged |
| `FIRECRAWL_API_URL` | not configured | Optional boundary remains inactive |
| `FIRECRAWL_API_KEY` | not configured | No Firecrawl request can execute |
| `RESEARCH_SCHOLAR_ENABLED` | false | Scholar is skipped, not reported as a live pass |
| `RESTRICTED_SOCIAL_ACCESS_MODE` | `search_discovery` | Direct LinkedIn/X/Instagram/Threads requests remain disabled |
| `INTELLIGENCE_WATCHLIST_PATH` | configured repository watchlist | TechCrunch, newsletter/Substack, and VC page controls use public URLs only |
| `SOURCE_SETTINGS_PATH` | configured local JSON path | Holds enable/disable overrides; no credentials |
| `SOURCE_QUALITY_REPORT_PATH` | configured local JSON path | Stores the latest non-secret atomic report |
| `VENTURE_INTELLIGENCE_LIVE_TESTS` | required only during CLI run | Has no effect without `--allow-network` |

Secret values are never serialized into the report, API, CLI output, or this
matrix.

