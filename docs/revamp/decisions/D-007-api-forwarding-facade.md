# D-007: Transitional forwarding facade for moved API handlers

- **Date:** 2026-11-20
- **Area:** Transitional API forwarding facade
- **Differs from owner instruction:** No (implements S0 split)

1. **Chosen:** routes moved from `api/app.py` and `api/routers/v1.py` into 17 domain routers.
   `app.py` is now a 114-line factory. `api/routers/_compat.py` makes the old modules
   resolve, and forward monkeypatches to, names that now live in the domain routers.
2. **Owner instruction:** split routers early with zero behaviour change.
3. **Why:** existing tests monkeypatch names such as `api.app._grounded_analyst_context`;
   without forwarding, the patch would no longer reach the code that runs and the tests
   would pass while testing nothing. Verified: 71 flattened routes identical in order,
   paths, methods, names, status codes, tags and operation IDs; `openapi.json`
   byte-identical; all 226 former names resolve.
   **Trade-off:** module-class swapping is unusual. It is transitional: it is removed once
   tests patch the new module locations (tracked as an S0 follow-up), and no new code may
   rely on it.
4. **Parity:** every legacy `/api/*` and `/api/v1/*` route is unchanged.
