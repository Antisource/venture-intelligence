"""CLI boundary for explicitly gated M9 live-source validation."""

from __future__ import annotations

import argparse
import asyncio

from venture_intelligence.config import settings
from venture_intelligence.operations.validation import run_live_validation


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--program", required=True)
    result.add_argument("--allow-network", action="store_true")
    result.add_argument("--ground-truth")
    result.add_argument("--report", default=settings.source_quality_report_path)
    result.add_argument("--max-queries", type=int, default=8)
    result.add_argument("--max-requests", type=int, default=24)
    return result


def main() -> None:
    args = parser().parse_args()
    report = asyncio.run(
        run_live_validation(
            program_id=args.program,
            report_path=args.report,
            allow_network=args.allow_network,
            ground_truth_path=args.ground_truth,
            max_queries=args.max_queries,
            max_requests=args.max_requests,
        )
    )
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
