# ADR-0003: Job queue, scheduler and delivery outbox

- **Status:** Proposed
- **Date:** 2026-11-23
- **Related:** ADR-0001, ADR-0002, ADR-0004, ADR-0007, D-013

## Context

- **The monitor is one process doing everything.** `monitor.py:120-155` is a single
  `asyncio.gather` of the Uvicorn API, a 60 s pipeline tick, a 300 s `AgentLoop` and one
  task per connector polling every 1,800 s (P-104, P-220–P-222).
- **The scheduler is separate, and the UI cannot start it.** `operations/cycle.py:447-474`
  is its own foreground loop. It uses a PID-file lock with live-PID checks on Windows and
  POSIX, and keeps state in `data/operations/state.json` (P-098, P-099, P-224, P-225,
  P-275). "Enable scheduler" in the UI and API only flips a flag (P-049, P-176). On the
  operator's install it reads `enabled: true, running: false`.
- **Email has four structural failures** (FINDINGS §5): nothing drains `daily` and
  `weekly` deliveries, cycles get a no-op notifier, test sends are deduplicated after
  their first success, and `PARTIAL` deliveries are never retried.
- **`event_queue` is already a database-backed queue** with exactly-once processing per
  worker (`UNIQUE(event_id, worker)`).
- **The product loop needs scheduling:** each user's mandates run at an interval the user
  chooses. Delivery goes to email first, then Slack and Teams, then web push and
  Telegram.
- **Constraints:** open-source components only, no required external service, a light
  local mode, and Postgres already present (ADR-0001).

## Decision

1. **Procrastinate** (MIT; 3.x, verify) runs on the application Postgres, behind our own
   `JobQueue` port: `enqueue(task, payload, idempotency_key, run_at, queue)`, `cancel`
   and `status`. A hand-rolled `SKIP LOCKED` queue could replace it without touching any
   service. An Alembic revision vendors Procrastinate's schema SQL at a pinned version,
   and each upgrade is a new revision.
2. **Workers.**
   - `vi worker` serves the queues `pipeline`, `collect`, `deliver` and `default`.
   - Task functions are thin and synchronous. They run in Procrastinate's sync-task
     thread executor (verify) and call the service layer (ADR-0007).
   - Local mode can run the API and a worker in one process.
     `python -m venture_intelligence.monitor` (P-104) becomes that composition.
3. **Retries.** Each task declares a `RetryStrategy` with a maximum number of attempts,
   exponential wait with jitter, and the transient exception types to retry. Permanent
   errors fail at once and record a code and a phase: validation errors, SMTP 5xx and
   authentication failures.
4. **Idempotency.**
   - Every enqueue carries a key. A `job_requests(idempotency_key PRIMARY KEY, ...)` row is
     inserted in the same transaction, so a duplicate enqueue is a no-op.
   - Procrastinate's `queueing_lock` stops a second queued copy of a job.
   - `lock` serialises jobs that must not overlap: `cycle:global`, `pipeline`,
     `connector:<id>` and `mandate:<id>`.
5. **Scheduling.**
   - **Static work** uses periodic tasks:
     - `pipeline.tick`: every 60 s as a fallback; normally it is enqueued after ingest;
     - `agent.loop`: every 300 s;
     - `connector.poll:<id>`: every `CONNECTOR_POLL_INTERVAL` (P-283);
     - `outbox.drain`: every 30 s;
     - `scheduler.tick`: every 60 s;
     - `system.heartbeat`.
   - **User mandates are data, not code.** They live in
     `mandate_schedules(mandate_id, interval, anchor_time, timezone, next_run_at,
     enabled)`. In one transaction, `scheduler.tick`:
     - claims due rows with `FOR UPDATE SKIP LOCKED`;
     - enqueues `mandate.run` with the key `mandate:<id>:<window_start>`;
     - advances `next_run_at`.
   - `UNIQUE(mandate_id, window_start)` on `mandate_runs` guarantees that each window runs
     at most once, even with two schedulers.
   - Windows missed during downtime coalesce into one catch-up run, which the UI shows.
   - Intervals use stdlib `zoneinfo`. Cron syntax is deferred.
6. **The PID-file scheduler is replaced.** The cycle (P-223) becomes the task `cycle.run`
   with `lock="cycle:global"`. The PID file, `cycle.lock` and `state.json` are retired.
   CLI and API behaviour:
   - `scheduler start --interval Nh` sets the default schedule and runs a foreground
     worker.
   - `--once` enqueues one cycle and waits for it.
   - `scheduler status` and `scheduler stop` read and disable the schedule.
   - `cycle run` still exits 3 while the lock is held (P-073).
   - `PUT /api/v1/scheduler` (P-049) now really starts and stops scheduling.
   - Until mandates exist (S1), one default schedule mirrors `SCHEDULER_INTERVAL_HOURS`
     and `SCHEDULER_ENABLED` (P-318, P-319).
