# Revamp baseline audit: findings

Scope: read-only audit at HEAD `c563225`, whose product code is identical to `main`
`88609e6`. Line references are `path:line` from the repository root at that baseline.
Findings marked **local runtime evidence** come from read-only inspection of the
operator's untracked `data/` directory (counts only; no addresses or secrets). That
evidence is not in the repository and will differ on other installs.

Baseline gates (run 2026-11-20): `uv run pytest -q` 347 passed; `ruff` clean;
`pnpm lint`, `typecheck`, `test` (53), `build` pass. No CI existed.

## Verdict summary

| # | Claim in the brief | Verdict | Correction |
|---|---|---|---|
| 1 | The repo has a single commit | Confirmed for `main` | `main` has 1 commit; it squashes milestones M1–M9, so history explains no decision |
| 2 | No investment-thesis feature | Confirmed, with a correction | No thesis/mandate/criteria exists, but `strategic_fit` (weight 0.06) claims "MFC thesis" alignment and only measures program affinity plus CRM feedback |
| 3 | Founder card is partial | Confirmed, worse than partial | 4 of 9 ranking dimensions have no data producer; no dimension is rendered; max local composite score is 0.073 |
| 4 | No active identity disambiguation | Partially true | Same-name evidence stays unbound passively and merge rules are conservative for people, but there is no active disambiguation, merge/split or review queue; non-research sources are auto-attached; companies fuzzy-merge at 0.82 with no review |
| 5 | Email undiagnosed or broken | Confirmed | Automated alert email is structurally impossible (thresholds unreachable, scheduled cycles use a no-op notifier, daily/weekly never drains). "Sent without provider acknowledgement" is **corrected**: SMTP acceptance is required; the inverse bug exists (alert lifecycle says DELIVERED when email was suppressed) |
| 6 | UI exposes plumbing | Confirmed | Lineage, formulas and versions render on the default Research overview; System pages are top-level navigation and dump raw JSON, request counts and digests |

## 1. Commit history

- `git rev-list --count origin/main` is 1 (`88609e6 Initial public release`, 2026-09-11).
- The single product commit squashes milestones M1–M9 (see `docs/ARCHITECTURE.md:83-125`),
  so `git blame` explains nothing. Design rationale lives only in `docs/*.md` and comments.
- **Consequence:** `PARITY.md` and the test suite are the only regression oracle. The
  baseline stays reachable as `revamp-main` @ `88609e6` until promotion.

## 2. Investment thesis

- No thesis, mandate or criteria model, config, API or UI. A word-boundary search finds
  only comments, docstrings and the git-guard fixtures.
- `shared/models.py:358-368`: `DIMENSION_WEIGHTS["strategic_fit"] = 0.06  # sector
  alignment with MFC thesis`. "MFC" is an undefined, hard-coded firm reference.
- `pipeline/ranking_worker.py:213-231`: `strategic_fit = 0.75*program_affinity +
  0.15*human_feedback_positive + 0.10*realized_outcome - 0.20*human_feedback_negative`.
  No sector, stage, geography or check-size input.
- `ranking_worker.py:18` tells partners to tune `DIMENSION_WEIGHTS` in code; weights are
  a module constant with no config or API.
- `pipeline/recommendation_worker.py:20,208`: IGNORE says "below investment thesis
  thresholds"; the thresholds are fixed cut-offs (`:182-203`).
- **Consequence:** the thesis engine is net-new (S1). Replace `strategic_fit` with
  explainable per-criterion `thesis_fit`; move weights and recommendation thresholds into
  versioned thesis revisions; store `thesis_revision_id` on each ranking.

## 3. Founder card

Three things are called a "founder" view, and each is hollow.

**A. Nine-dimension `FounderRanking`** (`shared/models.py:358-405`, computed in
`pipeline/ranking_worker.py:140-261`, composite = Σ score × weight at `:263-265`).

