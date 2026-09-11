# OS-agnostic unattended operation

The supported unattended unit is one bounded intelligence cycle:

```text
uv run venture-intelligence --json cycle run
```

That command is the only scheduler action. Windows Task Scheduler, systemd,
cron, and launchd must invoke it from the repository root; scheduler-specific
scripts must not contain collection or ranking logic. Exit status is `0` only
for a completed cycle, `1` for partial/failed work, `2` for invalid invocation,
and `3` when another cycle owns the application lock.

The JSON state at `OPERATIONS_STATE_PATH` records last run, last success, last
failure, failure reason, next intended run, and bounded per-program results.
`OPERATIONS_LOCK_PATH` is authoritative across every scheduler. A lock owned by
a live process is never removed based on age. A dead owner is recovered with an
explicit reason; an invalid lock is recovered only after
`OPERATIONS_LOCK_STALE_SECONDS` (default six hours). Logs must be captured by
the operating-system scheduler. Secrets stay in a protected `.env` or process
environment and must never be placed in task arguments.

## Windows Task Scheduler

Create a task that runs whether or not the user is logged on, starts at boot,
and repeats every 12 hours. Set **Start in** to the repository root and use:

```text
Program: C:\path\to\uv.exe
Arguments: run venture-intelligence --json cycle run
Start in: C:\path\to\venture-intelligence
```

Enable “run as soon as possible after a missed start,” prevent parallel task
instances, and retain task history. The application lock is still the final
overlap guard. Grant the task account read/write access only to the repository's
configured data, knowledge, and log paths. Reboot persistence is supplied by
Task Scheduler; no foreground application scheduler is required.

## Linux systemd timer

Use a oneshot service whose `WorkingDirectory` is the repository and whose
`ExecStart` is the canonical command. Pair it with a timer using
`OnBootSec=5m`, `OnUnitActiveSec=12h`, and `Persistent=true`. Let systemd capture
stdout/stderr in the journal and use the service exit status for alerting.

## Linux cron

When systemd is unavailable, a crontab entry may invoke the same command every
12 hours after changing to the repository root. Redirect stdout/stderr to an
operator-owned log file. Cron provides no durable catch-up after downtime, so
systemd is preferred where available.

## macOS launchd

Use a LaunchAgent or LaunchDaemon with `WorkingDirectory` set to the repository,
`ProgramArguments` containing `uv`, `run`, `venture-intelligence`, `--json`,
`cycle`, `run`, and `StartInterval` set to `43200`. Configure explicit standard
output/error paths. Use a LaunchDaemon only when the task must run before login.

## Verification

Run one manual cycle, inspect its JSON and exit status, then run:

```text
uv run venture-intelligence --json scheduler status
uv run venture-intelligence --json runs list
```

Restart the machine or scheduler service, verify another bounded cycle runs,
and confirm that an intentionally overlapping invocation exits `3` without
collecting or mutating duplicate derived state. The foreground `scheduler
start` command remains useful for local development, but it is not the
production unattended mechanism.

### Current Windows-host validation note

On 2026-09-04 the canonical command completed successfully against disposable,
network-disabled state and persisted restart-safe cycle/scheduler JSON. A
temporary current-user Task Scheduler definition registered successfully with no
secrets, but remained queued for 45 seconds and executed no cycle in the hosted
Codex session; passwordless S4U registration was denied by Windows. Both temporary
task definitions were removed. Therefore command, lock, state, and task-definition
contracts are validated, while actual Task Scheduler execution remains a manual
host acceptance item rather than a claimed live pass.
