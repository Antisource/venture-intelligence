"""Compatibility boundary for the M3 replay-backed evaluation service."""

from venture_intelligence.evaluation.models import (
    EvaluationRequest,
    EvaluationRun,
    GroundTruthDataset,
    GroundTruthImportRequest,
)
from venture_intelligence.evaluation.service import EvaluationService
from venture_intelligence.storage.db import Database


class EvaluationFramework:
    """Backward import-compatible facade over the authoritative M3 service."""

    def __init__(self, db: Database) -> None:
        self._service = EvaluationService(db)

    def import_ground_truth(self, request: GroundTruthImportRequest) -> GroundTruthDataset:
        return self._service.import_ground_truth(request)

    def run(self, request: EvaluationRequest) -> EvaluationRun:
        return self._service.run(request)

    def evaluate(self, *_args: object, **_kwargs: object) -> EvaluationRun:
        raise TypeError(
            "The ambiguous live-state evaluate() API was removed in M3. "
            "Import typed ground truth, then call run(EvaluationRequest)."
        )
