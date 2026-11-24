# Venture Intelligence

**A local-first venture research workspace that turns public signals into traceable evidence, identity hypotheses, and analyst decisions.**

Venture Intelligence helps investors and researchers investigate accelerator-associated founders and companies without treating search snippets, social handles, or model output as fact. It preserves the path from source material to identity resolution, knowledge, ranking, and review.

> **Beta status:** the primary local workflow has completed manual acceptance,
> with external-source limitations documented below. This is a research
> prototype—not a production intelligence service, admission predictor, or
> investment-return model.

## Why this exists

Early company information is fragmented across official directories, announcements, public websites, repositories, publications, newsletters, archives, and indexed social references. Collecting it is relatively easy; knowing what is attributable, independent, current, and strong enough to act on is harder.

The product thesis is:

```text
public signals
  -> immutable observations and evidence
  -> candidate hypotheses
  -> identity and program verification
  -> canonical intelligence
  -> analyst decision
```

Weak or ambiguous material remains visible for investigation, but it cannot silently strengthen a canonical founder or company.

## What it does today

- Runs bounded discovery for previously unknown founder/company hypotheses without seeding from an official accelerator directory.
- Researches a named founder or company across permitted public sources.
- Separates discovery, identity corroboration, program verification, and enrichment.
- Preserves URLs, retrieval times, acquisition paths, evidence origins, confidence reasoning, contradictions, and temporal status.
- Rejects generic labels and unsupported identities before they reach ranking, CRM, knowledge, or graph projections.
- Compiles canonical JSON knowledge into revisioned, human-readable wiki views.
- Provides an analyst Inbox, Discover workspace, entity Research view, Portfolio/decision workflow, historical replay/evaluation, and an interactive temporal 3D relationship graph with an accessible table fallback.
- Supports append-only CRM actions, recommendations, alerts, and optional SMTP delivery.
- Works without a hosted LLM; deterministic behavior is authoritative and local Ollama/Qwen is optional.

It does **not** claim to:

- predict YC or accelerator acceptance;
- reliably discover founders before public announcement;
- prove identity or program membership from a keyword, username, or single weak source;
- provide direct live LinkedIn, X, or Instagram intelligence;
- bypass authentication, CAPTCHAs, robots rules, rate limits, or private access controls;
- provide production-grade authentication or tenant isolation;
- demonstrate investment-return predictive power;
- be production-ready.

Official accelerator directories are verification and ground-truth sources for this workflow, not its intended primary discovery mechanism.

## Analyst workflow

```text
Inbox -> Discover -> Research -> Portfolio
```

1. **Inbox** surfaces material changes and records that need attention.
2. **Discover** shows pre-canonical hypotheses, why they appeared, coverage, contradictions, and promotion blockers.
3. **Research** brings together categorized evidence, provenance, timeline, knowledge revisions, relationships, decisions, and the entity-scoped analyst.
4. **Portfolio** supports qualification and follow-up through append-only CRM decisions.

The graph is part of Research rather than a separate source of truth. Selecting a node or relationship exposes the same server-provided evidence and provenance used elsewhere.

## Reading the intelligence correctly

Four concepts are deliberately separate:

| Concept | Meaning | What it does not mean |
|---|---|---|
| Identity status | Whether evidence is attributable to this exact person or company | Program membership |
| Program status | Whether the selected accelerator/batch claim is supported | Investment quality |
| Evidence confidence | Deterministic strength of one claim/category, including source quality, recency, independence, and contradictions | Certainty from keyword frequency |
| Attention score | A prioritization signal for analyst review | Factual confidence or predicted return |

Identity may be `verified`, `corroborated`, `provisional`, `ambiguous`, or `unresolved`. Program status may be `verified`, `supported`, `hypothesis`, `unknown`, or `contradicted`. Evidence is marked `attached`, `unbound`, or `contradictory`.

Unbound same-name evidence is retained in the investigation record. It cannot create or strengthen an entity, feature, ranking, CRM record, canonical wiki claim, or graph relationship. Confidence is capped and does not increase when multiple acquisition tools point to the same underlying source.

## Architecture

The backend and frontend are physically separate and communicate through typed JSON contracts:

