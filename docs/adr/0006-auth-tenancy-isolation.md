# ADR-0006: Authentication, tenancy and isolation

- **Status:** Proposed
- **Date:** 2026-11-23
- **Related:** ADR-0001, ADR-0002, ADR-0005, ADR-0007, D-016, D-017, D-018

## Context

- **No route authenticates, and CORS allows every origin** for GET, POST and PUT
  (`api/app.py:79-84`, P-005). Any page the operator visits can redirect alert email,
  exfiltrate candidates over the operator's SMTP, trigger egress, change the scheduler and
  sources, or append CRM actions. Loopback binding stops neither browser-originated
  requests nor DNS rebinding (FINDINGS §10.1).
- **The WebSockets have no Origin check** (`/ws`, `/api/ws`; P-035, P-036).
- **The SMTP password is a plain `str`** (FINDINGS §10.2). Other tokens are already
  `SecretStr` (P-320, P-322, P-325).
- **No users, theses or mandates exist** (FINDINGS §2). CRM actions record a free-form
  `actor`.
- **The owner requires a hosted mode with:**
  - TLS and authentication;
  - per-user isolation of lens, thesis, mandates, notes and pipeline;
  - abuse controls, admin tasks and secrets handling;
  - an audit log of people-level queries;
  - per-layer rate limits;
  - erasure and opt-out under GDPR and India's DPDP Act.

  Lenses are not a security boundary. LANDSCAPE §4 proposes single-tenant hosting first.

## Decision

1. **Two modes, one codebase.** `VI_MODE=local` is the default. `VI_MODE=hosted` makes
   authentication mandatory.
2. **Same-origin hardening in both modes** lands in S0 and closes FINDINGS §10.1:
   - Wildcard CORS is removed. CORS stays off unless `CORS_ALLOWED_ORIGINS` lists explicit
     origins, and `*` is never combined with credentials.
   - A trusted-host allowlist (`localhost`, `127.0.0.1`, the configured domain) defeats
     DNS rebinding.
   - Every unsafe method requires a JSON (or multipart) content type, plus either
     `Sec-Fetch-Site: same-origin|none` or an allowlisted `Origin`. Hosted mode also
     requires a double-submit CSRF token: the `__Host-vi_csrf` cookie echoed in
     `X-CSRF-Token`.
   - WebSocket and SSE handshakes check `Origin`.
   - Local mode has no login on loopback (D-016). The local CLI calls services
     in-process, not over HTTP (ADR-0007).
3. **Hosted identity: built-in accounts.**
   - Passwords are hashed with Argon2id (argon2-cffi, MIT).
   - Sessions are server-side in Postgres. The id is a 256-bit random value, stored as a
     SHA-256 hash, with an idle limit (12 h) and an absolute limit (7 d); both are
     tunable.
   - The session cookie is `__Host-vi_session; Secure; HttpOnly; SameSite=Lax`. It rotates
     on login and on any role change.
   - Optional OIDC login (Authorization Code with PKCE, via Authlib, BSD-3) works with any
     provider: Keycloak (Apache-2.0), Authentik, Dex (Apache-2.0) or a corporate IdP.
     Accounts are linked by `(issuer, subject)`.
   - Login attempts are throttled per account and per IP. TOTP and passkeys come later.
4. **Tokens.** The CLI, MCP and automation use personal access tokens (`vi_pat_…`):
   stored hashed, scoped to capability groups, with mandatory expiry, last-used tracking
   and revocation. Browser sessions never use JWTs, because JWTs are hard to revoke.
   Tokens never go in URLs or in `localStorage`.
5. **Authorization.** Deployment roles are `owner`, `admin` and `member`. Each capability
   in the registry (ADR-0007) declares `requires_role` and `people_level`. The service
   layer enforces both, so REST, the remote CLI and MCP share one check. Lenses are
   presentation only.
6. **Tenancy and row-level security.** Hosted mode serves one organisation per deployment
   (D-017).
   - **Private, workspace-scoped data** (`workspace_id NOT NULL`):
     - theses and their revisions, mandates and schedules;
     - notes, CRM pipeline actions, lens preferences, saved searches;
     - delivery settings and the outbox, and model-provider keys;
     - targeted research runs and their observations;
     - thesis-dependent rankings;
     - the audit entries.
   - **Workspaces:** every user has a personal workspace. Team workspaces are opt-in
     membership.
   - **Shared corpus:** data from scheduled program monitoring (connector events,
     observations, entities, evidence) has `workspace_id NULL` and is readable by every
     member. Observations from a workspace's own research stay private, so one user's
     targeted research never reveals their interest to another.
   - **Policies:** each table has `ENABLE` and `FORCE ROW LEVEL SECURITY`, with
     `USING/WITH CHECK (workspace_id = current_setting('vi.workspace_id')::uuid)`. Corpus
     tables also allow `workspace_id IS NULL` for reads.
   - **Session variables:** services open each transaction with `SET LOCAL vi.workspace_id`
     and `vi.user_id`, which is safe under PgBouncer transaction pooling.
   - **Roles:** `vi_app` is neither the table owner nor `BYPASSRLS`.
   - **Local mode runs the same policies** with one implicit workspace, so every test
     exercises RLS.