- Produced signals (`pipeline/signal_worker.py:65-122`): PROGRAM_AFFINITY, TECHNICAL_DEPTH,
  HIRING_VELOCITY, RESEARCH_FOUNDER, STEALTH_STARTUP, OPEN_SOURCE_LEADER, human feedback,
  realized outcome. **REPEAT_FOUNDER is never computed** (declared only, `models.py:293`).
- **No connector emits `COMMIT_VELOCITY_HIGH`, `HIRING_VELOCITY_HIGH` or
  `SHOW_HN_POSTED`.** `JOB_POSTED` comes only from `WellfoundConnector`
  (`connectors/public_sources.py:421-424`).

| Dimension (weight) | Inputs | Data source status |
|---|---|---|
| technical_depth (0.20) | repos 30%, commits_7d 50%, stars 20% (`signal_worker.py:135-158`) | Partial: the 50% commit part has no producer |
| research_output (0.12) | arXiv `PAPER_PUBLISHED` / 2 | Present |
| hiring_velocity (0.12) | `open_roles_30d` / 5 | Effectively none (Wellfound only; 0 local signals) |
| network_quality (0.10) | 0.5 OSS stars + 0.5 REPEAT_FOUNDER | Half missing |
| founder_experience (0.15) | REPEAT_FOUNDER | None: always 0 |
| product_momentum (0.12) | 0.6 technical + 0.4 hiring | Degraded |
| community_reputation (0.08) | GitHub stars (comment promises HN karma, X following) | Partial |
| strategic_fit (0.06) | program affinity ± CRM feedback | Present but mislabeled (§2) |
| timing (0.05) | 0.5 program + 0.5 stealth | Partial |

- Local runtime evidence: 7,064 rankings, max composite 0.073, average 0.070; the top row
  scores 0.0 on 7 of 9 dimensions.
- The UI never renders dimensions; it shows `composite × 100` as "Attention score"
  (`ProductWorkspaces.tsx:105,142`, `IntelligencePages.tsx:47`).

**B. Hypothesis "Intelligence profile" card** (`frontend/src/features/research/ResearchWorkspace.tsx:48-50`,
computed in `batch_discovery/engine.py:722-902`).

- `background_claims` has no production producer (only the fixture at
  `tests/test_batch_discovery_m8.py:83` sets it), so "Background" always shows
  "Unknown / insufficient public evidence" and `founder_attention` is always 0.
- `investment_attention_pct` (a keyword heuristic) is **also** labeled "Attention score".
- `completeness_pct` (`engine.py:881-882`) is computed and never shown.

**C. Legacy company-evidence founder rows** (`api/app.py:761-798`) split names on
whitespace and hard-code `"founder_background": "Unknown / insufficient public evidence"`.

- **Consequence (S2):** one founder report card contract: nine dimensions, each with its
  producer, evidence links or an explicit insufficient-evidence state, and coverage shown
  ("4 of 9"). Absent dimensions drop out of the denominator instead of scoring 0. Give the
  two "Attention score" numbers distinct names. Remove or source REPEAT_FOUNDER, commit
  velocity and hiring, and mark unavailable any dimension without a permitted source.

## 4. Identity disambiguation

**What exists**

- Per-result attribution in research (`research/agent.py:180-223`): a person is ATTACHED
  only for exact name plus a LinkedIn `/in/` URL with founder context, an official program
  company page, or a declared first-party domain; everything else is UNBOUND with a reason
  code. Run-level anchors cannot bless sibling results (`agent.py:184`).
- `attribution_status ∈ {attached, unbound, contradictory}` (`intelligence/models.py:51`);
  identity status `verified/corroborated/provisional/ambiguous`
  (`intelligence/service.py:245-293`).
- Unbound evidence is excluded from knowledge claims (`knowledge/compiler.py:230-314,
  443-455`) and from ranking and CRM via `pipeline/entity_integrity.py:136`.