```text
Public search / feeds / websites / APIs / archives
                    |
                    v
       bounded acquisition and source policy
                    |
                    v
 immutable observation -> candidate hypothesis -> integrity gate
                                               | accepted only
                                               v
                                   CanonicalEvent + SQLite queues
                                               |
                    +--------------------------+-------------------+
                    |                          |                   |
              entity resolution          knowledge/wiki     temporal graph
                    |                          |                   |
                    +----> features -> signals -> rankings ------+
                                      |            |
                                      v            v
                                     CRM     recommendations/alerts
                                                    |
                                                    v
                                   FastAPI -> React/TypeScript/Vite
```

Important invariants:

- JSON is canonical machine-readable truth; Markdown wiki pages are compiled views.
- Raw evidence and operational actions are append-only or immutable where required.
- Default historical semantics use `observed_at <= cutoff`; replay runs in an isolated store and suppresses operational side effects.
- `ProgramRegistry`, backed by `config/programs.yaml`, is the program abstraction. YC is configured data, not a system-wide special case.
- Frontend types are generated from the current FastAPI OpenAPI schema.

See [Architecture](docs/ARCHITECTURE.md), [JSON contracts](docs/JSON_CONTRACTS.md), and [Data model](docs/DATA_MODEL.md).

## Source and integration model

Availability is runtime-dependent. A connector existing in code is not proof that a source is currently reachable.

| Boundary | Default/role | Notes |
|---|---|---|
| Public search, RSS, public program/VC/company pages | Credential-free discovery and verification | Bounded requests, retries, caching, circuit protection, and structured failures |
| arXiv | Enabled publication enrichment | Uses the official `arxiv` Python package/API and preserves canonical metadata |
| GitHub | Public access; token optional | A fine-grained read-only token can improve quota; repository matches do not prove founder identity |
| Product Hunt | Optional official API | Requires explicit enablement and token; operators are responsible for upstream terms and permitted use |
| Google Scholar | Disabled by default | Best-effort public discovery only; blocks/CAPTCHAs become structured failures |
| Indexed LinkedIn/X/Instagram/Threads references | Discovery clues through permitted search providers | The destination platform is not crawled directly |
| Direct LinkedIn/X/Instagram/Threads automation | Disabled | No login automation or access-control circumvention |
| Discord public widget | Optional public signal | Authenticated/private collection is outside the default boundary |
| Wayback | Historical evidence | Historical observations are never represented as current facts |
| Maigret/Sherlock | Optional bounded identity clues | Username equality is not identity proof and cannot promote an entity |
| SMTP/Slack | Optional delivery | No alert is marked sent without provider acknowledgement |
| Ollama/Qwen | Optional local analysis/extraction | Model output is non-authoritative unless validated against canonical evidence |
| Firecrawl | Disabled optional boundary | Not registered in the default connector factory and not required |

For the authoritative policy and dated validation observations, see [Public sources](docs/SOURCES.md), [Source acceptance matrix](docs/SOURCE_ACCEPTANCE_MATRIX.md), and [Operational validation](docs/OPERATIONAL_VALIDATION.md).

### Source status semantics

`configured` means the application has settings for a source; `enabled` means
policy permits it for the current operation; `available` means a bounded check
could reach it; `validated` means that check produced the expected normalized
contract; and `degraded` means a partial, blocked, drifting, or unavailable
outcome was observed. These states are not interchangeable, and a connector's
presence in source code is never reported as live success.

## Quick start

### Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)
- Node.js 20.19+ or 22.12+ (the versions accepted by the pinned Vite release)
- pnpm 11 (the repository declares its package-manager version)

No external API credential, browser session, hosted LLM, Ollama daemon, or GPU is required for core startup or offline tests.

### Install and run

From a clean clone, in the repository root:

> This release is supported from a source checkout. PyPI and standalone wheel
> distribution are not currently supported.

```powershell
uv sync --all-groups
pnpm install --frozen-lockfile
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
pnpm build
uv run python -m venture_intelligence.monitor
```

For macOS/Linux, replace the copy command with:

