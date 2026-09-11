"""Atomic JSON persistence for non-authoritative operational reports."""

from __future__ import annotations

import os
from pathlib import Path

from venture_intelligence.operations.models import OperationalValidationReport


class SourceQualityReportStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> OperationalValidationReport | None:
        if not self.path.exists():
            return None
        return OperationalValidationReport.model_validate_json(
            self.path.read_text(encoding="utf-8")
        )

    def save(self, report: OperationalValidationReport) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, self.path)
