"""Export the authoritative FastAPI schema for frontend type generation."""

from __future__ import annotations

import json
from pathlib import Path

from venture_intelligence.api.app import app


def main() -> None:
    destination = Path(__file__).resolve().parents[1] / "frontend" / "src" / "api"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "openapi.json").write_text(
        json.dumps(app.openapi(), indent=2, sort_keys=True),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
