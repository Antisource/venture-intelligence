# D-001: Claude Code subagents with treehouse worktrees instead of firstmate

- **Date:** 2026-11-20
- **Area:** Crew runtime
- **Differs from owner instruction:** Yes (owner approved)

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
