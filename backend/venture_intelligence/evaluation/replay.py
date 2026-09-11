"""Compatibility boundary for the Milestone 2 isolated replay service.

Evaluation metrics remain deferred. This module intentionally cannot replay
into the live database or combine replay with evaluation.
"""

from __future__ import annotations

from datetime import datetime

from venture_intelligence.replay.models import ReplayMode, ReplayRequest, ReplayResult
from venture_intelligence.replay.service import ReplayService
from venture_intelligence.storage.db import Database


class BatchReplayer:
    """Backward-compatible name backed by the safe isolated replay service."""

    def __init__(self, db: Database) -> None:
        self._service = ReplayService(db)

    def replay(
        self,
        until: datetime,
        *,
        mode: ReplayMode = ReplayMode.OBSERVED_AT,
        start_at: datetime | None = None,
        max_events: int = 5_000,
    ) -> ReplayResult:
        return self._service.run(
            ReplayRequest(
                cutoff=until,
                mode=mode,
                start_at=start_at,
                max_events=max_events,
            )
        )

    def replay_and_evaluate(self, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError(
            "Evaluation and ground-truth metrics are deferred beyond Milestone 2; "
            "use BatchReplayer.replay() for isolated reconstruction."
        )
