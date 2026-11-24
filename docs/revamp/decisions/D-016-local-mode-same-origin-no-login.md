# D-016: Local mode has no login but is locked to same-origin requests

- **Date:** 2026-11-23
- **Area:** Local-mode security
- **Differs from owner instruction:** No (adds the local-mode policy)

1. **Chosen:** in `VI_MODE=local`, nobody logs in on loopback. Every mode still enforces
   the following:
   - no wildcard CORS;
   - a trusted-host allowlist against DNS rebinding;
   - JSON or multipart bodies only on unsafe methods;
   - `Sec-Fetch-Site` or `Origin` checks on unsafe methods;
   - `Origin` checks on WebSocket and SSE handshakes.

   In local mode the CLI calls the service layer in-process instead of over HTTP. See
   ADR-0006.
2. **Owner instruction:** authentication is required in hosted mode. The owner gave no
   policy for local mode. FINDINGS §10.1, which the owner accepted as an S0 fix, shows
   that loopback binding alone does not stop cross-site requests.
3. **Why:** a login screen on a single-user local install is friction that buys nothing
   against the real threat: a page in the user's own browser forging requests. Origin and
   host checks stop that threat without accounts.
   **Trade-off:** any local process running as the same user can still call the API. That
   matches the trust model of other local developer tools, and an optional local token can
   be added if the owner wants one.
4. **Parity:** P-005 changes from all-origin CORS to same-origin; this is the intended
   fix, and non-browser clients are unaffected. Every same-origin UI flow and legacy route
   keeps working (vitest suite and route tests). Cross-site requests to each mutating
   route are refused, with a test per route generated from OpenAPI.
