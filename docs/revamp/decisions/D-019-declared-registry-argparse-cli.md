# D-019: A declared capability registry generates `vi` on argparse; the legacy command name keeps today's output

- **Date:** 2026-11-23
- **Area:** Service layer and CLI
- **Differs from owner instruction:** No (adds the registry mechanics and the CLI framework)

1. **Chosen:** capabilities are declared in code in `services/capabilities.py`. Each
   entry has an id, blurb, default lens, teaser, CLI command, API route, MCP tool,
   people-level flag, egress flag, required role and rate class. The registry generates
   the `vi` command tree (still argparse), its help and the MCP tool list, and serves
   `/api/v1/capabilities`. A consistency test ties it to OpenAPI.
   - `vi` defaults to TOON output, has structured errors and never prompts.
   - The legacy name `venture-intelligence` keeps today's default human output.
   - `--json` works everywhere.
   - `_compat.py` is retired by moving tests to dependency overrides. Facade access is a
     `DeprecationWarning` that the suite treats as an error until the facade is deleted.

   See ADR-0007.
2. **Owner instruction:** an AXI-style `vi` CLI (TOON, structured errors and exit codes,
   no prompts) and a web UI over one service layer; a capability registry that drives UI
   teasers and CLI help.
3. **Why:** a registry derived from routes cannot carry lens, teaser or people-level
   metadata, and CLI-only capabilities have no route. argparse already holds 33 commands
   with parity tests and adds no dependency. Typer encourages prompts. Keeping the legacy
   name's output protects existing scripts.
   **Trade-off:** registry entries and routes are declared separately, so a test must
   keep them in sync. The CLI framework choice may be revisited in S5.
4. **Parity:** golden tests check that every command from P-071 to P-103 gives identical
   `--json` output under both names. Exit codes 0, 1, 2 and 3 (P-073) are unchanged. The
   route table (71 routes) and `openapi.json` stay identical after `_compat.py` is
   deleted.
