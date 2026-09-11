"""Bounded local CLI for auditable historical replay."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime

from venture_intelligence.config import settings
from venture_intelligence.replay.models import ReplayMode, ReplayRequest, ReplayStatus
from venture_intelligence.replay.service import ReplayService
from venture_intelligence.storage.db import Database


def _timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an ISO-8601 timestamp") from exc


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Rebuild historical projections in an isolated temporary store."
    )
    result.add_argument("--database", default=str(settings.db_path))
    result.add_argument("--until", required=True, type=_timestamp, dest="cutoff")
    result.add_argument("--from", type=_timestamp, dest="start_at")
    result.add_argument(
        "--mode",
        choices=[mode.value for mode in ReplayMode],
        default=ReplayMode.OBSERVED_AT.value,
    )
    result.add_argument("--max-events", type=int, default=5_000)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    request = ReplayRequest(
        cutoff=arguments.cutoff,
        start_at=arguments.start_at,
        mode=arguments.mode,
        max_events=arguments.max_events,
    )
    replay = ReplayService(Database(arguments.database)).run(request)
    print(replay.model_dump_json(indent=2))
    return 0 if replay.status in {ReplayStatus.COMPLETED, ReplayStatus.EMPTY} else 2


if __name__ == "__main__":
    raise SystemExit(main())
