# Contributing to Venture Intelligence

Thank you for helping improve Venture Intelligence. This is an evidence-oriented research prototype, so correctness, provenance, and honest uncertainty matter more than collection volume.

## Before opening a change

1. Read the [documentation map](docs/README.md) and
   [architecture](docs/ARCHITECTURE.md).
2. Review the [Research Agent](docs/RESEARCH_AGENT.md) and
   [public-source policy](docs/SOURCES.md) for the boundary you plan to change.
3. Check existing issues or open a short proposal before a broad architectural, schema, source-policy, or UI change.

Keep pull requests focused. Do not combine unrelated formatting, generated artifacts, connector expansion, and product behavior in one change.

## Local setup

```powershell
uv sync --all-groups
pnpm install --frozen-lockfile
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
pnpm build
```

The offline suites require no credentials or network access. Never add real tokens, `.env`, databases, browser profiles, private data, or generated runtime knowledge to a change.

## Engineering expectations

- Preserve immutable raw evidence and append-only histories where defined.
- Keep canonical contracts JSON-native; Markdown wiki content is generated output.
- Do not promote a person or company without attributable public identity evidence.
- Keep identity, program membership, evidence confidence, and attention/ranking semantics separate.
- Use `ProgramRegistry`; do not introduce program-specific logic when configuration can express it.
- Treat search engines and OSINT tools as acquisition paths, not independent evidence origins.
- Do not add authentication bypass, CAPTCHA solving, credential harvesting, proxy evasion, private-data collection, or direct restricted-social automation.
- Keep external requests bounded, policy-checked, observable, failure-tolerant, and disabled in tests.
- Core behavior must not require a hosted LLM. Model output is non-authoritative unless validated against canonical evidence.

## Tests and checks

Run the checks relevant to your change; run the complete offline gate before requesting merge:

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

When a FastAPI model or route changes, regenerate and commit the matching OpenAPI/TypeScript contracts:

```powershell
pnpm api:types
```

Add a focused regression for defects and boundary changes. Tests must use fakes or local fixtures by default and must not depend on live sources, SMTP, browser sessions, Ollama, GPU hardware, or credentials.

## Documentation

Update the README or the closest existing document when commands, configuration, contracts, source policy, or user-visible behavior changes. Avoid creating overlapping documents. Describe implemented behavior and known limits—never convert fixture coverage into a live-source claim.

## Pull request checklist

- [ ] The change is focused and its purpose is clear.
- [ ] Evidence, provenance, temporal, and candidate-integrity invariants are preserved.
- [ ] New external access remains public, bounded, policy-compliant, and optional where applicable.
- [ ] Focused tests and the relevant quality gates pass.
- [ ] Generated API contracts are synchronized when required.
- [ ] Documentation matches the implementation.
- [ ] No credentials, private data, runtime databases, caches, or generated knowledge files are included.

## Security, license, and conduct

Report vulnerabilities through the private process in [SECURITY.md](SECURITY.md).
Contributions are accepted under the repository's [Apache License 2.0](LICENSE).
The repository does not currently include a formal code of conduct; keep
collaboration respectful, technical, and privacy-conscious.