7. **Rate and abuse controls.**
   - Fixed-window counters live in Postgres (an `UNLOGGED` table, updated with upserts)
     and are keyed by (account, capability class, window).
   - Budgets are separate, and tighter, for people-level capabilities, egress-triggering
     capabilities (research, cycles, gather) and model calls.
   - Caddy and the app both limit request body size.
   - Per-lens limits follow the owner's instruction, but they are defaults that slow
     accidental heavy use. Because lenses are not a security boundary, the enforced
     ceiling is per account and per workspace (D-018).
8. **People-level audit.** Every `people_level` capability appends one
   `people_query_audit` row: actor, workspace, capability, lens, subject entity ids (or a
   query digest), result count, request id and time. Users see their own entries; admins
   see all. Retention defaults to 13 months and is
   configurable (confirm with counsel). Erasure and opt-out use `vi admin erase-subject`,
   which calls the ADR-0002 function. The GDPR and DPDP mapping covers technical controls
   only and is not legal advice; check DPDP Rules timelines.
9. **Secrets.**
   - Every secret setting is a `SecretStr`, which fixes the SMTP password.
   - Local mode reads `.env`. Hosted mode reads compose secret files (the `*_FILE`
     convention).
   - Workspace secrets (BYOK model keys, SMTP credentials, Slack and Teams webhooks) are
     encrypted with AES-256-GCM (`cryptography`, Apache-2.0 OR BSD-3) under `VI_SECRET_KEY`.
     Key ids are versioned, so the key can rotate.
   - The API returns only `configured` and the last four characters.
10. **Admin tasks are CLI-only by default:** `vi admin` users, tokens, erase-subject,
    backup, restore and rotate-secret-key. They run with the owner database role and are
    not exposed over HTTP.

## Alternatives considered

- **Require an external IdP such as Keycloak:** gives MFA and federation for free, but
  adds a heavy JVM service for a solo self-hoster. Kept only as optional OIDC.
- **Proxy authentication with trusted headers (oauth2-proxy, MIT; Authelia,
  Apache-2.0):** no auth code in the app, but a misconfigured deployment lets clients
  spoof the headers, and the app still needs users for RLS and audit. Rejected.
- **fastapi-users (MIT):** ready-made flows, but in maintenance mode (verify) and
  JWT-first. Rejected; our needs are small.
- **Schema-per-tenant or database-per-tenant:** stronger isolation, but N-fold
  migrations and no shared corpus. It fits only multi-organisation SaaS, which is an
  owner question.
- **Filtering in the application only, without RLS:** simpler, but one missing `WHERE`
  clause leaks data. Rejected; RLS is defence in depth.

## Consequences

- **Data model:** private tables gain `workspace_id`, and migration backfills the local
  workspace.
- **Failure mode:** an RLS bug fails closed (no rows returned), so errors must make that
  diagnosable.
- **Migration steps:** (1) S0: CORS, host, origin and CSRF hardening, with tests;
  (2) users, sessions and tokens; (3) workspaces and RLS; (4) audit and rate limits;
  (5) secret encryption; (6) OIDC.
- **Parity risks:**
  - P-005 changes from all origins to same-origin. This is the intended fix, and it does
    not affect non-browser clients.
  - Form-encoded cross-site POSTs are refused. The frontend sends JSON (verify for every
    mutation).
  - Cross-origin WebSockets (P-035, P-036) are refused.
  - Same-origin legacy mutations keep working: P-011 (analyst chat), P-017 and P-018 (CRM
    advance and note), and P-026–P-028 (email settings, test send and digest).

## How parity is verified

- **Cross-site protection:** for every mutating route, enumerated from OpenAPI, a test
  sends a cross-site request and expects refusal. A rebinding `Host` is refused, and so is
  a cross-origin WebSocket.
- **Same-origin flows keep working:** the vitest suite and the legacy route tests pass
  unchanged.
- **Workspace isolation:** a matrix generated from the registry checks that user A cannot
  read or change B's thesis, mandate, notes, pipeline or outbox through REST, the remote
  CLI or MCP.
- **Audit coverage:** every `people_level` capability writes an audit row.
- **Both topologies:** the local-mode suite passes with RLS on, and CI runs in both
  topologies (ADR-0005).
