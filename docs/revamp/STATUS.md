# Revamp status

_Last updated: 2026-11-24_

## Current stage

**S0 platform implementation.** The ADRs are in draft PR #8. Link 8
(`feat/s0-postgres-platform`) adds the PostgreSQL platform skeleton: Alembic
baseline with tier-1 append-only triggers, `import-sqlite`, an ephemeral
Postgres test harness and CI job, `/livez` and `/readyz`, and the `local` and
`demo` compose profiles. The app still runs on SQLite; domains port in later
links. Earlier: the git guards are in `revamp-main` (PR #1); PRs #2-#4 were
merged into stack branches instead of `revamp-main`; one PR with head `refactor/s0-split-shared-models` (#5) carries
links 2-4 into `revamp-main`.

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
| 4 | `refactor/s0-split-shared-models` | stack branch | #4 | merged into a stack branch |
| 2-4 | `refactor/s0-split-shared-models` | `revamp-main` | #5 | carries links 2-4; ready for review, merge first |
| 5 | `refactor/s0-split-api-routers` | `revamp-main` | #6 | pushed, CI green; depends on #5; draft until the S0 critic loop passes |
| 6 | `test/s0-isolate-test-environment` | `revamp-main` | #7 | depends on #6; test-suite isolation from `.env`, `data/` and SMTP |
| 7 | `docs/s0-architecture-adrs` | `revamp-main` | #8 | draft; depends on #7; ADR-0001 to ADR-0008 and D-010 to D-021, all Proposed; no code changes |
| 8 | `feat/s0-capability-registry` | `revamp-main` | #9 | depends on #8; capability registry and lens view, `vi capabilities`, `vi doctor` model report, `vi demo seed` |
| 9 | `feat/s0-postgres-platform` | `revamp-main` | this link's PR | depends on #9; S0 Postgres platform skeleton (engine, baseline migration, importer, probes, compose) |

## Next actions

1. Owner: merge #5, then #6, then #7, then the ADR PR, each with "Create a
   merge commit" titled as the PR. Review the Proposed ADRs (`docs/adr/`).
2. Agent: finish the rest of S0 (Postgres migration path, test and critic
   harnesses), then run the S0 critic loop before marking S0 PRs ready. Done:
   capability registry and lens skeleton, `vi doctor`, demo database (link 8).
3. After each owner merge: merge `origin/revamp-main` forward through every
   unmerged link.

Local-only work: none. Every finished link is pushed.

### Deferred from the S0 registry

Recorded in the D-019 addendum (`docs/revamp/decisions/D-019-declared-registry-argparse-cli.md`):

- Registry fields `mcp_tool`, `egress`, `requires_role` and `rate_class`.
- TOON output and generating the `vi` command tree from the registry (S5).
- The model report in `/readyz` (with the Postgres platform readiness work).
- `vi demo seed` working outside a source checkout.

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
