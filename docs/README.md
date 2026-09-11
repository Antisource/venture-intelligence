# Documentation map

Start with the root [README](../README.md). The documents below describe the
current implementation unless explicitly marked as a dated audit record.

## Core design

- [Architecture](ARCHITECTURE.md) — runtime boundaries and data flow.
- [Data model](DATA_MODEL.md) and [JSON contracts](JSON_CONTRACTS.md) — canonical
  records, temporal semantics, and generated browser types.
- [Evidence and latest-batch discovery](LATEST_BATCH_DISCOVERY.md) — hypothesis,
  identity, program verification, and promotion rules.
- [Feature lineage](FEATURE_LINEAGE.md) — evidence to feature, signal, and rank.
- [Temporal graph](GRAPH.md), [replay](REPLAY.md), and
  [evaluation](EVALUATION.md) — as-of reasoning and isolated reconstruction.

## Research and decisions

- [Research Agent](RESEARCH_AGENT.md) and [source policy](SOURCES.md).
- [Knowledge layer](KNOWLEDGE_LAYER.md) and [CRM workflow](CRM.md).
- [Notifications](NOTIFICATIONS.md) and [conversational analyst](ANALYST.md).
- [API contract](API.md).

## Run and contribute

- [Product operations](PRODUCT_OPERATIONS.md) — installation, startup, CLI,
  scheduler, and database safety.
- [Testing](TESTING.md) and [troubleshooting](TROUBLESHOOTING.md).
- [Unattended operation](UNATTENDED_OPERATION.md).

## Dated audit records

- [Source acceptance matrix](SOURCE_ACCEPTANCE_MATRIX.md) and
  [operational validation](OPERATIONAL_VALIDATION.md) record bounded observations
  from specific dates. They are not guarantees of current third-party uptime.
- [Requirement matrix](REQUIREMENT_MATRIX.md) is historical implementation
  traceability, not current setup guidance.

Documents are intentionally split by stable user concern. Removed milestone
summaries and generated architecture reports should not be recreated as a
second source of operational truth.
