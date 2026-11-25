# D-018: Per-lens rate limits are defaults; the enforced ceiling is per account

- **Date:** 2026-11-23
- **Area:** Rate limits
- **Differs from owner instruction:** Partly (per-layer limits remain, but they are not the security control)

1. **Chosen:** rate limits are Postgres-backed fixed-window counters keyed by (account,
   capability class, window), with separate low budgets for people-level, egress and
   model capabilities. Each lens sets default budgets that discourage accidental heavy
   use. The enforced maximum is per account and per workspace, whatever lens is active.
   See ADR-0006.
2. **Owner instruction:** per-layer rate limits for people data; lenses are views over one
   backend, not a security boundary.
3. **Why:** a user can switch lens at will, so a limit that depends only on the lens
   would let anyone raise their own budget by switching to L4. Tying the ceiling to the
   account keeps the owner's intent, limiting people-data access, without treating lenses
   as security.
   **Trade-off:** two numbers to configure (lens default and account ceiling). Admins
   need a clear settings page.
4. **Parity:** no current capability is rate-limited, so existing tests are unaffected.
   New tests check that switching lens never raises the account ceiling and that every
   people-level capability writes an audit row.
