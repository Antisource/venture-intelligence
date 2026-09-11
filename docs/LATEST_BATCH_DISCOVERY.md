# Latest-batch discovery (M8)

## Discovery relevance correction (2026-09-02)

Candidate extraction and accelerator assessment are separate boundaries. Generic
venture intents are always present alongside registry-derived verification intents, and
`POST /api/v1/batch-discovery/runs` accepts an omitted `program_id`. Such a run
stores provisional evidence and hypotheses in the explicit
`generic_venture`/`unscoped` scope; it cannot manufacture program evidence or
promote an accelerator member. Selecting YC, Speedrun, or another configured
program only adds terms from `ProgramRegistry` and activates the independent
program/batch assessment.

Public search parsing now retains indexed snippets. A relevant result that lacks
an extractable identity may receive one bounded direct-public article fetch
through the shared crawler and request budget. LinkedIn, X, Instagram, and
Threads destinations are never fetched directly. Founder-only, company-only,
and founder-plus-company claims may survive as weak hypotheses; they remain
blocked from promotion until the unchanged identity, provenance, independent
origin, and program-evidence gates pass.

Rejections distinguish insufficient content, non-venture context, no candidate
claim, no entity mention, ambiguous role, restricted destination, known entity,
and time-window failures. Query text, source labels, platform names, and URL
slugs remain forbidden as identities.

M8 answers a different question from target research: **who might have joined a
program even though the official directory does not list them yet?** The
workflow can begin without a founder or company name. Official directories are
verification and fallback sources only; a directory row is deliberately
ignored during the unknown-candidate discovery stage.

The current live YC scope is `yc_current` / **F26 (Fall 2026)**. S26 remains a
supported historical batch under that same canonical program identity. The UI,
API, CLI, query keywords and directory verification URL derive the selected
batch from `ProgramRegistry`; replay/evaluation can explicitly select S26.

## Data flow and integrity boundary

```text
bounded public/indexed signals
  -> immutable raw observation
  -> exact candidate hypothesis cluster
  -> identity and batch assessment
  -> promotion gate
       -> validated CanonicalEvent(s), or
       -> retained uncertain/rejected hypothesis
  -> enrichment only after a discovery hypothesis exists
```

`batch_discovery_observations` is append-only evidence. A revisioned
`batch_candidate_hypotheses` row records the assessment derived from those
observations. Candidate clustering uses normalized exact person/company
identity, aliases, stable usernames, domains and URLs. The M8 boundary does not
use fuzzy names to merge people or companies. Its tagged events also bypass
the resolver's legacy fuzzy-name fallback, preventing similarly named startups
from converging accidentally.

A query, source label, connector name, URL slug, technology or platform name is
not an identity. Generic candidates such as `twitter`, `github`, `arxiv` and
`keyframes` cannot create an observation or canonical event. Enrichment
evidence cannot by itself prove program participation.

## Persisted discovery funnel and zero states

Every run persists the classification made at collection time rather than
reconstructing it from logs. The authoritative funnel is:

```text
retrieved_items -> relevant_items -> venture_observations
  -> candidate_hypotheses_created / candidate_hypotheses_updated
  -> identity_corroborated -> batch_evidence_present
  -> promotion_eligible -> canonical_promotions
```

Each transition retains typed drop counts such as `IRRELEVANT_TO_VENTURE`,
`NO_EXTRACTABLE_ENTITY`, `INSUFFICIENT_IDENTITY`,
`DERIVATIVE_DUPLICATE`, `NO_BATCH_EVIDENCE`,
`INSUFFICIENT_INDEPENDENT_ORIGINS`, `DOMINANT_CONTRADICTION`, and
`BELOW_PROMOTION_THRESHOLD`. Per-provider/source-family yield records retrieved,
relevant, accepted, rejected, independent-origin, hypothesis, funding,
investor, duplicate, and promotion-attribution counts. Watchlist entries also
retain publication/actor-level yield. These metrics describe usefulness; the
separate normalized source status describes health.

