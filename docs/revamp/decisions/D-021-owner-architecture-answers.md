# D-021: Owner answers to the open architecture questions

- **Date:** 2026-11-24
- **Area:** Architecture (owner decisions)
- **Differs from owner instruction:** No (owner decisions)

1. **Chosen:** the owner answered the questions left open by ADR-0001 to ADR-0008:
   - **Install path:** Docker or Podman compose is the only supported install. There is
     no native no-Docker path; users without Docker get documented manual Postgres steps.
     See ADR-0005 and D-011.
   - **Hosting:** one organisation per hosted deployment, with RLS-isolated workspaces.
     Multi-organisation SaaS is out of scope. See ADR-0006 and D-017.
   - **Erasure residue and audit retention:** erasure leaves content-free tombstones (row
     id, source host, timestamps). The people-query audit log is kept for 12 months
     (configurable, default 12) and then removed by the audited retention purge. A
     `PRIVACY.md`, marked as owner-reviewed, documents both. See ADR-0002, ADR-0006 and
     D-012.
   - **Default model licence:** the Hugging Face card for `Qwen/Qwen2.5-Coder-3B` lists
     the `qwen-research` licence, which is not OSI-compatible. The default Ollama model
     therefore switches to `granite3.3:2b` (IBM Granite, Apache-2.0, a similar size;
     verify the Ollama tag). Qwen stays optional through `OLLAMA_MODEL` and the Qwen-based
     custom Modelfile. The Python default, `.env.example`, the README and AGENTS.md change
     together in the ADR-0008 implementation link. See ADR-0008 and D-020.
2. **Owner instruction:** these are the owner's answers, given on 2026-11-24.
3. **Why:** each question needed an owner or legal call that engineering autonomy does
   not cover: supported install paths, the hosting business model, privacy residue and
   retention, and the open-source rule applied to model weights.
   **Trade-off:** users without Docker carry the manual Postgres setup themselves. The
   new default model may extract less well than Qwen; the evaluation harness measures
   that before the switch lands.
4. **Parity:** existing `.env` files that set `OLLAMA_MODEL` keep their model, and
   `qwen` stays an alias for `ollama` (P-330–P-337). The Modelfile stays the optional Qwen
   custom model, so `test_modelfile_uses_required_qwen_parameters` passes unchanged. The
   native commands (P-104, P-105) keep working for development against the compose
   database.