- Batch discovery uses domain identity anchors (`batch_discovery/engine.py:654-683`) and an
  AMBIGUOUS_IDENTITY penalty up to 0.18; Maigret/Sherlock results never promote
  (`batch_discovery/tools.py:111,135`).
- Resolver (`pipeline/entity_resolution.py`): a person merges only on exact profile URL and
  exact normalized name (`:292-315`).

**What is missing or weak**

- **Non-research sources are auto-attached** (`storage/db.py:1520-1522`): evidence from
  social, newsletter, blog, website, HN and GitHub connectors never gets an anchor check.
  This business rule lives in the storage layer.
- **Fuzzy company merge with no review** at SequenceMatcher ≥ 0.82
  (`entity_resolution.py:266-279`, threshold `config.py:92`); 0.72–0.82 near-misses are
  only log warnings.
- **Person fragmentation:** without an exact profile URL every event creates a new person;
  nothing links them later.
- **Corrections are labels only:** `DUPLICATE`, `IDENTITY_CORRECTED`
  (`shared/models.py:731-732`) and pass reason `wrong_identity` are recorded but nothing
  merges, splits or re-points evidence; the UI does not even offer them
  (`DecisionPages.tsx:15-26`).
- **"Verified" is source-based:** any attached evidence from a `directory` source yields
  `verified` (`intelligence/service.py:258-267`).
- **Consequence (S2):** anchor-based query planning and clustering applied to **all**
  sources; persisted candidate pairs with feature scores; a "Which person?" card whose
  answer is an append-only decision that re-points evidence; negative evidence lowers
  confidence; no auto-merge on name alone; attribution logic out of storage.

## 5. Email delivery

The SMTP adapter can work (local evidence: 1 test email and 3 digests recorded `sent`, last
on 2026-09-11), but automated alert email cannot happen and manual sends fail silently.

Path: `api/app.py:575-673` → `pipeline/email.py:44-273` (`EmailNotificationService`);
alerts: `monitor.py:106` → `pipeline/notification.py:57-159`; config `config.py:96-101`,
`.env.example:90-96`; CLI `cli.py:140-148, 484-499`; readiness `operations/readiness.py:97-105`.

**Failure causes, ranked by impact**

1. **Thresholds are unreachable.** MONITOR needs composite ≥ 0.40, CONTACT ≥ 0.70,
   ESCALATE ≥ 0.80 (`recommendation_worker.py:182-203`); IGNORE is never notified and
   MONITOR also needs confidence ≥ 0.80 (`notification.py:97-104, 252`). Real composites
   are about 0.07. Local evidence: all 6,834 recommendations are `ignore`; 0 alerts.
2. **Scheduled, CLI and API cycles never notify.** `operations/cycle.py:429-444` passes a
   `_LocalNotificationBoundary` whose `dispatch_pending()` returns 0 (same in
   `api/routers/batch_discovery.py:207, 227-229`). Only the monitor wires the real worker
   (`monitor.py:143`), and only when its own tick drained new events (`:105-110`), which
   cycles usually pre-empt.
3. **`frequency=daily|weekly` queues forever** (`email.py:203-204`): nothing reads `queued`
   deliveries and no digest scheduler exists. The local preference is `daily`.
4. **No recipient means silent `suppressed`** with no reason, even for forced test sends
   (`email.py:193-194`); the UI shows only "Test email: suppressed."
   (`OperationsPages.tsx:56`). Local evidence: destination unset; 104 of 120 records
   suppressed. Saving an empty field writes `null` (`OperationsPages.tsx:59`), and `load()`
   returns defaults on any error (`email.py:84-89`), which the next write then persists.
5. **The test email is deduplicated after its first success:** fixed id `test-email`
   (`email.py:144-150`) and a duplicate check that `force=True` does not bypass
   (`:207-211`). Digests have the same problem (content-hash ids).
6. **Port 465 is unsupported:** always `smtplib.SMTP` then STARTTLS (`email.py:229`), no
   `SMTP_SSL` path. The STARTTLS toggle is read with `os.getenv` only (`:230-233`), so a
   `.env` value is ignored (pydantic-settings does not export `.env` to `os.environ`).