Zero yield is classified as `healthy_zero`, `degraded_zero`, `pipeline_zero`,
or `threshold_zero`. A successful empty search is therefore not presented as a
system failure, while a blocked acquisition path is not presented as evidence
that no result exists.

## Discovery, verification and enrichment

1. **DISCOVERY** runs bounded, program-derived announcement, newsletter, public
   website and indexed-social queries. Results must explicitly name a plausible
   founder/company and contain either a venture signal or a relevant
   batch/program claim. A credible launch, funding, founder-activity, or VC
   signal can create a weak analyst-visible hypothesis with `batch evidence =
   false`; it does not create membership or a canonical entity.
2. **VERIFICATION** searches for the proposed identity and batch claim. It can
   consult a configured official program directory only after the hypothesis
   exists and distinguishes support, contradiction, another program/batch,
   ambiguous identity and stale evidence.
3. **ENRICHMENT** looks for technical/publication identity from GitHub, Hugging
   Face, PyPI/npm, arXiv and ORCID. It enriches profile context and never turns
   enrichment availability into membership confidence.

Every source/tool role and acquisition boundary is returned by
`GET /api/v1/batch-discovery/contract`. `GET /api/v1/sources` remains the
application-wide M1 provider/connector policy inventory.

## Explainable confidence

Assessment is deterministic and non-monotonic. It records the exact observation
IDs used by each confidence component:

- independent current support, source reliability, directness, identity match
  and temporal relevance increase confidence;
- derivative copies share an origin and do not count as independent sources;
- contradictions, another batch/program, ambiguous identity and stale evidence
  reduce confidence;
- confidence is capped at 95%;
- promotion requires at least 72% batch confidence, 80% identity confidence,
  two independent supporting origins, a company identity, and no dominant
  contradiction.

Failure to pass leaves the candidate outside entities, EventBus downstream
queues, graph, ranking, recommendations and CRM. The React inspector says
`Insufficient evidence — candidate not promoted` and exposes the blockers.
Venture discovery, batch confidence, and investment attention remain separate.
A strong funding/technical signal can raise investment attention while the
candidate remains `needs_accelerator_investigation`; high batch confidence does
not force a high investment-attention score.

Candidate JSON also carries deterministic `why_now`, exact promotion blockers,
and `next_best_checks`. The latter is derived from applicable, unchecked source
families and does not use an LLM.

## Coverage and applicability

Source Coverage is `successfully checked applicable families / applicable
families`. Each family stores `applicable`, a deterministic reason, and one of
supporting, contradicting, checked-no-evidence, unavailable,
permission-required, not-checked, or not-applicable. Only the first three count
as checked. Circuit-open, rate-limited, timeout, parser failure, unavailable,
permission-required, and not-configured states do not count. Technical and
historical families become applicable only when identity/profile signals make
them relevant; universal public identity, program, startup-media, VC,
newsletter, indexed-social, and public-web checks remain applicable.

Coverage never contributes to confidence. The API can therefore truthfully
return states such as `HIGH CONFIDENCE · LOW COVERAGE` or low confidence after
complete checking. The same server-derived coverage is used by API, CLI, and
observed-at replay; React must not recalculate it.

## Evidence origin and acquisition path

`origin_url`, `publisher`, `claim_fingerprint`, `referenced_source`, and
`derivative_of` are stored separately from `acquisition_providers` and
`acquisition_paths`. Bing, Brave, and DuckDuckGo finding the same TechCrunch URL
means three retrieval paths but one evidence origin. A newsletter or VC repost
that merely links to that article remains derivative. A VC portfolio page or
partner statement adding an explicit investment claim is a new origin. When
independence cannot be established, evidence is not automatically awarded an
independent-support vote.

## Funding and investor claim semantics

