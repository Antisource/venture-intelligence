# D-008: Gate merge commits and the ship protocol

- **Date:** 2026-11-20
- **Area:** Gate merge commits and the ship protocol
- **Differs from owner instruction:** Partly

1. **Chosen:** before any branch goes through `no-mistakes`, it is merged forward from its
   stack base with a Conventional Commit message (`chore(git): merge <base> into <branch>`)
   and `origin/<base>` is verified to be an ancestor of `HEAD`. The gate's Rebase step then
   has nothing to integrate.
2. **Owner instruction:** every commit, including merges made by tools, is a Conventional
   Commit; the gate must never rewrite pushed history (§3.4a).
3. **Why:** the rehearsal on a private throwaway repository (`nm-stack-rehearsal`) showed:
   a CI repair landed as a new commit on top (`fix(gate): ...`); a stacked PR targeted the
   previous branch (`--base-branch`); and a real conflict with a moved base was integrated
   with a **merge commit, not a rebase**, so every previously pushed head stayed an
   ancestor. No rewrite occurred in any case. But that gate-made merge commit is titled
   `Merge remote-tracking branch 'origin/main' into HEAD`, and no-mistakes has no
   documented setting for its merge message. Merging forward first means the gate never
   needs to make one. If a base moves between the check and the gate's fetch, the CI
   guard flags the commit and it is reported to the owner rather than rewritten.
4. **Parity:** process only.
