# ADR-0007: Module boundaries and the single service layer

- **Status:** Proposed
- **Date:** 2026-11-23
- **Related:** ADR-0001, ADR-0003, ADR-0006, ADR-0008, D-006, D-007, D-019

## Context

- **S0 split the four hotspot files** (FINDINGS §7): `storage/db/` mixins,
  `shared/models/` (D-006), and `api/app.py` into a 114-line factory plus 17 domain
  routers. `api/routers/_compat.py` is a forwarding facade, so tests that monkeypatch
  old module names still reach the moved code (D-007).
- **Business logic still sits in transport and storage code:** health assembly and
  connector orchestration inline in routers; 28 raw `._connect()` calls outside storage;
  two digest builders (`app.py:603-638`, `cli.py:151-163`), two analyst-context builders
  and four `EmailNotificationService` constructions (FINDINGS §10); attribution in
  storage (FINDINGS §4); a 114-line capability catalog hard-coded in the batch-discovery
  router (FINDINGS §7).
- **The CLI** (FINDINGS §8) is argparse with 33 commands (P-071–P-103). `operations.cli`
  repeats `sources check` with different defaults (8/24 versus 4/12), and the API
  advertises `python -m venture-intelligence`, which cannot run.
- **The owner's brief:** an AXI-style `vi` CLI (TOON output, structured errors and exit
  codes, no prompts), a web UI and MCP over one service layer, and a capability registry
  (id, blurb, default layer, teaser, CLI command, API route) that drives UI teasers and
  CLI help. Lenses L1–L4 are views, not security.

## Decision

