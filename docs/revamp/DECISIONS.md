# Revamp decision log

Each entry states what was chosen, what the owner instructed (if different),
why the choice is better and what trade-off it accepts, and how feature
parity is preserved. Architecture-level choices also get an ADR in
`docs/adr/`. Newest entries at the bottom.

| ID | Date | Area | Differs from owner instruction? |
|---|---|---|---|
| D-001 | 2026-11-20 | Crew runtime | Yes (owner approved) |
| D-002 | 2026-11-20 | Gate configuration location | Yes |
| D-003 | 2026-11-20 | Gate history-rewrite prevention | No (implements §3.4a) |
| D-004 | 2026-11-20 | Guard implementation | Partly |
| D-005 | 2026-11-20 | Agent CLIs for gate and loop | No (owner approved) |

## D-001: Claude Code subagents with treehouse worktrees instead of firstmate

1. **Chosen:** parallel crew work runs as Claude Code subagents, each in a
   treehouse-leased worktree on Windows. firstmate is not cloned.
2. **Owner instruction:** use firstmate (on WSL2 under Windows) and fall
   back to native subagents if it does not fit.
3. **Why:** firstmate supports only macOS and Linux, needs tmux, and the
   only WSL distro installed is kali-linux. Moving the run into WSL would
   split the toolchain (Docker Desktop, uv, pnpm and Playwright are set up
   on Windows). The owner chose this option during preflight.
   **Trade-off:** no firstmate dashboard or crewmate ergonomics; the CTO
   agent does the dispatch and integration by hand.
4. **Parity:** affects process only, not product features.

## D-002: no-mistakes settings live in the global config, not `.no-mistakes.yaml`

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

## D-003: Prevent gate-driven history rewrites of stacked branches

Settings in `~/.no-mistakes/config.yaml`:

```yaml
agent: claude
auto_fix: { rebase: 0, ci: 3, ... }   # rebase repair disabled
ci:
  revalidate_repairs: true            # every CI repair re-passes Review
  rebase_behind: false                # never auto-rebase a BEHIND PR
  release_after_checks_pass: true     # do not hold runs for owner merges
rebase:
  strategy: merge                     # publish by fast-forward, not force-push
commit:
  fix_message: "fix(gate): {{.Summary}}"   # Conventional Commits subject
providers:
  github:
    draft_pull_requests: true         # PRs go ready only after the critic loop
```

- **Why:** the no-mistakes README says CI repairs can publish through a
  "guarded force-push", and rebase-strategy publication rewrites the
  reviewed head. A rewritten branch would orphan every branch stacked on
  it. The merge strategy publishes by fast-forward. Disabling rebase repair
  and auto-rebase removes the remaining rewrite paths that the docs
  describe.
- **Open item:** the docs still say merge-conflict repairs on an open PR go
  through the force-push safety guard. This will be rehearsed on a
  throwaway repository before the first real push (§3.4a). If a rewrite of
  a depended-on branch cannot be prevented, the run stops and the owner is
  told.
- **Stacked PR bases:** pushes use `no-mistakes axi run --base-branch
  <previous stack branch>`, the documented per-run override, because
  `pr.base_branch` is a single per-repo value.

## D-004: Guards are one stdlib checker shared by hooks and CI

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

## D-005: Claude Code CLI as the agent for no-mistakes and gnhf

1. **Chosen:** install the Claude Code CLI (`@anthropic-ai/claude-code`)
   and set `agent: claude` in both `~/.no-mistakes/config.yaml` and
   `~/.gnhf/config.yml`. gnhf uses `commitMessage.preset: conventional`.
2. **Owner instruction:** same (approved during preflight). The only agent
   CLI found before that was GitHub Copilot's.
3. **Why:** the reviewer and the loop driver use the same model family the
   brief is written for.
4. **Parity:** process only.