```bash
test -f .env || cp .env.example .env
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The monitor process starts the API and worker loops and serves the current Vite production bundle at `/`. Runtime directories and the local SQLite database are created automatically.

### Development mode

Run the API/frontend server:

```powershell
uv run uvicorn venture_intelligence.api.app:app --reload --host 127.0.0.1 --port 8000
```

For frontend hot reload, run this in a second terminal:

```powershell
pnpm dev
```

Vite serves [http://127.0.0.1:5173](http://127.0.0.1:5173) and proxies API requests to port 8000. Run commands from the repository root. See [Product operations](docs/PRODUCT_OPERATIONS.md) and [Troubleshooting](docs/TROUBLESHOOTING.md).

### Containers (PostgreSQL preview)

With Docker Desktop, Podman or Rancher Desktop running:

```powershell
docker compose --profile local up --build
```

This starts PostgreSQL 18 (named volume, not published to the host) and the
API/UI on [http://127.0.0.1:8000](http://127.0.0.1:8000). `GET /livez` reports
the process is up; `GET /readyz` checks the stores and answers 503 with a
reason when one is not ready. The app still stores intelligence in SQLite
while domains are ported to PostgreSQL (ADR-0001), so `/readyz` reports the
schema as behind until you migrate it:

```powershell
docker compose --profile local run --rm api python -m venture_intelligence.storage.pg upgrade
```

The `demo` profile (`docker compose --profile demo up --build`) runs that
migration automatically as a one-shot service. Stop with
`docker compose --profile local down` (add `-v` to delete the volumes).

To copy an existing SQLite database into an upgraded PostgreSQL database
(`DATABASE_URL` set; the SQLite file is only read):

```powershell
uv run python -m venture_intelligence.storage.pg import-sqlite --sqlite data/intelligence.db
```

## Configuration

Copy `.env.example` to `.env` and change only what you need. The defaults use local SQLite (`data/intelligence.db`), the YAML program registry, bounded public collection, indexed-only social discovery, disabled rendered crawling, and no external notification provider.

Common optional settings:

| Need | Variables |
|---|---|
| Higher GitHub quota | `GITHUB_TOKEN` (fine-grained, public repositories, read-only metadata/contents) |
| Product Hunt official API | `PRODUCT_HUNT_API_ENABLED=true`, `PRODUCT_HUNT_ACCESS_TOKEN` |
| SMTP alerts | `EMAIL_SMTP_HOST`, `EMAIL_SMTP_PORT`, `EMAIL_SMTP_USERNAME`, `EMAIL_SMTP_PASSWORD`, `EMAIL_SMTP_FROM`; configure recipient/preferences in the UI |
| Local Ollama/Qwen | `OLLAMA_ENABLED=true` and/or `ANALYST_PROVIDER=qwen`; default model is `qwen2.5-coder:3b` |
| Opt-in public Scholar discovery | `RESEARCH_SCHOLAR_ENABLED=true` |
| API bind address | `API_HOST`, `API_PORT` |

Never commit `.env`, tokens, SMTP credentials, database files, browser profiles, or runtime knowledge pages. Product Hunt usage restrictions and every source's upstream terms remain the operator's responsibility.

### Optional local Qwen

Core research, scoring, replay, evaluation, CRM, and graph behavior is deterministic and works without an LLM. To enable local Qwen:

```powershell
ollama pull qwen2.5-coder:3b
$env:ANALYST_PROVIDER = "qwen"
uv run venture-intelligence doctor
```

The provider-neutral analyst receives bounded canonical JSON context. If Ollama is missing or unhealthy, the application falls back to the deterministic grounded boundary rather than inventing facts.

### Optional email

Email starts disabled with a blank recipient. Configure SMTP secrets through environment variables and recipient/category/threshold preferences in System settings. Preview does not send:

```powershell
uv run venture-intelligence email preview --digest
```

Sending is an explicit operator action. Failures are structured and do not interrupt research.

## Operator commands

The CLI is a thin interface over the same services and state used by the API:

```powershell
uv run venture-intelligence doctor
uv run venture-intelligence sources status
uv run venture-intelligence cycle run --program yc_current
uv run venture-intelligence runs list --program yc_current
uv run venture-intelligence scheduler start --interval 12h
```

Use `uv run venture-intelligence --help` for the complete command surface and place `--json` before a command for structured output. Live network validation is separately gated and is not part of normal startup or tests. See [Product operations](docs/PRODUCT_OPERATIONS.md) and [Unattended operation](docs/UNATTENDED_OPERATION.md).

## Testing

Offline quality gates:

```powershell
uv run pytest -q
uv run ruff check backend tests scripts
uv run python -m compileall -q backend tests
pnpm test
pnpm typecheck
pnpm lint
pnpm build
uv lock --check
```

Tests use temporary SQLite databases and fake providers. They do not require credentials or contact third parties. Release claims must come from a fresh run of this gate; permanent documentation intentionally avoids test counts that become stale. Live-source and SMTP observations are recorded separately and are not represented as fixture-backed guarantees.

See [Testing](docs/TESTING.md) for scope and limitations.

## Security and source policy

- Public, read-only, bounded collection only.
- Redirect targets are revalidated against public/private and restricted-source policy before contact.
- Request budgets cover providers, retries, redirects, and rendered navigation boundaries.
- Private/local targets, login walls, CAPTCHAs, and restricted direct-social destinations fail closed.
- Credentials remain server-side and are redacted from diagnostics.
- Candidate/entity integrity is enforced before EventBus publication and again before derived projections.
- The default local deployment has no production authentication or tenant isolation; bind to loopback and do not expose it to an untrusted network.

For vulnerability reporting and the supported deployment boundary, see [SECURITY.md](SECURITY.md). Never include live credentials or private data in a report.

## Repository structure

```text
backend/venture_intelligence/  Python application, APIs, domain services, pipelines
frontend/                      React/TypeScript/Vite analyst workspace
config/                        Program registry and public watchlists
tests/                         Offline Python regression and acceptance tests
docs/                          Architecture, policy, operations, and feature references
scripts/                       Contract generation and bounded operator helpers
knowledge/                     Generated JSON/Markdown knowledge views (runtime)
ollama_modelfile/              Optional local extractor model definition
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) and [JSON contracts](docs/JSON_CONTRACTS.md)
- [Research Agent](docs/RESEARCH_AGENT.md), [latest-batch discovery](docs/LATEST_BATCH_DISCOVERY.md), and [source policy](docs/SOURCES.md)
- [Knowledge layer](docs/KNOWLEDGE_LAYER.md), [feature lineage](docs/FEATURE_LINEAGE.md), and [temporal graph](docs/GRAPH.md)
- [CRM and decisions](docs/CRM.md), [notifications](docs/NOTIFICATIONS.md), and [analyst boundary](docs/ANALYST.md)
- [Historical replay](docs/REPLAY.md) and [evaluation](docs/EVALUATION.md)
- [Documentation map](docs/README.md), [product operations](docs/PRODUCT_OPERATIONS.md), [testing](docs/TESTING.md), and [troubleshooting](docs/TROUBLESHOOTING.md)