Financial claims preserve `claim_kind` (`round_amount`, `total_funding`,
`target_raise`, `valuation`, `fund_size`, `revenue`, or unknown), `status`
(`announced`, `closed`, `reported`, `targeting`, `rumored`, or unknown), explicit
round type, source, claim text, and conflict links. `financial_claims` contains
the full typed history; compatibility field `funding_rounds` contains only
actual round-amount claims. A target, valuation, fund vehicle, or ARR statement
cannot emit a canonical funding-round event. Conflicting round amounts remain
visible until authoritative evidence resolves them.

Investor relationships distinguish participated, invested, led, co-led,
backed, portfolio-company, and mentioned. Only explicit led/co-led semantics
create `LED_INVESTMENT_IN`; all weaker investment relationships use
`INVESTED_IN`. A firm claim without an identified partner creates an
organization relationship and never invents a person.

## Material change and human judgment

Analyst-facing changes require a new hypothesis, a lifecycle transition, a new
independent origin, a contradiction/funding/investor/program-evidence change,
or a confidence movement of at least five percentage points. An identical
source re-check, derivative copy, retry, metadata-only change, or one-point
recomputation is retained in run history but does not create Inbox/digest churn.
Coverage-only no-evidence checks are likewise historical unless an analyst is
explicitly inspecting investigation completion.

CRM judgments are append-only actions. The typed vocabulary includes confirmed,
dismissed, duplicate, identity-corrected, priority-changed,
investigation-requested, outreach, meeting, diligence, pass, and invest (along
with the existing workflow actions). Each preserves actor, timestamps, note,
ranking context, evidence-state provenance, and never edits immutable evidence.

## Source and restricted-social policy

| Source boundary | Roles | Access | Can seed discovery? |
|---|---|---|---|
| DuckDuckGo/Bing/Yahoo/Brave public indexes | DISCOVERY | public search | Yes |
| TechCrunch public RSS | DISCOVERY, FUNDING, MARKET_SIGNAL, TEMPORAL_NEWS | public feed | Yes |
| Newsletter/Substack watchlist | DISCOVERY, FUNDING, INVESTOR_SIGNAL, TEMPORAL_NEWS | public feed | Yes |
| VC/partner watchlist | DISCOVERY, FUNDING, INVESTOR_SIGNAL, TEMPORAL_NEWS | public pages | Yes |
| Public program announcements/indexed references | DISCOVERY, VERIFICATION | public search/indexed reference | Yes |
| Official program directory | VERIFICATION | public direct | No |
| Bounded public-page verifier | IDENTITY, VERIFICATION | policy-checked public direct | No |
| GitHub/Hugging Face/PyPI/npm/arXiv/ORCID | IDENTITY, ENRICHMENT | public search/direct | No |
| Wayback | HISTORICAL | public search/direct | No |
| Maigret/Sherlock | IDENTITY, ENRICHMENT | offline import or explicitly gated bounded local tool | No |

The permitted social boundary remains:

```text
public search provider -> indexed LinkedIn/X/Instagram/Threads reference: allowed discovery
venture-intelligence -> direct LinkedIn/X/Instagram/Threads automation: disabled by default
```

No account automation, CAPTCHA solving, proxy rotation, stealth browser,
authenticated scraping or private-source access is added by M8.

## OSINT tool benchmark

The exposed benchmark is a deterministic offline representative-fixture
comparison, not a claim that live internet runs occurred in this release. It
records incremental discoveries, useful corroborations, false matches, request
cost, runtime, structured-output quality, provenance quality and policy
compatibility.

