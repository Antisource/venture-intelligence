"""The generated decision index (docs/revamp/DECISIONS.md) is an owned contract.

Oracle: the per-decision files under docs/revamp/decisions/ and the format
documented in scripts/docs/decision_index.py.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "docs" / "decision_index.py"
_spec = importlib.util.spec_from_file_location("decision_index", _PATH)
assert _spec and _spec.loader
decision_index = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = decision_index  # @dataclass resolves its module here
_spec.loader.exec_module(decision_index)


def test_committed_index_is_current() -> None:
    assert decision_index.main(["--check"]) == 0


def _write(directory: Path, name: str, text: str) -> None:
    (directory / name).write_text(text, encoding="utf-8")


def test_index_lists_decisions_in_id_order_with_links(tmp_path: Path) -> None:
    body = (
        "- **Date:** 2026-11-23\n- **Area:** Area {n}\n"
        "- **Differs from owner instruction:** No\n"
    )
    _write(tmp_path, "D-010-later.md", "# D-010: Later\n\n" + body.format(n=10))
    _write(tmp_path, "D-002-earlier.md", "# D-002: Earlier\n\n" + body.format(n=2))
    rendered = decision_index.render(tmp_path).splitlines()
    rows = [line for line in rendered if line.startswith("| D-")]
    assert rows == [
        "| D-002 | 2026-11-23 | Area 2 | No | [Earlier](decisions/D-002-earlier.md) |",
        "| D-010 | 2026-11-23 | Area 10 | No | [Later](decisions/D-010-later.md) |",
    ]


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("D-001-x.md", "# D-001: X\n\n- **Date:** 2026-11-23\n- **Area:** A\n"),
        ("D-001-x.md", "# D-002: X\n\n- **Date:** d\n- **Area:** a\n"
         "- **Differs from owner instruction:** No\n"),
        ("decision-1.md", "# D-001: X\n"),
    ],
)
def test_malformed_decisions_are_rejected(tmp_path: Path, name: str, text: str) -> None:
    _write(tmp_path, name, text)
    with pytest.raises(ValueError):
        decision_index.render(tmp_path)
