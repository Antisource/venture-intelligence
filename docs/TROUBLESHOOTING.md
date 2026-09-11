# Troubleshooting

## Replay returns `partial`

Inspect the structured `errors` and `counts` in the CLI JSON. `partial` means a
candidate/event was rejected or the `max_events` bound was reached; valid
projections already rebuilt are preserved. Increase the bound only for a local
administrative run after reviewing resource limits. Replay never writes to the
live database. A naive timestamp is rejected—use an ISO-8601 value with a UTC
offset such as `2026-01-15T12:00:00+00:00`.

## Install and imports

From the repository root run `uv sync --all-groups`. If uv reports a host cache
path collision, set a project-local cache for that shell, for example
`$env:UV_CACHE_DIR = '.uv-cache-local'`, then rerun the command. This is an
environment issue, not a package import requirement. Verify imports with:

```powershell
uv run python -c "import venture_intelligence; import venture_intelligence.storage.db"
```

## Startup

Use `uv run python -m venture_intelligence.monitor` for the full local system,
or `uv run uvicorn venture_intelligence.api.app:app --reload --host 127.0.0.1
--port 8000` for API/frontend-only work. Run from the repository root so the
registry, `.env`, frontend assets, and relative database path resolve.

Run `pnpm build`, then check `/api/health`, `/api/v1/programs`, `/`, and the
hashed `/frontend/dist/assets/*.js` path referenced by `/`. FastAPI serves the
production bundle; `pnpm dev` is the optional hot-reload server.

## Source failures

HTTP 202 is `TEMPORARILY_UNAVAILABLE` and is never evidence. HTTP 200 login
content and login redirects are `AUTH_REQUIRED`; 403/CAPTCHA is `BLOCKED`;
429/rate responses are `RATE_LIMITED`; 404/410 and timeouts are `UNAVAILABLE`.
Transport success is tracked separately from usable source content. Repeated
login/blocked outcomes participate in the host circuit breaker. Do not add
credentials or retry loops to bypass a restriction.

Direct LinkedIn/X/Instagram/Threads monitor automation is disabled while
`RESTRICTED_SOCIAL_ACCESS_MODE=search_discovery` (the default). Indexed public
search discovery is the supported default. Only set `approved_public_web` when
an operator has explicitly approved ordinary direct public-page access.

## Analyst and research

Research runs can end with `runtime_budget_exhausted`,
`request_budget_exhausted`, `max_queries_reached`, `provider_unavailable`, or
`no_results`. This is a valid JSON result, not an application crash. Inspect
`requests_used`, retry/provider/domain counts, and source statuses. The analyst
always returns a grounded response. In default `auto` mode it uses local Qwen
when detected and otherwise reports `local-boundary`; missing Ollama does not
make the analyst route unavailable.

## Database and tests

SQLite is initialized automatically at `DB_PATH` (default
`data/intelligence.db`). Tests use temporary databases. Do not delete or commit
runtime databases, `.env`, browser profiles, caches, or virtual environments.