7. **Silent auth skip and From misalignment:** AUTH runs only if user and password are both
   set (`:244-245`); From falls back to `venture-intelligence@localhost` via an
   `os.getenv`-only helper (`:282-289`), which Gmail/Outlook/SES reject or DMARC-fail.
   Host fallbacks share the `.env` blind spot, so readiness and send can disagree.
8. **Swallowed exceptions, weak diagnosis:** every exception is caught and truncated to 500
   characters without SMTP code or phase (`:250-255`); "CONFIGURED" means only that a host
   string exists. The notification lifecycle maps `{console sent, email suppressed}` to
   DELIVERED, and a failed email to PARTIAL that is never retried
   (`notification.py:72-82, 145, 249-251, 288-295`).
9. **Lost-update race and blocking I/O:** `send()` reloads the JSON file, holds SMTP for up
   to about 30 s, then writes stale data back (`email.py:190-273`), reverting concurrent
   settings saves; no lock or atomic write. SMTP and `time.sleep` run inside `async`
   routes and the monitor's shared event loop.
10. **The test suite writes to the operator's real delivery log:**
    `tests/test_crm_feedback_m5.py:198-199` uses `settings.email_config_path =
    data/email_notifications.json` and real SMTP settings from `.env`. Local evidence: 80
    suppressed records are that test's fixture alert. **With a destination set and
    `frequency=immediate`, running the tests would send a real email.**

- Only category `threshold_alerts` is ever sent (`notification.py:156`); the nine alert
  toggles in Settings control categories nothing emits, and `threshold_alerts` itself has
  no toggle.
- `smtp.starttls()` has no verified SSL context; `EMAIL_SMTP_PASSWORD` is a plain `str`.
- **Consequence (S1 root cause, S3 gateway):** a persistent outbox with a state machine
  (`queued → sending → accepted | failed(code, phase) | suppressed(reason)`) and idempotency
  keys; a scheduler that drains digests; transport chosen from typed settings (STARTTLS 587
  vs implicit TLS 465); a verified TLS context; `vi doctor email` that probes connect, TLS
  and AUTH without sending and names the failing stage; test sends never deduplicated;
  thresholds owned by the thesis; dispatch from cycles and the scheduler; test isolation.

## 6. UI plumbing in default views

- `/research/:entityId` default overview renders `IntelligenceLineage`: "Evidence → feature
  → signal → ranking" with feature `formula`, `feature_version`, `signal_version`, and a
  ranking-history table with `ranking_version` to 3 decimals (`ResearchWorkspace.tsx:43-44`,
  `IntelligencePages.tsx:50-66`).
- `?view=evidence`: "Acquired via {providers}" and `<details>Provenance</details>` with
  `JSON.stringify(item)`; raw observations likewise (`ResearchWorkspace.tsx:76, 81`).
- `/research?view=investigate` (default with no entity): budget brief "8 queries maximum ·
  20 second bound", `stop_reason` pill, hard-coded budgets (`ResearchPage.tsx:49, 80, 96`).
- `?view=network`: raw `JSON.stringify(node.properties)` and `replay_projection_digest`
  (`GraphPage.tsx:60, 261`).
- `?view=decisions`: `feature_version`, `recommendation_version`, `alert_version`
  (`DecisionPages.tsx:121, 157, 160, 179`).
- `/inbox`, `/discover`: "Identity verified · Program supported · N independent origins ·
  Attention score X%" on every row; "Refresh intelligence" runs a full cycle and links to
  `/system/runs` (`ProductWorkspaces.tsx:32, 105, 124, 136, 142`).
- `/system/sources`, `/system/runs`, `/system/evaluate`: provider credentials and
  capabilities, request/retry/latency tables, every cycle as `<pre>{JSON}</pre>`, digests
  (`FoundationPage.tsx:43-130`, `OperationsPages.tsx:73-101`, `EvaluationPage.tsx:125-129`).
