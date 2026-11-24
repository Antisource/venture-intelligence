# D-013: Procrastinate on Postgres for jobs and schedules; delivery is an at-least-once outbox

- **Date:** 2026-11-23
- **Area:** Jobs and scheduling
- **Differs from owner instruction:** No (adds the component and the delivery guarantee)

1. **Chosen:** Procrastinate (MIT) runs on the application Postgres, behind our own
   `JobQueue` port. It has retries, idempotency keys, `queueing_lock` and `lock`, and
   periodic tasks. User mandates are rows that a periodic `scheduler.tick` turns into
   jobs; `UNIQUE(mandate_id, window_start)` lets each window run at most once. Together
   these replace the PID-file scheduler and the monitor's loops. Delivery goes through a
   transactional `delivery_outbox` that is drained with `SKIP LOCKED`. See ADR-0003.
2. **Owner instruction:** a real job queue and scheduler with retries and idempotency;
   no required paid or hosted service.
3. **Why:** Postgres is already required (ADR-0001), so a Postgres-native queue adds no
   service and keeps local mode light. Celery, Dramatiq and RQ need a Redis, Valkey or
   RabbitMQ broker, and Redis licensing has changed twice. APScheduler is a scheduler, not
   a durable queue.
   **Trade-offs:** Procrastinate is less widely deployed than Celery; the port and a
   pinned version keep a hand-rolled `SKIP LOCKED` queue as a fallback. SMTP has no
   idempotency, so delivery is at-least-once: a crash after the server accepts a message
   but before its state is written can duplicate that one message. A stable `Message-ID`
   derived from the idempotency key lets mail clients collapse it. Exactly-once delivery
   is not promised.
4. **Parity:** the scheduler and lock tests behind P-224 and P-225 keep their names, with
   assertions restated for database locks. `cycle run` still exits 3 while a cycle runs
   (P-073). `PUT /api/v1/scheduler` (P-049) now really starts and stops scheduling.
   `OPERATIONS_LOCK_PATH`, `OPERATIONS_LOCK_STALE_SECONDS` and `OPERATIONS_STATE_PATH`
   (P-315–P-317) become no-ops with a deprecation warning.
