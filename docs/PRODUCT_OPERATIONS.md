# Product operations

This is the operator and analyst runbook for the local-first product. Consult
SOURCE_ACCEPTANCE_MATRIX.md for dated source observations rather than treating
fixture coverage, prior validation, or a connector's presence as a blanket
claim about current live sources or production readiness.
The default YC target is **Y Combinator · F26 (Fall 2026)**. `yc_current` is the
single canonical YC program; S26 remains configured as historical data and is
used unchanged by replay/evaluation.
Speedrun targets SR007 through its public official directory API; SR006 is
explicitly available as a historical batch.

In Inbox/Discover, **All programs** includes provisional opportunities discovered
without an accelerator selection. Selecting one program excludes those unscoped
opportunities; it does not relabel them as members. Opening a provisional record
in Research preserves its observations, provenance, unresolved founder identity,
and separate identity/program confidence. It cannot enter Portfolio or the
canonical relationship graph merely because it appears in Discover.

## Install and start on Windows

From PowerShell in the repository root:

> This release is supported from a source checkout. PyPI and standalone wheel
> distribution are not currently supported.

```powershell
uv sync --all-groups
pnpm install --frozen-lockfile
pnpm build
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
uv run python -m venture_intelligence.monitor
```

Open `http://127.0.0.1:8000`. For frontend development, keep the backend on
8000 and run `pnpm dev`; Vite serves port 5173. FastAPI serves the current Vite
build at `/`. Inbox, Discover, Portfolio and Research are the primary workspaces;
the legacy application is retired.

## First checks

```powershell
uv run venture-intelligence doctor
uv run venture-intelligence programs list
uv run venture-intelligence batches list --program yc_current
uv run venture-intelligence sources status
```

`doctor` reports database, registry/current batch, source readiness, indexed
social policy, optional analyst, email and scheduler state without printing
tokens, passwords or webhook URLs. It also reports the default Ollama model as
PRESENT, MISSING (with the `ollama pull` command), UNREACHABLE or NOT_SELECTED,
and lists next steps from the capability registry (`config/capabilities.yaml`).
`vi` is an alias of `venture-intelligence`; `vi capabilities --lens L1` lists
what a lens can do. `vi demo seed --database data/demo.db` writes a fictional,
synthetic dataset to a new file and refuses the operational database. `/#/system/runs` provides the same product-level
readiness and retained cycle history.

## Credential and permission matrix

| Boundary | Default state | Configuration |
|---|---|---|
| Public web/search (DuckDuckGo/Bing/Yahoo/Brave), RSS, public program pages | Credential-free, enabled by default; availability may drift | Sources UI or `SOURCE_*` settings |
| arXiv official API | Credential-free; publication queries only | `RESEARCH_ARXIV_ENABLED=true` |
| Google Scholar public discovery | Disabled, best effort, may block | `RESEARCH_SCHOLAR_ENABLED=true` |
| GitHub public pages/API | Public access; token can improve rate limits | `GITHUB_TOKEN` optional |
| Product Hunt GraphQL API | Implemented, disabled until configured | `PRODUCT_HUNT_ACCESS_TOKEN` and `PRODUCT_HUNT_API_ENABLED=true`; operators remain responsible for Product Hunt's API terms and deployment permissions |
| Discord public widget | Server-owner-enabled public embed only | Program guild/widget IDs; usernames are clues and autonomous scans do not create people |
| Discord bot/channel API | Not implemented | Authenticated message-content collection is outside this public-only boundary |
| LinkedIn/X/Instagram/Threads direct access | Permission required and disabled | No login automation or bypass is supported |
| LinkedIn/X/Instagram/Threads indexed discovery | Permitted-provider discovery only | At least one enabled search provider must be healthy |
| Manual public evidence | Available | Use the typed API; include a public URL and provenance |
| Deterministic analyst | Active by default | `ANALYST_PROVIDER=auto`; local Qwen augments when detected |
| Email delivery | Not configured by default; preview works | Recipient in UI/API plus `EMAIL_SMTP_*` environment values |
| Slack | Optional | `SLACK_WEBHOOK_URL`; never exposed to the browser |
| Firecrawl | Optional boundary only, not registered | Not required and not live-validated |

