# D-017: Hosted mode is one organisation per deployment, with RLS-isolated workspaces and a shared public corpus

- **Date:** 2026-11-23
- **Area:** Tenancy
- **Differs from owner instruction:** Partly (no multi-organisation SaaS; the public corpus is shared inside a deployment)

1. **Chosen:** each hosted deployment serves one organisation. Every user has a personal
   workspace; team workspaces are opt-in.
   - **Private data** carries `workspace_id NOT NULL` and is isolated by Postgres
     row-level security (`FORCE`, `SET LOCAL vi.workspace_id`, a non-owner
     `NOBYPASSRLS` role). It covers theses, mandates, notes, CRM pipeline, lens
     preferences, the delivery outbox, model keys, targeted research and its
     observations, and thesis-dependent rankings.
   - **The shared corpus** is public-source data from scheduled monitoring. It has
     `workspace_id NULL` and every member can read it.

   Local mode runs the same policies with one workspace. See ADR-0006.
2. **Owner instruction:** in hosted mode, per-user isolation of lens, thesis, mandates,
   notes and pipeline, for example with Postgres row-level security.
3. **Why:** collecting public data once per deployment avoids duplicate crawling and
   load on sources. Keeping each workspace's own research private means one user's
   targeted lookups never reveal their deal interest to another. LANDSCAPE §4 also
   recommends single-tenant hosting first. Personal workspaces deliver the per-user
   isolation the owner asked for.
   **Trade-off:** multi-organisation SaaS would need schema-per-tenant or an
   organisation key on the corpus as well, which is a later decision. Team workspaces
   share data deliberately, so membership changes need care.
4. **Parity:** every existing capability runs in the implicit local workspace, so the
   current suite passes with RLS on. A registry-generated matrix test proves that user A
   cannot read or change B's private rows through REST, the remote CLI or MCP.
