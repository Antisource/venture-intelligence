"""Local-only CLI for ground-truth import and historical evaluation."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from venture_intelligence.evaluation.models import (
    EvaluationRequest,
    GroundTruthImportRequest,
    SourceAblationSpec,
)
from venture_intelligence.evaluation.service import EvaluationService
from venture_intelligence.replay.models import ReplayMode
from venture_intelligence.shared.models import Source
from venture_intelligence.storage.db import Database


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local historical evaluation")
    parser.add_argument("--database", required=True, help="SQLite database path")
    commands = parser.add_subparsers(dest="command", required=True)
    import_command = commands.add_parser("import", help="Import validated ground truth JSON")
    import_command.add_argument("--file", required=True)
    run_command = commands.add_parser("run", help="Run isolated historical evaluation")
    run_command.add_argument("--ground-truth", required=True)
    run_command.add_argument("--cutoff", required=True)
    run_command.add_argument(
        "--mode",
        choices=[item.value for item in ReplayMode],
        default="observed_at",
    )
    run_command.add_argument("--max-events", type=int, default=5_000)
    run_command.add_argument(
        "--ablate-source",
        action="append",
        default=[],
        choices=[item.value for item in Source],
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    service = EvaluationService(Database(args.database))
    if args.command == "import":
        payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
        result = service.import_ground_truth(GroundTruthImportRequest.model_validate(payload))
    else:
        ablations = [
            SourceAblationSpec(
                ablation_id=f"without_{source}",
                excluded_sources=[Source(source)],
            )
            for source in args.ablate_source
        ]
        result = service.run(
            EvaluationRequest(
                ground_truth_dataset_id=args.ground_truth,
                cutoff=datetime.fromisoformat(args.cutoff),
                mode=ReplayMode(args.mode),
                max_events=args.max_events,
                ablations=ablations,
            )
        )
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
