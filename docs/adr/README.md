# Architecture decision records

These records describe the target architecture of the revamp. Each one covers a single
architectural decision: its context, the alternatives weighed, the consequences, and how
the 353-item feature-parity contract (`docs/revamp/PARITY.md`) stays intact.

The evidence behind the context sections is the baseline audit
(`docs/revamp/FINDINGS.md`). When an ADR chooses something that differs from, or adds to,
the owner's instructions, that choice is also recorded as a `D-NNN` entry in
`docs/revamp/DECISIONS.md`.

**Status values:**

- **Proposed:** written, not yet accepted by the owner.
- **Accepted:** approved; implementation may rely on it.
- **Superseded by ADR-NNNN:** replaced by a later record.
- **Deprecated:** no longer applies.

Only the owner moves an ADR out of Proposed. To change an accepted decision, write a new
ADR that supersedes it; do not rewrite the accepted one.

## Index

| ADR | Title | Status | Decision log |
|---|---|---|---|
| [0001](0001-postgresql-primary-datastore.md) | PostgreSQL as the primary datastore | Proposed | D-010, D-011 |
| [0002](0002-migrations-and-evidence-constraints.md) | Schema migrations, evidence invariants as constraints, and the SQLite import | Proposed | D-012 |
| [0003](0003-job-queue-and-scheduler.md) | Job queue, scheduler and delivery outbox | Proposed | D-013 |
| [0004](0004-observability.md) | Observability | Proposed | D-014 |
| [0005](0005-deployment-and-topologies.md) | Deployment and topologies | Proposed | D-015 |
| [0006](0006-auth-tenancy-isolation.md) | Authentication, tenancy and isolation | Proposed | D-016, D-017, D-018 |
| [0007](0007-module-boundaries-service-layer.md) | Module boundaries and the single service layer | Proposed | D-019 |
| [0008](0008-model-provider-interface-byok.md) | Model provider interface and bring-your-own-key | Proposed | D-020 |

## Conventions

- **Licences:** every component has an OSI-approved licence, named by its SPDX id. No
  required component may be a paid or hosted service; optional ones are marked optional.
- **Versions:** an ADR names the version it targets. "Verify" means the version or fact
  must be re-checked when the ADR is implemented.
- **References:** context cites `FINDINGS §n`, `P-NNN` parity rows, and `path:line` at
  the baseline commit.
- **Verification:** every ADR ends with "How parity is verified", naming existing tests
  or the new tests to write before the code behind them changes.

## Template

```markdown
# ADR-NNNN: <title>

- **Status:** Proposed
- **Date:** YYYY-MM-DD
- **Related:** <ADR-NNNN, D-NNN>

## Context

<The forces at play, with concrete references: FINDINGS sections, PARITY rows,
file paths. State the owner's binding requirements that apply.>

## Decision

<What we will do, specifically: components, versions, licences, interfaces.>

## Alternatives considered

<At least two, each with honest trade-offs and the reason it lost.>

## Consequences

<Positive and negative effects, migration steps in order, and parity risks.>

## How parity is verified

<Existing tests that must keep passing, new tests to write first, and any
digest or byte-identity checks.>
```