- Sources, Runs, Evaluation, Replay and Settings are top-level sidebar peers of Inbox
  (`shell/navigation.ts:56-60`).
- **Consequence (S1 thin lenses, S5 polish):** the lens system moves plumbing behind L4 or
  an Advanced/audit disclosure. Every capability stays (PARITY rows) but moves.

## 7. Storage and API hotspots

| File | Lines | Shape | Responsibilities |
|---|---:|---|---|
| `storage/db.py` | 2,758 | 97 methods, 30 tables (+2 created lazily) | About 20, including the attribution business rule (`:1502-1532`) |
| `shared/models.py` | 1,226 | 61 classes | About 14 sections, plus dead Speedrun/accelerator constants (`:1202-1226`) |
| `api/app.py` | 927 | 32 routes, 2 SSE aliases, 2 WebSockets, static mount, SPA fallback | About 9, including raw SQL in 5 places and digest composition |
| `api/routers/v1.py` | 1,044 | 27 routes | About 12 domains, with health assembly (`:275-399`) and connector orchestration (`:876-1038`) inline |
| `api/routers/batch_discovery.py` | 241 | 5 routes | Routes plus a 114-line hard-coded capability catalog |

Cross-cutting smells:

- 28 raw `._connect()` uses outside `storage/`.
- `Database(settings.db_path)` is built on every v1 request (16 call sites), and each
  construction re-runs DDL, migrations and the legacy quarantine (`db.py:529-541`).
- All routes are `async def` but do synchronous SQLite and SMTP work on the event loop the
  monitor shares.

**Consequence (S0):** these four files are split into domain modules first, as the first
code links in the branch stack, so later branches touch disjoint files.

## 8. CLI entry points

All `argparse`. `venture-intelligence` (`cli.py:557`, global `--json`, `--database`) has:
`doctor`, `programs list`, `batches list`, `sources status|check`, `search providers`,
`feeds list|status`, `watchlist list|status`, `funding recent`, `investors recent`,
`partner mentions`, `discovery candidates`, `candidate show|evidence|investigate|export`,
`rankings list`, `changes list`, `inbox`, `graph show`, `research`, `analyst ask`,
`email preview|send --digest`, `cycle run`, `scheduler start|status|stop`, `runs list|show`,
`db status|fresh`, `reconcile`. Exit codes (`cli.py:567-593`): 0 ok; 1 broken/degraded,
failed cycle or storage error; 2 user action required; 3 cycle already running.

Module CLIs: `python -m venture_intelligence.monitor`, `.api.app`, `.replay.cli`,
`.evaluation.cli`, `.operations.cli` (duplicates `sources check` with different defaults:
8/24 versus 4/12). Scripts: `export_openapi.py`, `crawlee_public_fetcher.mjs`, git guards.

- **Defect:** the API advertises `uv run python -m venture-intelligence --json sources check …`
  (`api/routers/v1.py:431-434`); a hyphenated module name cannot run.
- **Consequence (S5):** `vi` (alias `venture-intelligence`) becomes AXI-conformant with
  backwards-compatible shims for every command above.

## 9. Scheduler, monitor loop and connectors

- **Monitor** (`monitor.py:120-155`): one `asyncio.gather` of the Uvicorn API, the pipeline
  tick every 60 s (validator → resolver → graph → evidence → signals, then ranking,
  recommendations and notifications only if entities changed), `AgentLoop` every 300 s, and
  one task per connector (poll every 1,800 s by default).
- **Scheduler** (`operations/cycle.py:447-474`): a separate foreground loop the monitor does
  not start (`venture-intelligence scheduler start --interval 12h`), with a PID-file lock
  and state in `data/operations/state.json`. The UI/API "Enable scheduler" only flips a
  flag. Local evidence: `enabled: true, running: false`; last run 2026-09-11, `partial`.
