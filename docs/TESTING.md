# Testing and development checks

The default quality gate is offline. It uses temporary SQLite databases and
fake transports/providers; it does not require credentials, a browser profile,
SMTP, a running Ollama server, GPU hardware, or third-party network access.
Development and verification are supported from a source checkout; PyPI and
standalone wheel distribution are not supported in this release.

## Complete quality gate

Run from the repository root after installing Python and Node dependencies:

```powershell
uv run pytest -q
uv run ruff check backend tests scripts
uv run python -m compileall -q backend tests scripts
pnpm test
pnpm typecheck
pnpm lint
pnpm build
uv lock --check
pnpm install --frozen-lockfile --offline
```

`pnpm typecheck` and `pnpm build` regenerate the authoritative OpenAPI document
and TypeScript declarations. A contract-only check is:

```powershell
pnpm api:types
```

Generated files are `frontend/src/api/openapi.json` and
`frontend/src/api/schema.d.ts`. Re-running generation without backend changes
must produce identical bytes.

## What the suites protect

The Python suite covers:

- candidate validation before EventBus publication and downstream projection;
- source policy, redirect/private-target checks, budgets, retries, caching,
  partial evidence, circuit breakers, and truthful operation statuses;
- discovery, verification, enrichment, provenance, confidence, and independent
  evidence origins;
- program-generic YC and a16z Speedrun behavior;
- temporal graph validity, isolated replay, evaluation, and as-of semantics;
- versioned feature/signal/ranking lineage;
- append-only CRM decisions, recommendations, alerts, and email acknowledgement;
- generated knowledge, contradiction/staleness detection, and SSE/polling;
- API contracts, production asset serving, CLI exit codes, and clean shutdown.

The frontend suite covers the native React shell and routes, typed API boundary,
loading/empty/error states, theme, command search, discovery and research,
decision workflows, knowledge revisions, replay/evaluation diagnostics, and the
3D graph's accessible relationship-table fallback.

Live source checks are deliberately separate. Fixture success does not establish
that an upstream source is currently reachable. Use the explicitly gated,
bounded command in [`OPERATIONAL_VALIDATION.md`](OPERATIONAL_VALIDATION.md) and
record the dated result in [`SOURCE_ACCEPTANCE_MATRIX.md`](SOURCE_ACCEPTANCE_MATRIX.md).

## Local runtime smoke

Build the production bundle, start the normal process, and use a disposable or
empty database when testing startup:

```powershell
pnpm build
uv run python -m venture_intelligence.monitor
```

Verify `/`, `/api/health`, the primary React routes, and graceful shutdown. Do
not use the operational database for destructive test setup.

## Temporary files

Pytest writes to the ignored `.pytest-tmp/` directory. Python, Vite, coverage,
browser, and validation caches are ignored and may be removed at any time.
Operational databases, generated knowledge, and user decisions are local state:
they are ignored from Git but must not be deleted as routine cleanup.

## Release evidence

Exact counts and dated live observations belong in a release report, not in
permanent setup instructions. The current repository should be judged by a
fresh run of the commands above; prior milestone counts are not a guarantee.