1. **Layers.** The package keeps one distribution. import-linter (BSD-2) contracts in CI
   enforce the boundaries.
   - **`domain/`** is pure, with no I/O. It holds the Pydantic models (`shared/models/`
     moves here with re-exports, keeping D-006's `__module__`) and the rules for
     evidence, attribution, confidence and scoring.
   - **`services/`** is the single application service layer, one module per bounded
     context (thesis, mandates, discovery, research, evidence, entities, rankings,
     decisions, delivery, digest, sources, operations, analyst, admin). Every public
     function is one capability: typed Pydantic input and output, plus a `ServiceContext`
     (actor, workspace, lens, request id, clock, unit of work, job queue, model
     provider). Services open transactions and enforce authorization, people-level audit
     and rate limits (ADR-0006).
   - **`ports/` and `adapters/`** hold storage (ADR-0001), the job queue (ADR-0003),
     delivery channels, model providers (ADR-0008), connectors, research providers and
     the crawler.
   - **`interfaces/`** holds `api/` (routers that parse, call a service and map errors,
     with no SQL and no rules), `cli/` (`vi`), `mcp/` and `jobs/` (task wrappers). The
     React frontend remains a pure API client (AGENTS.md).
   - **Import rules:** interfaces → services → domain and ports; adapters → ports and
     domain; nothing imports interfaces; `sqlite3`, `psycopg` and `sqlalchemy` appear only
     in `adapters/storage`.
2. **The capability registry** is `services/capabilities.py`, a list declared in code:

   ```python
   Capability(id, title, blurb, default_lens, teaser, cli, api=(method, path), mcp_tool,
              people_level, egress, requires_role, rate_class, handler)
   ```

   The registry is used to:
   - generate the `vi` command tree and its help;
   - check routes: every entry with `api` maps to exactly that OpenAPI operation, and
     every non-legacy route maps to an entry;
   - list MCP tools;
   - serve `GET /api/v1/capabilities` for UI teasers and lens defaults.

   The batch-discovery catalog moves into the registry. Lens definitions live in
   `services/lenses.py`: the default capabilities and presentation hints for each lens.
   The UI and `vi --lens` use them, and authorization never reads them.
3. **CLI.** `vi` stays on argparse, with its commands generated from the registry (D-019).
   - **Output:** `--format toon|json|text`, defaulting to TOON. `--json` still works
     (P-072, P-259). The legacy name `venture-intelligence` keeps today's human output by
     default.
   - **Errors:** printed on stderr as `{error: {code, message, hint, capability}}`.
     Exit codes stay 0 ok, 1 failed or degraded, 2 user action, 3 already running (P-073);
     new codes (for example 4 auth, 5 rate-limited) are documented before use.
   - **No prompts:** destructive commands require explicit flags.
   - **Local and remote:** local mode calls services in-process. `--remote URL` (or
     `VI_REMOTE`) calls the API with a personal access token, using the route the registry
     names, so inputs and outputs are identical either way.
   - **Module CLIs** (P-104–P-109) become shims onto `vi`. They keep their current
     defaults until a recorded decision unifies them, and the impossible
     `python -m venture-intelligence` hint is corrected.
   - **TOON:** a small in-house encoder against the TOON specification, with golden tests.
     A maintained OSI-licensed package may replace it (verify).
4. **MCP** uses the official `mcp` Python SDK (MIT): stdio for local use, and Streamable
   HTTP at `/mcp` behind token auth in hosted mode. Its tools are the registry entries
   that set `mcp_tool`, with the same audit and rate limits as every other interface.
5. **A typed API with a drift check.**
   - Request and response models are the service models.
   - CI runs `scripts/export_openapi.py` (P-110), then
     `git diff --exit-code frontend/src/api/openapi.json frontend/src/api/schema.d.ts`.
   - New capabilities are additive under `/api/v1`. A breaking change needs `/api/v2` and
     a decision record.
   - Legacy `/api/*` routes (P-007–P-034) become adapters onto the same services, each
     with a response-shape test.
   - Schemathesis (MIT) fuzzes the OpenAPI document in a nightly job.
6. **One implementation of each rule.** `services/digest.py` is the only digest composer,
   used by the API, the CLI and jobs. There is one analyst-context builder and one
   delivery service, built once by the application container. Attribution moves to
   `domain/attribution.py`.
7. **`_compat.py` is retired** (completing D-007):
   1. Routers obtain services through `Depends`, and tests use `app.dependency_overrides`
      or a fake `ServiceContext` instead of monkeypatching module globals.
   2. A codemod moves the remaining patches of `api.app.*` and `api.routers.v1.*` onto the
      owning modules.
   3. The facade emits a `DeprecationWarning` on forwarded access, and the suite runs with
      that warning as an error, so stragglers fail instead of silently testing nothing.
   4. `_compat.py`, the module-class swap and `routers/v1.py` are deleted. `api/app.py`
      exports only `create_app` and `app`.
   5. `test_api_compat_facade.py` is replaced by a route-table test (71 routes) and an
      OpenAPI byte-identity test.

## Alternatives considered

- **Routers as the service layer, with the CLI always calling HTTP:** one fewer layer,
  but a local CLI would need a running server, transport concerns would leak into rules,
  and MCP and jobs would duplicate logic again. Rejected.
- **Typer (MIT) or Click (BSD-3):** nicer help and completion, but Typer encourages
  prompts, both add dependencies, and argparse already holds 33 commands with parity
  tests. Deferred to S5 (D-019).
- **A registry derived from FastAPI routes by introspection:** no duplication, but routes
  carry no lens, teaser or people-level metadata, and CLI-only capabilities have no
  route. Rejected for a declared registry plus a consistency test.
- **GraphQL (Strawberry, MIT):** flexible UI queries, but a second schema, and auditing
  people-level reads per capability becomes harder. Rejected.
- **Separate services:** no operational need at this scale. Rejected.

## Consequences

- **The refactor surface is large but mechanical,** and import-linter keeps it from
  regressing.
- **Migration steps:** (1) `ServiceContext` and dependency injection; (2) move logic out
  of routers domain by domain, alongside the ADR-0001 port; (3) the registry,
  `/capabilities` and the consistency test; (4) generate `vi`; (5) MCP; (6) delete
  `_compat.py`.
- **Parity risks:**
  - Rewritten tests might stop exercising real code, the risk D-007 recorded. The
    deprecation-as-error step and spot mutation checks mitigate it.
  - `vi` defaulting to TOON could break scripts, which is why the legacy name keeps
    today's output.

## How parity is verified

- The flattened route table (71 routes) and `openapi.json` are unchanged.
- The registry consistency test passes.
- **CLI golden tests:**
  - every command from P-071 to P-103 runs under `venture-intelligence` with the same
    arguments, and its `--json` output matches golden files;
  - `vi` and `venture-intelligence` return identical JSON for each command.
- The vitest suite passes unchanged.
- The import-linter contracts pass in CI.
