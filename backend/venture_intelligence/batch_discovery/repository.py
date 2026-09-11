"""SQLite persistence for immutable M8 observations and hypothesis revisions."""

from __future__ import annotations

from datetime import datetime

from venture_intelligence.batch_discovery.models import (
    BatchDiscoveryObservation,
    BatchDiscoveryRun,
    CandidateHypothesis,
)
from venture_intelligence.storage.db import Database


class BatchDiscoveryRepository:
    """Keeps candidate state separate from canonical entities and projections."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def save_observation(
        self,
        observation: BatchDiscoveryObservation,
        hypothesis_key: str,
    ) -> bool:
        with self.database._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO batch_discovery_observations
                  (observation_id, run_id, program_id, batch_id, hypothesis_key,
                   observed_at, origin_key, stance, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation.observation_id,
                    observation.run_id,
                    observation.program_id,
                    observation.batch_id,
                    hypothesis_key,
                    observation.observed_at.isoformat(),
                    observation.origin_key,
                    observation.stance.value,
                    observation.model_dump_json(),
                ),
            )
        return cursor.rowcount > 0

    def observations(
        self,
        program_id: str,
        batch_id: str,
        *,
        cutoff: datetime | None = None,
    ) -> list[BatchDiscoveryObservation]:
        clauses = ["program_id=?", "batch_id=?"]
        parameters: list[object] = [program_id, batch_id]
        if cutoff is not None:
            clauses.append("observed_at <= ?")
            parameters.append(cutoff.isoformat())
        with self.database._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM batch_discovery_observations WHERE "
                + " AND ".join(clauses)
                + " ORDER BY observed_at, observation_id",
                parameters,
            ).fetchall()
        return [BatchDiscoveryObservation.model_validate_json(row["payload"]) for row in rows]

    def observations_by_ids(self, observation_ids: list[str]) -> list[BatchDiscoveryObservation]:
        if not observation_ids:
            return []
        placeholders = ",".join("?" for _ in observation_ids)
        with self.database._connect() as connection:
            rows = connection.execute(
                f"SELECT payload FROM batch_discovery_observations "
                f"WHERE observation_id IN ({placeholders}) ORDER BY observed_at, observation_id",
                observation_ids,
            ).fetchall()
        return [BatchDiscoveryObservation.model_validate_json(row["payload"]) for row in rows]

    def next_revision(self, hypothesis_id: str) -> int:
        with self.database._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(revision), 0) AS revision "
                "FROM batch_candidate_hypotheses WHERE hypothesis_id=?",
                (hypothesis_id,),
            ).fetchone()
        return int(row["revision"]) + 1

    def save_hypothesis(self, hypothesis: CandidateHypothesis) -> CandidateHypothesis:
        with self.database._connect() as connection:
            existing = connection.execute(
                "SELECT payload FROM batch_candidate_hypotheses "
                "WHERE hypothesis_id=? AND input_digest=?",
                (hypothesis.hypothesis_id, hypothesis.input_digest),
            ).fetchone()
            if existing is not None:
                return CandidateHypothesis.model_validate_json(existing["payload"])
            connection.execute(
                """
                INSERT INTO batch_candidate_hypotheses
                  (revision_id, hypothesis_id, revision, run_id, program_id,
                   batch_id, state, confidence_pct, observed_at, input_digest, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hypothesis.revision_id,
                    hypothesis.hypothesis_id,
                    hypothesis.revision,
                    hypothesis.run_id,
                    hypothesis.program_id,
                    hypothesis.batch_id,
                    hypothesis.state.value,
                    hypothesis.assessment.confidence_pct,
                    hypothesis.last_updated_at.isoformat(),
                    hypothesis.input_digest,
                    hypothesis.model_dump_json(),
                ),
            )
        return hypothesis

    def latest_hypotheses(
        self,
        program_id: str,
        batch_id: str,
        *,
        include_rejected: bool = True,
    ) -> list[CandidateHypothesis]:
        with self.database._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload FROM batch_candidate_hypotheses
                WHERE program_id=? AND batch_id=?
                ORDER BY hypothesis_id, revision DESC
                """,
                (program_id, batch_id),
            ).fetchall()
        latest: dict[str, CandidateHypothesis] = {}
        for row in rows:
            item = CandidateHypothesis.model_validate_json(row["payload"])
            latest.setdefault(item.hypothesis_id, item)
        result: list[CandidateHypothesis] = []
        for item in latest.values():
            if not include_rejected and item.state.value == "rejected":
                continue
            result.append(self._with_canonical_ids(item))
        return sorted(
            result,
            key=lambda item: (
                -item.assessment.confidence_pct,
                item.display_name.casefold(),
            ),
        )

    def hypothesis(self, hypothesis_id: str) -> CandidateHypothesis | None:
        with self.database._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM batch_candidate_hypotheses "
                "WHERE hypothesis_id=? ORDER BY revision DESC LIMIT 1",
                (hypothesis_id,),
            ).fetchone()
        if row is None:
            return None
        return self._with_canonical_ids(CandidateHypothesis.model_validate_json(row["payload"]))

    def save_run(self, run: BatchDiscoveryRun) -> None:
        with self.database._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO batch_discovery_runs
                  (run_id, program_id, batch_id, started_at, finished_at,
                   status, input_digest, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.program_id,
                    run.batch_id,
                    run.started_at.isoformat(),
                    run.finished_at.isoformat() if run.finished_at else None,
                    run.status,
                    run.input_digest,
                    run.model_dump_json(),
                ),
            )

    def runs(
        self,
        *,
        program_id: str | None = None,
        limit: int = 50,
    ) -> list[BatchDiscoveryRun]:
        where = " WHERE program_id=?" if program_id else ""
        parameters: tuple[object, ...] = (program_id,) if program_id else ()
        with self.database._connect() as connection:
            rows = connection.execute(
                f"SELECT payload FROM batch_discovery_runs{where} ORDER BY started_at DESC LIMIT ?",
                (*parameters, max(1, min(limit, 200))),
            ).fetchall()
        return [BatchDiscoveryRun.model_validate_json(row["payload"]) for row in rows]

    def run(self, run_id: str) -> BatchDiscoveryRun | None:
        with self.database._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM batch_discovery_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
        return BatchDiscoveryRun.model_validate_json(row["payload"]) if row is not None else None

    def state_counts(self) -> dict[str, int]:
        tables = (
            "batch_discovery_runs",
            "batch_discovery_observations",
            "batch_candidate_hypotheses",
            "events",
            "persons",
            "companies",
        )
        with self.database._connect() as connection:
            return {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in tables
            }

    def _with_canonical_ids(self, item: CandidateHypothesis) -> CandidateHypothesis:
        return item.model_copy(
            update={
                "canonical_person_id": (
                    self.database.resolve_entity_id(item.promoted_person_source_id)
                    if item.promoted_person_source_id
                    else None
                ),
                "canonical_company_id": (
                    self.database.resolve_entity_id(item.promoted_company_source_id)
                    if item.promoted_company_source_id
                    else None
                ),
            }
        )
