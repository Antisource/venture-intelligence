"""Evaluation results, ground truth datasets and evaluation runs."""

from typing import Any

from venture_intelligence.shared.models import (
    EvaluationResult,
)


class EvaluationMixin:
    # ════════════════════════════════════════════════════════════════════════════
    # EVALUATION
    # ════════════════════════════════════════════════════════════════════════════

    def write_evaluation(self, result: EvaluationResult) -> None:
        import uuid

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO evaluation_results VALUES
                  (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(uuid.uuid4()),
                    result.batch,
                    self._ts(result.evaluated_at),
                    result.precision_at_10,
                    result.precision_at_25,
                    result.recall,
                    result.avg_lead_time_days,
                    result.median_lead_time_days,
                    result.false_positives,
                    result.missed_companies,
                    result.alert_acceptance_rate,
                    result.investment_conversion_rate,
                    result.total_entities_ranked,
                    result.notes,
                ),
            )

    def get_evaluations(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM evaluation_results ORDER BY evaluated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def write_ground_truth_dataset(self, dataset: Any) -> None:
        """Persist a validated, immutable ground-truth snapshot."""
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT payload FROM ground_truth_datasets WHERE dataset_id=?",
                (dataset.dataset_id,),
            ).fetchone()
            payload = dataset.model_dump_json()
            if existing is not None:
                # Identical semantic imports are idempotent.  The first import
                # timestamp remains authoritative for audit history.
                return
            conn.execute(
                """
                INSERT INTO ground_truth_datasets
                  (dataset_id, program_id, batch_id, target_level,
                   outcome_timestamp, digest, imported_at, payload)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    dataset.dataset_id,
                    dataset.program_id,
                    dataset.batch_id,
                    dataset.target_level.value,
                    self._ts(dataset.outcome_timestamp),
                    dataset.digest,
                    self._ts(dataset.imported_at),
                    payload,
                ),
            )

    def get_ground_truth_dataset(self, dataset_id: str) -> Any | None:
        from venture_intelligence.evaluation.models import GroundTruthDataset

        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM ground_truth_datasets WHERE dataset_id=?",
                (dataset_id,),
            ).fetchone()
        return GroundTruthDataset.model_validate_json(row["payload"]) if row else None

    def write_evaluation_run(self, result: Any) -> None:
        """Persist only the evaluation report; operational projections stay untouched."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO evaluation_runs_v2
                  (evaluation_run_id, program_id, batch_id, target_level,
                   cutoff, outcome_timestamp, status, report_digest, created_at, payload)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    result.evaluation_run_id,
                    result.program_id,
                    result.batch_id,
                    result.target_level.value,
                    self._ts(result.cutoff),
                    self._ts(result.outcome_timestamp),
                    result.status.value,
                    result.report_digest,
                    self._ts(result.created_at),
                    result.model_dump_json(),
                ),
            )

    def get_evaluation_run(self, evaluation_run_id: str) -> Any | None:
        from venture_intelligence.evaluation.models import EvaluationRun

        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM evaluation_runs_v2 WHERE evaluation_run_id=?",
                (evaluation_run_id,),
            ).fetchone()
        return EvaluationRun.model_validate_json(row["payload"]) if row else None

    def list_evaluation_runs(self) -> list[Any]:
        from venture_intelligence.evaluation.models import EvaluationRun

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM evaluation_runs_v2 ORDER BY created_at DESC"
            ).fetchall()
        return [EvaluationRun.model_validate_json(row["payload"]) for row in rows]