7. **A transactional outbox handles delivery.**
   - Digest and alert composition write `delivery_outbox` rows in the same transaction as
     the domain change.
   - Each row moves through `queued → sending → accepted | failed(code, phase) |
     suppressed(reason)` (FINDINGS §5 consequence).
   - Idempotency keys:
     - digests: `(mandate_id, window_start, channel, recipient)`;
     - alerts: `(alert_id, channel, recipient)`;
     - test sends: a random nonce, so they are never deduplicated (cause 5).
   - `outbox.drain` claims rows with `SKIP LOCKED`, marks them `sending` with an attempt
     id, calls the channel adapter and records the provider result.
   - Email, Slack, Teams, web push and Telegram are adapters behind one `DeliveryChannel`
     port.
   - Daily and weekly digests are ordinary mandate schedules, so queued deliveries always
     drain (cause 3).
   - Delivery is at-least-once (D-013). SMTP has no idempotency, so a crash after the
     server accepts a message but before the state is written can duplicate that message.
     A stable `Message-ID` derived from the key lets mail clients collapse duplicates.
8. **`event_queue` stays domain state.** It holds per-worker pipeline lineage, not jobs.
   It is ported to Postgres with `SKIP LOCKED` claiming.

## Alternatives considered

| Option | Licence | Extra service | Scheduling | Verdict |
|---|---|---|---|---|
| Procrastinate | MIT | none | periodic tasks | chosen |
| Hand-rolled `SKIP LOCKED` queue | ours | none | must build | fallback behind the port |
| Celery + Valkey or RabbitMQ | BSD-3 (Valkey BSD-3, RabbitMQ MPL-2.0) | broker + beat | beat, single instance | too heavy for local |
| Dramatiq | LGPL-3.0 | Redis or RabbitMQ | needs APScheduler | still needs a broker |
| RQ | BSD-2 | Redis or Valkey | rq-scheduler | still needs a broker |
| APScheduler 3.x | MIT | none | yes | not a durable queue |

- **Hand-rolled queue:** about 300 lines with full control. We would have to build and
  test retries, periodic tasks, stalled-job recovery and admin commands ourselves.
- **Celery:** the most widely deployed option, with Flower for monitoring. It adds a
  broker, has the beat single-instance problem and visibility-timeout redelivery quirks.
  Redis licence changes (RSALv2/SSPL, then an AGPL option in Redis 8) would push us to
  pin Valkey.
- **Dramatiq and RQ:** lighter, but still need a broker, plus another library for
  schedules.
- **APScheduler:** a scheduler, not a work queue. The 4.x line with a Postgres data store
  is not yet stable (verify).
- **Temporal (MIT) or Hatchet (MIT):** durable workflows, but each needs its own server.
  Too heavy for a solo install.

## Consequences

- **Postgres also carries the queue.** That is fine at thousands of jobs a day; revisit
  above about 100 jobs/s (verify with the S6 load test).
- **Bus factor.** Procrastinate is less widely deployed than Celery. The port and the
  pinned version mitigate this.
- **Migration steps:** (1) the port and schema revision; (2) cycle, tick, agent loop and
  connector poll as tasks; (3) the monitor composes API and worker; (4)
  `mandate_schedules` with the default schedule; (5) the outbox and the email adapter
  rewrite (S1 root cause); (6) delete the PID-file code.
- **Parity risks:**
  - The live-PID protection behind P-225 becomes database-lock semantics.
  - `OPERATIONS_LOCK_PATH`, `OPERATIONS_LOCK_STALE_SECONDS` and `OPERATIONS_STATE_PATH`
    (P-315–P-317) become no-ops that log a deprecation warning.

## How parity is verified

- These tests keep their names and assertions and are reworded where locks changed:
  - `test_scheduler_restart_overlap_failure_and_enable_disable`;
  - `test_cycle_lock_never_reclaims_a_live_process`, which now asserts "never two
    concurrent cycles";
  - `test_cycle_lock_recovers_dead_and_old_invalid_owners`, which now asserts "a crashed
    worker's cycle is retried";
  - `test_cycle_cli_exit_codes_are_scheduler_safe`.
- These email tests pass on the outbox: `test_email_success_duplicate_and_test`,
  `test_email_retry_exhaustion_is_logged` and
  `test_email_refused_recipient_is_never_recorded_as_sent`.
- New outbox tests check that:
  - a daily digest is drained;
  - a test send is never deduplicated;
  - a crash while `sending` recovers;
  - an enqueue repeated with the same key is a no-op.
- The P-221 tick tests (`test_validator_failure_blocks_downstream_workers`) pass with an
  in-process worker.
