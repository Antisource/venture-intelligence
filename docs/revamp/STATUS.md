# Revamp status

_Last updated: 2026-11-23_

## Current stage

**S0 Foundation.** The git guards are in `revamp-main` (PR #1). PRs #2-#4
(baseline docs, `storage/db/` split, `shared/models/` split) were merged into
stack branches instead of `revamp-main`, because each targeted the previous
stack branch. One PR with head `refactor/s0-split-shared-models` carries links
2-4 into `revamp-main`.

## Branches and PRs

Every PR targets `revamp-main`, never a previous stack branch. A PR that
depends on another open PR says so in its description. Merge in order, each
with **"Create a merge commit"** (not squash or rebase). Squashing rewrites
history and brings conflicts back into dependent branches. Set the merge
commit title to the PR title so `revamp-main` stays Conventional Commits (the
PR #1 merge commit, "Merge pull request #1 ...", is not conventional).

| # | Branch | Target | PR | State |
|---|---|---|---|---|
| 0 | `revamp-main` | = `main` @ `88609e6` | n/a | created on origin |
| 1 | `ci/s0-git-guards` | `revamp-main` | #1 | merged into `revamp-main` |
| 2 | `docs/s0-revamp-baseline` | stack branch | #2 | merged into a stack branch; carried to `revamp-main` by the link 2-4 PR |
| 3 | `refactor/s0-split-db-module` | stack branch | #3 | merged into a stack branch; carried to `revamp-main` by the link 2-4 PR |
| 4 | `refactor/s0-split-shared-models` | stack branch | #4 | merged into a stack branch; this branch is the head of the link 2-4 PR into `revamp-main` |
| 5 | `refactor/s0-split-api-routers` | `revamp-main` | not opened | next; own PR, depends on the link 2-4 PR |
| 6 | `test/s0-isolate-test-environment` | `revamp-main` | not opened | next; own PR, states which PR it depends on |

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

1. Done (2026-11-20): `gh auth login`.
2. Done (2026-11-20): `claude` sign-in.
3. Decide on the CEO agent's proposed deferrals in `LANDSCAPE.md` (mobile
   channel, Teams, warm-intro paths, CRM-lite scope). The brief forbids
   dropping features without owner approval, so all of them stay in scope
   until the owner says otherwise.
4. Set the repo's default merge-commit message to "Pull request title" so
   GitHub merge commits stay Conventional Commits.
5. Optional: enable branch protection on `main` and `revamp-main` (require
   PRs; block direct pushes and force-pushes).