The public intelligence watchlist is data-driven by
`config/intelligence_watchlists.yaml`. Entries may declare aliases (for example,
`a16z` for Andreessen Horowitz); `/api/v1/sources` and the Sources workspace
expose the active entries without turning an investor mention into program proof.

Source health is never inferred from a connector existing. Run the explicitly
gated, bounded, disposable-store validation when network checks are intended:

```powershell
$env:VENTURE_INTELLIGENCE_LIVE_TESTS = "1"
uv run venture-intelligence sources check --program yc_current --allow-network --max-queries 4 --max-requests 12
```

No CAPTCHA bypass, proxy evasion, credential harvesting, fake account, private
network target or direct restricted-social automation is supported.

## Analyst workflow

```powershell
uv run venture-intelligence cycle run --program yc_current
uv run venture-intelligence discovery candidates --program yc_current --batch F26
uv run venture-intelligence candidate show <hypothesis-id>
uv run venture-intelligence candidate evidence <hypothesis-id>
uv run venture-intelligence candidate investigate <hypothesis-id>
uv run venture-intelligence rankings list --program yc_current --batch F26
uv run venture-intelligence changes list --since 24h
uv run venture-intelligence search providers
uv run venture-intelligence feeds status
uv run venture-intelligence watchlist status
uv run venture-intelligence funding recent
uv run venture-intelligence investors recent
uv run venture-intelligence partner mentions
uv run venture-intelligence inbox
uv run venture-intelligence graph show <canonical-entity-id>
uv run venture-intelligence analyst ask "Why do we think Acme joined YC F26?"
uv run venture-intelligence candidate export <hypothesis-id> --format json
uv run venture-intelligence candidate export <hypothesis-id> --format markdown
```

Use `--json` before the command for automation. `cycle run` uses the registry's
active cohort and the same locked production service as the API. Discovery queries never require
a supplied founder identity. Incompatible providers are skipped before a
network request or budget charge. Retrieved but irrelevant results remain audit
records and cannot affect confidence, ranking, recommendations or chat.

The UI analyst inbox at `/#/inbox` also has Funding Announced, VC Backed,
Partner Mention, Newsletter Discovery, TechCrunch Discovery, Social Discovery,
and Low Coverage views. Candidate detail explains discovery reason, recency, change,
support/contradiction counts, next action and immutable provenance.

## Local analyst

Deterministic grounded answers work without a model. When no candidate evidence
is indexed, the answer says so and does not substitute general model knowledge.
The default `auto` mode detects optional local Qwen. To require the Qwen path:

```powershell
ollama pull qwen2.5-coder:3b
$env:ANALYST_ENABLED = "true"
$env:ANALYST_PROVIDER = "qwen"
uv run venture-intelligence doctor
uv run venture-intelligence analyst ask "Which candidates changed most?"
```

Health distinguishes unreachable Ollama, missing model, not configured and
healthy. Model output is non-authoritative and receives bounded canonical JSON
context; answers carry application evidence citations.

## Email and partner digest

```powershell
uv run venture-intelligence email preview --digest
uv run venture-intelligence email send --digest
```

Preview never requires SMTP. If delivery is absent, the exact result is
`DELIVERY NOT CONFIGURED — PREVIEW AVAILABLE`; no delivery row is fabricated.
Configure the recipient and preferences in `/#/system/settings`, and keep SMTP secrets
only in `.env`/environment variables. `send` reports success only after the
provider confirms delivery. Intelligence cycles do not silently contact email
or Slack; delivery remains an explicit operator action.

## Manual and 12-hour cycles

```powershell
uv run venture-intelligence cycle run --program yc_current
uv run venture-intelligence runs list --program yc_current
uv run venture-intelligence runs show <run-id>
uv run venture-intelligence scheduler start --interval 12h
uv run venture-intelligence scheduler status
uv run venture-intelligence scheduler stop
```

