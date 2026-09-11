# Historical evaluation

Milestone 3 evaluates historical predictions without consulting current derived
state. A validated `GroundTruthDataset` is compared with an isolated
`ReplayResult` selected by `observed_at <= cutoff` by default. `occurred_at`
reconstruction remains an explicit alternate mode; `ingested_at` is never the
historical knowledge boundary.

## Ground truth

Imports declare a program, batch, target level (`company`, `person`, or `both`),
outcome timestamp, public source URL, provenance, and canonical records. Names
and aliases are exact normalized identities: generic labels, overlaps,
duplicates, naive timestamps, empty provenance, and entity types inconsistent
with the declared target level are rejected. Empty record sets are valid so an
empty announced batch can be evaluated truthfully.

## Metrics and denominators

- Precision@10 and Precision@25 use fixed denominators of 10 and 25. Their
  sample size records how many historical rankings were actually available.
- Recall is unique positive identities ranked divided by positive ground-truth
  identities.
- Lead time is measured from the earliest replay evidence `observed_at` to the
  record-specific outcome time, or the dataset outcome timestamp.
- False positives are top-25 ranked identities not labelled positive; missed
  identities are classified as not discovered, evidence missing, signal
  extraction failure, or ranking failure.
- Alert acceptance and investment conversion use only explicit outcome labels
  attached to historically reconstructed `CONTACT` recommendations. They never
  inspect current CRM or alert tables. An unavailable denominator yields zero
  and remains visible in the metric contract.

Source ablation is bounded and reproducible. Each requested source exclusion
creates another disposable replay and reports metric deltas from baseline. It
does not contact providers or add new OSINT integrations.

## Isolation and persistence

Replay rebuilds validated projections in a temporary SQLite store and suppresses
collection, alerts, notification/email/Slack, CRM, and broadcasts. The live
database receives only canonical ground-truth audit snapshots and completed
evaluation JSON reports. Report and replay digests omit volatile timestamps, so
identical semantic inputs produce the same run ID and relevant result digest.
Evaluation version metadata is copied from the replay projection and records the
active feature, signal, ranking, recommendation, feedback, and CRM-action
versions alongside event/replay schemas. Historically eligible human feedback
may affect the reconstructed ranking, but remains a non-ground-truth input;
ground-truth datasets and realized outcome labels retain separate provenance.

## Local commands and read-only UI

```powershell
uv run python -m venture_intelligence.evaluation.cli `
  --database data/intelligence.db import --file ground-truth.json
uv run python -m venture_intelligence.evaluation.cli `
  --database data/intelligence.db run `
  --ground-truth <dataset-id> --cutoff 2026-01-15T12:00:00+00:00 `
  --ablate-source github
```

`GET /api/v1/evaluations/contract`, `GET /api/v1/evaluations`, and the run-detail
route are read-only. React `/evaluate` shows authoritative metrics, missed and
false-positive diagnostics, and provenance digests. Remote import/evaluation is
not exposed because the service has no administrative authentication boundary.

## Limitations

Metric quality depends on complete, correctly labelled ground truth. Exact
identity matching intentionally avoids fuzzy founder/company conflation but may
miss an unrecorded alias. Source ablation measures the current replay projection
and does not perform later-milestone ranking redesign or broad source expansion.
