# D-004: Guards are one stdlib checker shared by hooks and CI

- **Date:** 2026-11-20
- **Area:** Guard implementation
- **Differs from owner instruction:** Partly

1. **Chosen:** `scripts/git/guards.py` (standard library only) backs the
   `commit-msg` and `pre-push` hooks in `.githooks/` (enabled by
   `scripts/git/install_hooks.sh`) and the `guards` job in
   `.github/workflows/ci.yml`. Tests: `tests/test_git_guards.py`.
2. **Owner instruction:** a pre-push/CI guard refusing pushes or PRs to
   `main`, and direct pushes to `revamp-main`, plus a commit-msg hook and a
   CI check for Conventional Commits.
3. **Why one checker:** the hook and CI rules cannot drift apart, and the
   hooks run without the project virtualenv. The pre-push hook also refuses
   plain `git push origin` of branches (they must go through
   `no-mistakes`) and non-conforming branch names.
   **Limit, stated plainly:** client hooks can be bypassed, and the CI check
   on pushes to `main`/`revamp-main` only *detects* a direct push after the
   fact. *Preventing* one needs server-side branch protection, which is a
   repository setting the owner controls (see STATUS.md).
4. **Parity:** process only.
