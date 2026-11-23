# D-009: Every PR targets revamp-main; the S0 catch-up PR comes from the models branch

- **Date:** 2026-11-23
- **Area:** PR base and head selection
- **Differs from owner instruction:** Partly (head branch)

1. **Chosen:** every pull request now targets `revamp-main` and states which PR
   it depends on, as the brief now requires. The catch-up PR that carries links
   2-4 into `revamp-main` uses `refactor/s0-split-shared-models` as its head,
   not `refactor/s0-split-db-module`.
2. **Owner instruction:** open one PR from the tip of
   `refactor/s0-split-db-module` into `revamp-main`.
3. **Why:** PRs #2-#4 were based on earlier stack branches, so the owner's
   merges landed in those branches instead of `revamp-main`. Afterwards the two
   branches had byte-identical trees (`git diff` empty), but the db-module tip
   adds GitHub's merge commit `Merge pull request #4 from ...`. That subject is
   not a Conventional Commit, so the repository's own guard ("Every PR commit is
   a Conventional Commit") would fail the PR, and the commit cannot be fixed
   without rewriting pushed history. The models branch carries the same content
   with a fully conventional history. It was first brought up to date with a
   conventional merge of `revamp-main`, so the gate never has to create its own
   merge commit (D-008).
   **Trade-off:** the remote `refactor/s0-split-db-module` keeps an extra merge
   commit that never reaches `revamp-main`; nothing depends on it.
4. **Parity:** content is identical to what PRs #2-#4 reviewed; verified with
   `git merge-base --is-ancestor` for every link tip and an empty tree diff.

**Owner action that prevents a recurrence:** when merging on GitHub, set the
merge commit title to the PR title (or change the repository default to "Pull
request title"). `revamp-main` already carries `Merge pull request #1 from ...`,
which the CI push check flags as non-conventional.
