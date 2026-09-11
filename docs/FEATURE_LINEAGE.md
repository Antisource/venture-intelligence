# Versioned feature and ranking lineage

Milestone 4 inserts an explicit, versioned feature layer between immutable
evidence and normalized signals:

```text
CanonicalEvent → Observation → Evidence → Feature → Signal
              → Hypothesis → Ranking → Recommendation
```

`Feature` is a measurable value, not an observation, claim, or score. Each row
records `feature_version`, `as_of`, `computed_at`, input evidence/observation
IDs, formula, parameters, source/program metadata, confidence, and a canonical
input digest. IDs are UUID5 values derived from semantic inputs, so identical
inputs, cutoff, and version reproduce the same feature and downstream signal
and ranking identities.

## Historical semantics

Operational and replay computation defaults to evidence with
`observed_at <= as_of`. Future observations cannot enter an earlier feature,
signal, or ranking. Isolated replay recomputes features from its disposable
event projection and includes feature/signal/ranking versions in replay and
evaluation metadata. It never reads current derived feature rows.

The `0003_feature_lineage` migration is additive. A pre-M4 `features` table is
rebuilt transactionally only to remove its legacy `UNIQUE(entity_id, name)`
constraint; existing rows are retained as `feature_version=legacy-1`. New
measurements append rather than overwriting history.

## Feature formulas

The M4 worker exposes deliberately simple formulas including evidence/source
counts, program-membership confidence, repository/star/commit activity,
30-day hiring, research publications, public launches/community observations,
and company-website presence. Signals reference the exact feature IDs used;
rank dimensions reference their exact signals and features. Repository stars
are entity-scoped and can never leak from another graph node or entity.

The stored signal enum value `yc_likelihood` remains a compatibility identifier
until the coordinated M6 program-contract migration. Its M4 computation and UI
wording are program-generic, and `program_id`/`batch_id` flow from evidence
metadata when present. Legacy YC-specific evidence claims are accepted only as
compatibility inputs; they are not new feature semantics.

## Read-only API and frontend

- `GET /api/v1/intelligence/rankings` returns integrity-gated current/as-of
  rankings, movement, and top referenced feature/signal.
- `GET /api/v1/intelligence/entities/{entity_id}` returns a validated founder or
  company with evidence, features, signals, hypotheses, ranking history,
  recommendation, and typed lineage edges.

The native React intelligence flow begins in `/discover` and opens validated
entities at `/research/:entityId`. Historical founder/company URLs remain
compatibility redirects into those workspaces. The views consume generated
OpenAPI types; selecting a signal shows only its referenced features and their
supporting evidence/source URLs. The UI does not compute or invent facts.
