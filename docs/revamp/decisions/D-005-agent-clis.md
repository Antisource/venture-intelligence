# D-005: Claude Code CLI as the agent for no-mistakes and gnhf

- **Date:** 2026-11-20
- **Area:** Agent CLIs for gate and loop
- **Differs from owner instruction:** No (owner approved)

1. **Chosen:** install the Claude Code CLI (`@anthropic-ai/claude-code`)
   and set `agent: claude` in both `~/.no-mistakes/config.yaml` and
   `~/.gnhf/config.yml`. gnhf uses `commitMessage.preset: conventional`.
2. **Owner instruction:** same (approved during preflight). The only agent
   CLI found before that was GitHub Copilot's.
3. **Why:** the reviewer and the loop driver use the same model family the
   brief is written for.
4. **Parity:** process only.