| Tool | Decision | Reason |
|---|---|---|
| [SpiderFoot](https://github.com/smicallef/spiderfoot) | REJECT | Broad target-first orchestration would duplicate the bounded collector and cannot share its exact request budget safely. |
| [Maigret](https://github.com/soxoj/maigret) | ADAPTER_ONLY | Manual JSON and a gated bounded local diagnostic are implemented. The Windows adapter uses Maigret's threaded DNS resolver; a one-site GitHub known-positive returned two ambiguous profile clues. Username equality is never canonical identity proof. |
| [Sherlock](https://github.com/sherlock-project/sherlock) | ADAPTER_ONLY | Manual JSON and a gated three-site local diagnostic are implemented. Sherlock was live-validated end to end, but every result remains an ambiguous clue and cannot promote an identity. |
| [Holehe](https://github.com/megadose/holehe) | REJECT | Password-recovery/account-existence probing is outside this product's policy and privacy boundary. |

## API and frontend

- `GET /api/v1/batch-discovery/contract`: stages, taxonomy, access/source matrix,
  promotion thresholds and offline tool benchmark.
- `GET /api/v1/batch-discovery?program_id=...`: latest hypothesis revisions.
- `GET /api/v1/batch-discovery/{hypothesis_id}`: candidate plus all immutable
  supporting/contradictory observations.
- `POST /api/v1/batch-discovery/runs`: bounded discovery-first run. Partial
  evidence survives budget exhaustion and the run reports `status=partial`.
- `POST /api/v1/batch-discovery/manual-observations`: typed public evidence
  import with provenance; it is not a direct canonical-entity write.

API and CLI runs drain promoted events through the same validation, resolution,
evidence, graph, signal, ranking and recommendation workers used by the monitor.
The shared intelligence-cycle service provides the same orchestration for
manual and scheduled operation. External email/Slack delivery remains an
explicit operator action.

The native `/#/discover` workspace presents separate company, founder, and
program views. Its opportunity table shows the identity, founder/company
context, why the record surfaced, its evidence state, the latest change, and a
next action. Selecting a record opens Research, where confidence reasoning,
negative or derivative evidence, promotion blockers, timeline/provenance,
profile fields, relationships, and decision context are available. Facts remain
server-provided. Inbox views include funding, VC backing, partner mentions,
newsletter, TechCrunch, indexed social, and low-coverage hypotheses.

Funding and investor observations never increase batch confidence. They receive
a separate deterministic investment-attention score and, only after canonical
promotion, create evidence-backed `INVESTED_IN`/`LED_INVESTMENT_IN` graph edges.
The same article returned by multiple search engines remains one evidence origin
with all acquisition paths retained in provenance.

## Historical acceptance

Replay selects immutable observations using `observed_at <= cutoff`, rebuilds
hypotheses deterministically, and asserts that live state counts do not change.
The offline acceptance fixture starts with no supplied identity and no official
directory row for Founder A, discovers Founder A from an indexed announcement,
deduplicates a derivative copy, verifies with independent public evidence,
promotes the valid candidate, enriches a technical profile, and reproduces the
result historically. A misleading candidate with contradiction/other-batch
evidence remains rejected and never emits an entity event.

The deterministic evaluator reports Precision@K, recall, false-positive rate,
missed identities, identity-resolution errors, lead time, source contribution,
per-source promotion-loss ablation and a Brier score for batch-confidence
calibration. It consumes the isolated hypothesis/observation snapshot and does
not read current CRM or rankings.

Live third-party availability is intentionally separate from this offline
implementation validation. Source drift, blocks and empty search results are
structured outcomes and do not weaken promotion requirements.

M9 operational validation exercises this workflow in a disposable database and
persists a typed source-quality report. The final 2026-09-01
intelligence-quality run found zero live candidates: 24 items were retrieved,
21 were classified `IRRELEVANT_TO_VENTURE`, and 3 relevant Brave results were
classified `NO_EXTRACTABLE_ENTITY` before observation creation. Indexed-social
queries ran successfully through Bing and Brave while DuckDuckGo and Yahoo were
unavailable; no direct social request occurred. TechCrunch feed, newsletter,
VC-watchlist, and arXiv public paths were reachable; Wayback was unavailable.
See
[`OPERATIONAL_VALIDATION.md`](OPERATIONAL_VALIDATION.md).