## Beta limitations

- Public search and source availability drift; blocked, unavailable, empty, and partial outcomes are expected and reported explicitly.
- Indexed social discovery depends on permitted search providers and does not provide direct platform access.
- Unknown-founder discovery has fixture-backed end-to-end coverage, but live discovery yield is not yet sufficient to claim reliable early identification or recall.
- Optional integrations require operator credentials/configuration and remain nonessential to core startup.
- Historical evaluation measures supplied ground truth; it does not establish real-world investment performance.
- The lazy Three.js bundle is intentionally isolated but remains comparatively large for low-bandwidth clients.
- Production authentication, authorization, tenancy, deployment hardening, and cross-platform scheduler acceptance remain outside this beta.

## Screenshots

No screenshot is shipped in this release because the accepted populated state
contains operational research data. A public product tour should use a separate,
clearly synthetic demo database and must not expose user notes, email settings,
or source credentials.

## Contributing

Start with [CONTRIBUTING.md](CONTRIBUTING.md). Keep changes narrow, preserve JSON/provenance and candidate-integrity contracts, add offline regressions, and regenerate typed API contracts when backend schemas change.

## License

Licensed under the [Apache License 2.0](LICENSE).

## Disclaimer

Venture Intelligence is research and decision-support software. Its evidence,
scores, hypotheses, recommendations, and model-assisted summaries are not
investment advice and should be independently verified.