- **Connectors** (`connectors/factory.py:41-64`), 13 registered: Directory, Triangulation,
  Newsletter, Blog, Website, Social, Discord, LinkedIn, GitHub, HackerNews, Arxiv,
  ProductHunt, Wellfound. `FirecrawlConnector` exists but is not registered.
- **Research providers** (`research/sources.py`, policies `research/provider_policy.py:117-269`):
  DuckDuckGo, Bing, Yahoo, Brave, public program OSINT, arXiv, Google Scholar (off by
  default), Wayback, RSS, public watchlists, composite fallback. Local OSINT tools Maigret
  and Sherlock are gated by `VENTURE_INTELLIGENCE_LIVE_TESTS=1`.
- **Crawler engines:** httpx (default), Playwright rendering, Crawlee via Node.
- **Consequence:** "LinkedInConnector" and "SocialConnector" must be audited against the
  public-sources-only rule (no direct LinkedIn/X/Instagram crawling) in the S6
  signal-feasibility audit; the job queue and scheduler replace the PID-file loop (S0 ADR).

## 10. Other defects, dead code and security concerns

### Security

1. **Unauthenticated, wildcard-CORS mutation surface.** `app.py:79-84` allows all origins
   for GET/POST/PUT, and no route authenticates. Any page the operator visits can redirect
   alert email (`PUT /api/settings/email`), exfiltrate candidates over the operator's SMTP
   (`POST /api/settings/email/digest {"send":true}`), trigger network egress
   (`/api/v1/cycles/run`, `/api/v1/research`), change scheduler and sources, and append CRM
   actions. Loopback binding does not stop browser-originated requests or DNS rebinding.
2. **STARTTLS without certificate verification** (`email.py:233`); the SMTP password is a
   plain `str` (`config.py:100`).
3. **The whole `frontend/` directory is served at `/frontend`** (`app.py:87`), including
   sources; only `dist/` is needed.
4. **`/api/health` returns the database path** (`app.py:337-343`).
5. **Tests use the operator's real `data/` and `.env`** (§5 cause 10): `Settings` reads
   `.env` at import (`config.py:17, 169`) and no `conftest.py` isolates `data/`. This
   breaks the AGENTS.md rule that tests must not need credentials.
6. `db fresh --path` deletes an arbitrary path and its `-wal`/`-shm` files after
   `--confirm` (`cli.py:520-528`).

**Consequence:** items 1 and 5 are fixed in S0 (test isolation is a prerequisite for
safely running the suite; hosted mode needs auth, CSRF/CORS and tenant isolation); the rest
land in S6 hardening with the threat model.

### Correctness and quality

- Same label, two meanings: "Attention score" (§3).
- `GET /api/evaluation` always returns empty: `db.write_evaluation` has no callers.
- Dead branches in `app.py:361, 399` (`/founders` path checks never match).
- Two digest builders (`app.py:603-638`, `cli.py:151-163`), two analyst-context builders,
  and four constructions of `EmailNotificationService`.
- `system_readiness()` creates and migrates the database as a side effect.
- `GraphPage.tsx:228` "Return to Discovery" links to `/discovery`, which lands on `/inbox`.
- Unlocked JSON state files shared across processes (`email_notifications.json`,
  `source_settings.json`, `source_state.json`, `operations/state.json`).

### Dead or orphaned code

Speedrun/accelerator constants (`shared/models.py:1210-1226`);
`SignalWorker.process_entity_speedrun`; `ClassificationWorker` (tests only, so
`classifications` is never populated at runtime); unregistered `FirecrawlConnector`;
Discord bot settings read only by readiness; `evaluation/framework.py` (raises "removed in
M3"); unused frontend client methods (`runBatchDiscovery`, `batchDiscoveryContractQuery`,
`temporalContractQuery`); legacy `/api/*` endpoints with no frontend consumer (kept per
AGENTS.md and listed in `PARITY.md`); nine ranking dimensions and `completeness_pct`
computed but never rendered.