The foreground scheduler uses the same orchestration service as API and CLI,
persists last/next-run state, defaults to 12 hours, prevents overlapping cycles,
recovers stale locks and isolates individual source failure. Stop the foreground
process with Ctrl+C. The stop command persists disabled state; start re-enables
it. Run records include budgets, eligible/skipped providers, requests, retries,
latency, observations, new/updated hypotheses, promotions, contradictions,
enrichments, affected canonical entities, changes, errors and stop reason.

For production unattended operation, use the operating-system scheduler to run
`uv run venture-intelligence --json cycle run` from the repository root. The
command returns non-zero for partial/failed/overlapping work and records last
success/failure timestamps. Task Scheduler, systemd, cron, and launchd examples,
reboot persistence, logging, least privilege, and lock recovery are documented
in [`UNATTENDED_OPERATION.md`](UNATTENDED_OPERATION.md).

## Safe database and reconciliation commands

```powershell
uv run venture-intelligence db status
uv run venture-intelligence db fresh --path data/manual-test.db --confirm
uv run venture-intelligence reconcile
uv run venture-intelligence reconcile --apply
```

The operational database additionally requires `--confirm-operational` before
reset. WAL and SHM companions are removed only for the exact confirmed target.
Reconciliation defaults to dry-run and preserves immutable evidence/events.

## Tests and builds

```powershell
uv run pytest -q
uv run ruff check backend tests
uv run python -m compileall -q backend tests
pnpm test
pnpm typecheck
pnpm lint
pnpm build
pnpm api:types
```

Tests are offline by default. Live-source success, live Ollama, SMTP delivery
and direct platform permissions must be reported separately from fixture tests.

## Final CLI classification (2026-09-07)

The CLI remains a thin argparse interface, not a second React application.
All 25 former top-level commands were reviewed; 24 remain. `--json` precedes
the command and preserves the structured service result. Human `doctor` output
shows capability summaries and local analyst health, not the provider-policy dump.

| Commands | Decision | Operator job |
|---|---|---|
| `doctor`, `sources`, `cycle`, `scheduler`, `runs` | KEEP | Readiness, gated source validation, one production cycle, scheduling and retained outcomes |
| `programs`, `batches`, `email`, `db`, `reconcile` | KEEP | Registry inspection, explicit digest preview/send, safe storage setup and dry-run integrity repair |
| `search`, `feeds`, `watchlist` | SYSTEM-ONLY | Provider/feed configuration diagnostics; no duplicate product workflow |
| `funding`, `investors`, `partner`, `discovery`, `candidate` | SYSTEM-ONLY | Canonical observation/hypothesis inspection and explicit export/investigation |
| `rankings`, `changes`, `inbox`, `graph`, `research`, `analyst` | SYSTEM-ONLY | Troubleshooting, bounded research and read/export access to the same application services |
| old top-level `status` | MERGE / REMOVE | Replaced by `doctor`; avoids two readiness definitions |
| old `discovery run` subcommand | MERGE / REMOVE | Replaced by `cycle run`; removes an unlocked competing production path |

Canonical collection: `uv run venture-intelligence --json cycle run --program yc_current`.
The optional program argument can be omitted to use active configured programs.
Do not supply a historical `--batch` to this production command; use replay for
historical reconstruction. No new CLI dependency or framework was added; S0 later
added the `vi` entrypoint alias and the `capabilities` and `demo` commands.

Exit codes: 0 success; 1 partial/degraded/failed execution or inaccessible storage;
2 invalid input or required user configuration; 3 overlapping cycle. Doctor's
optional-unconfigured capabilities do not turn its exit code into a failure.
The API and CLI both use `IntelligenceCycleService`, the application-owned lock,
SQLite and `OperationsStateStore`. Core commands are OS-neutral Python; scheduler
installation/reboot behavior still requires validation on each deployment OS.
