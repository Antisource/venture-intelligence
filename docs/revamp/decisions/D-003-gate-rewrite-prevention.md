# D-003: Prevent gate-driven history rewrites of stacked branches

- **Date:** 2026-11-20
- **Area:** Gate history-rewrite prevention
- **Differs from owner instruction:** No (implements §3.4a)

- **Owner instruction:** brief §3.4a: the gate must not rewrite a pushed
  branch that later stacked branches depend on; rehearse first and stop if
  it cannot be prevented.

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
- **Parity:** process only; no product capability affected.
