# D-002: no-mistakes settings live in the global config, not `.no-mistakes.yaml`

- **Date:** 2026-11-20
- **Area:** Gate configuration location
- **Differs from owner instruction:** Yes

1. **Chosen:** gate settings (`rebase.strategy`, `auto_fix`, `ci.*`,
   commit template, draft PRs) are set in `~/.no-mistakes/config.yaml`.
2. **Owner instruction:** configure the gate for this repo (location not
   specified, but a committed repo config is the obvious reading).
3. **Why:** per the no-mistakes repo-config reference, the `ci.*`,
   `rebase.strategy`, `pr.base_branch` and `auto_fix` keys are read **only
   from the default branch** (`main`), and `main` must not be touched. A
   committed `.no-mistakes.yaml` on a feature branch would be silently
   ignored for those keys. The global config accepts the same keys and
   applies because `main` has no repo config to override it.
   **Trade-off:** the settings are machine-local. They are recorded here so
   any other machine can reproduce them, and they can move into the repo
   once `revamp-main` is promoted to `main`.
4. **Parity:** process only.
