# Notifications

Email is an SMTP provider abstraction. Configure only the recipient and
preferences through Settings; SMTP host, credentials, and sender remain in
environment variables (`EMAIL_SMTP_*` or `SMTP_*`) and are never written by
the UI. Delivery is optional for process startup and research completion.

Preferences include enabled state, alert categories, frequency
(`immediate`, `daily`, or `weekly`), and a minimum confidence threshold.
Immediate alerts are delivered through configured SMTP. Digest preferences
record a durable queued state rather than bypassing a user's cadence choice;
the local foreground intelligence scheduler is available separately and never
silently sends a digest. Failed delivery uses bounded exponential backoff and
is recorded without crashing the research pipeline.

`POST /api/settings/email/digest` and `venture-intelligence email preview
--digest` build the current evidence-backed partner digest locally. Preview is
always available. `send=true`/`email send --digest` attempts delivery only when
recipient and SMTP configuration are present. Otherwise the response states
`DELIVERY NOT CONFIGURED — PREVIEW AVAILABLE`; it never fabricates `SENT`.

Milestone 5 adds append-only alert lifecycle events: `created`, `dispatching`,
`delivered`, `partial`, `failed`, `acknowledged`, `dismissed`, and `resolved`.
Delivery records remain separate from recommendation review state and preserve
the recommendation, entity, CRM-action lineage, actor, reason, provenance, and
time. Console delivery is the credential-free local sink. SMTP and Slack remain
optional; a failed optional sink produces a truthful partial/failed state and
never changes the underlying recommendation.
