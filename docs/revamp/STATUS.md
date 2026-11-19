# Revamp status

_Last updated: 2026-11-20_

## Current stage

**S0 Foundation.** Git guards, baseline docs and the three hotspot splits
(storage, shared models, API routers) are done locally and verified
behaviour-identical. Link 1 is going through the no-mistakes gate.

## Branch stack

Merge in this order, each with **"Create a merge commit"** (not squash or
rebase). Squashing rewrites history and brings conflicts back into the
stacked branches. Set the merge commit title to the PR title so it stays a
Conventional Commit.

| # | Branch | Base | PR | State |
|---|---|---|---|---|
| 0 | `revamp-main` | = `main` @ `88609e6` | n/a | created on origin |
| 1 | `ci/s0-git-guards` | `revamp-main` | not opened | in the gate (review fixes) |
| 2 | `docs/s0-revamp-baseline` | `ci/s0-git-guards` | not opened | ready, waits for link 1 |
| 3 | `refactor/s0-split-db-module` | `docs/s0-revamp-baseline` | not opened | ready, verified |
| 4 | `refactor/s0-split-shared-models` | link 3 | not opened | ready, verified |
| 5 | `refactor/s0-split-api-routers` | link 4 | not opened | ready, verified |

Links 3-5 are cut in parallel from the tip of link 2 and touch disjoint
files. Each is merged forward into the next (`git merge --no-ff`) as it
finishes, so the stack stays linear.

## Gate rehearsal (2026-11-20)

Private throwaway repo `Antisource/nm-stack-rehearsal` (safe to delete). Results:

| Check | Result |
|---|---|
| CI failure repaired | New `fix(gate): ...` commit on top; no rewrite |
| Stacked PR base | PR #2 targets `feat/s1-alpha` via `--base-branch` |
| Conflict with a moved base | Integrated with a merge commit, not a rebase; all pushed heads remain ancestors |
| Gate merge-commit subject | **Not conventional** (`Merge remote-tracking branch ...`); mitigated by D-008 |

First run failed because npm's `claude.cmd` wrapper mangles JSON arguments through
cmd.exe; fixed with `agent_path_override` to the native `claude.exe` (global config).

## Combined stack tip verification

On `refactor/s0-split-api-routers` with links 1-4 merged in: `pytest` 375 passed,
`ruff` clean, `openapi.json` unchanged, `tsc` clean.

## Preflight (2026-11-20)

| Item | Result |
|---|---|
| `origin` remote and push access | OK (`Antisource/venture-intelligence`, dry-run push succeeded) |
| `gh` | Installed 2.105.0 (owner approved). **Not signed in yet.** |
| Docker / compose | Docker 29.9.2, Compose v5.6.1. Daemon started. |
| Python 3.12 / uv | uv 0.11.32, CPython 3.12.13 available |
| Node / pnpm | Node 22.20.0, pnpm 11.24.0 |
| tmux | Not installed (not needed after D-001) |
| Playwright browsers | Chromium installed |
| no-mistakes | Installed v1.106.1, daemon running. **`init` pending gh sign-in.** |
| gnhf | Installed 0.1.53, conventional preset set |
| treehouse | Installed v3.1.8 |
| firstmate | Not cloned (D-001) |
| Claude Code CLI | Installed 2.1.328. **Not signed in yet.** |
| Branch protection on `main` / `revamp-main` | **Unknown** until `gh` is signed in |

## Baseline quality gate (on `main` @ `88609e6`)

- `uv run pytest -q`: 347 passed (1 deprecation warning)
- `uv run ruff check .`: clean
- `pnpm lint`, `pnpm typecheck`, `pnpm test` (53 passed), `pnpm build`: pass
  (one chunk-size warning)
- No CI existed. `ci/s0-git-guards` adds it.

## Environment note: AppData redirection

The Claude desktop app is an MSIX package (`Claude_pzs8sxrjxfjjc`). Tools
the agent installs or configures under `%APPDATA%` / `%LOCALAPPDATA%`
(npm globals such as `claude` and `gnhf`, `no-mistakes`, `treehouse`) are
physically stored in
`%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\` and are **not
visible** to terminals outside the app's package context. That is why
`claude` was "not recognized" in the owner's terminal. Configuration under
`%USERPROFILE%` (`~/.claude`, `~/.gnhf`, `~/.no-mistakes`) is shared.
Sign-ins were run from the app's Terminal panel by physical path.

## Waiting on the owner

1. `gh auth login`
2. `claude` (sign in once)
3. Decide on the CEO agent's proposed deferrals in `LANDSCAPE.md` (mobile
   channel, Teams, warm-intro paths, CRM-lite scope). The brief forbids
   dropping features without owner approval, so all of them stay in scope
   until the owner says otherwise.
4. Optional: enable branch protection on `main` and `revamp-main` (require
   PRs; block direct pushes and force-pushes), and set the repo's default
   merge-commit message to "Pull request title" so merge commits stay
   Conventional Commits.
