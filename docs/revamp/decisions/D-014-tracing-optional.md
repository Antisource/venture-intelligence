# D-014: Structured logs and metrics are mandatory; OpenTelemetry tracing is optional

- **Date:** 2026-11-23
- **Area:** Observability
- **Differs from owner instruction:** Partly (tracing is opt-in, not always on)

1. **Chosen:** structlog JSON logs with redaction, and prometheus-client metrics on
   `/metrics`, are always present. OpenTelemetry tracing ships as the optional extra
   `[otel]` and is active only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. Prometheus,
   Grafana and Jaeger run only in an optional compose profile. See ADR-0004.
2. **Owner instruction:** structured logs, metrics and tracing; local mode must stay
   light.
3. **Why:** traces are useful only with a collector or backend running, which a solo
   local install should not need. Logs and pull-based metrics cost almost nothing and
   need no extra service.
   **Trade-off:** a local user who hits a latency problem must install the extra and
   start the profile before traces exist. Hosted operators are told to enable tracing.
4. **Parity:** the P-244 metrics collector is bridged into Prometheus unchanged.
   `/api/health` (P-007) keeps its shape but stops returning the database path (a
   security fix). `vi doctor` (P-074) uses the same readiness service as `/readyz`.
