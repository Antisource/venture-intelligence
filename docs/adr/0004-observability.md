# ADR-0004: Observability

- **Status:** Proposed
- **Date:** 2026-11-23
- **Related:** ADR-0003, ADR-0005, ADR-0006, D-014

## Context

- **Metrics:** `observability/metrics.py` is an in-process collector (P-244). Nothing
  exports it.
- **Logging:** stdlib `logging` with free-form messages. Email errors are truncated to
  500 characters and carry no SMTP code or phase (FINDINGS §5 cause 8).
- **Health:** `GET /api/health` returns the database path (FINDINGS §10.4, P-007).
  `system_readiness()` creates and migrates the database as a side effect (FINDINGS §10).
  Readiness and the actual send path can disagree about email configuration (FINDINGS §5
  cause 7).
- **UI:** the System pages dump request, retry and latency tables and raw JSON into
  default navigation (FINDINGS §6).
- **New moving parts:** a worker, a queue and an outbox (ADR-0003) need visibility.
  Hosted mode needs an operator view. Local mode must stay light.

## Decision

1. **Logs are mandatory.** We use **structlog** (MIT OR Apache-2.0; 25.x, verify) over
   stdlib logging.
   - **Output:** JSON lines on stdout in containers and hosted mode; a console renderer
     on a TTY.
   - **Standard fields:** `ts`, `level`, `event`, `logger`, `request_id`, `job_id`,
     `task`, `mandate_id`, opaque `workspace_id` and `user_id`, `capability_id`, plus
     `trace_id` and `span_id` when tracing is on.
   - **Redaction:** a processor drops values whose keys look like secrets (password,
     token, api_key, authorization, cookie, webhook).
   - **People data stays out of logs.** Person names, email addresses and people-level
     query text are never logged at INFO or above. People-level access is recorded in the
     audit table (ADR-0006).
   - Uvicorn, httpx, SQLAlchemy and Procrastinate loggers go through the same pipeline.
   - Failures carry `error.code` and `error.phase` (for example `smtp.tls`, `smtp.auth`).
2. **Metrics are mandatory and cheap.** We use **prometheus-client** (Apache-2.0).
   - `GET /metrics` is served by the API and by the worker (on its own port). In hosted
     mode, Caddy does not route `/metrics` publicly.
   - **Core series:**
     - HTTP rate, errors and duration, labelled by route template;
     - job counts, duration and retries by task and outcome;
     - queue depth and oldest-job age;
     - outbox state counts and delivery latency by channel;
     - connector outcomes by source, using the existing typed statuses (`AUTH_REQUIRED`,
       `BLOCKED`, `RATE_LIMITED`);
     - model calls by provider, outcome and fallback, with token counts;
     - database pool usage.
   - The existing collector stays the domain-level source and is bridged into Prometheus
     collectors (P-244).
   - **Label rule:** entity ids, user ids and URLs are never labels.
3. **Tracing is optional (D-014).** The OpenTelemetry Python SDK and the OTLP exporter
   (Apache-2.0) ship as the extra `venture-intelligence[otel]`.
   - Tracing is enabled only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.
   - Instrumentation covers FastAPI, SQLAlchemy, httpx and job execution. Trace context
     travels in job payload headers, so an enqueue links to its run.
   - Without the extra installed, tracing costs nothing.
4. **Health endpoints.**
   - `GET /livez`: the process is alive; no database access.
   - `GET /readyz`: the database is reachable, the schema is at Alembic head, and the
     worker heartbeat is within twice its interval (when the topology has a worker).
   - Readiness never writes or migrates.
   - `/api/health` keeps its `{status, project, db}` shape (P-007), but `db` now reports
     `"postgresql"` or `"unavailable"` instead of a filesystem path.
   - `vi doctor` (P-074) calls the same readiness service, so the CLI, the API and the UI
     report the same state.
5. **The `observability` compose profile is optional.** It runs Prometheus (Apache-2.0),
   Grafana (AGPL-3.0) with provisioned dashboards, and Jaeger all-in-one (Apache-2.0) as
   the OTLP endpoint. None of these are needed to run the product.
6. **Honest UI.** Operational telemetry moves to L4 and the Advanced or audit
   disclosure (FINDINGS §6 consequence). Default lenses show only product state.

## Alternatives considered

- **OpenTelemetry for all three signals, with a Collector:**
  - For: one vendor-neutral pipeline.
  - Against: it needs an extra Collector service to be useful, and the Python logs signal
    has lagged traces and metrics in maturity (verify current status). It also makes the
    default local install heavier.
  - Outcome: adopted for traces only.
- **python-json-logger (BSD-2) on plain stdlib:**
  - For: one fewer concept.
  - Against: context binding and redaction would be hand-built. structlog's contextvars
    binding and processor chain are the reason to take the dependency.
- **Loki and Tempo (AGPL-3.0) as defaults:** good tools, but each is another stateful
  service. They are easy to add later through OTLP.
- **Sentry:** the server is under the FSL, which is not OSI-approved. GlitchTip (MIT) is a
  possible later add-on for error grouping.

## Consequences

- **New runtime dependencies:** structlog and prometheus-client, both small and pure
  Python.
- **Migration steps:**
  1. Add the logging configuration and redaction processor.
  2. Add request-id middleware and job context binding.
  3. Add the metrics registry and the collector bridge.
  4. Add `/livez` and `/readyz`, and remove readiness side effects.
  5. Redact `/api/health`.
  6. Add the `[otel]` extra.
  7. Add the compose profile and dashboards.
- **Parity risks:**
  - The `db` value of `/api/health` changes. This is a security fix. The frontend only
    checks that the response is a record (`frontend/src/api/client.ts:181-185`); verify
    that P-134 renders from `status` alone.
  - `vi doctor` on an uninitialised database now reports "run `vi db upgrade`" with exit
    code 2 instead of silently creating the database.

## How parity is verified

- `test_concurrent_evidence_metrics_remain_operation_attributed` (P-244) and
  `test_doctor_human_output_is_concise_but_json_preserves_diagnostics` (P-074) pass
  unchanged.
- New tests check that:
  - a configured secret never appears in captured logs or in `/metrics`;
  - `/readyz` fails when the schema is behind head;
  - `/api/health` contains no filesystem path;
  - metric label sets have bounded cardinality;
  - readiness performs no writes, asserted with a read-only database role.
