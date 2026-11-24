"""`vi capabilities`, the `vi` entry point and `doctor` model reporting."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from venture_intelligence.capabilities import capabilities_for
from venture_intelligence.cli import main
from venture_intelligence.config import settings

ROOT = Path(__file__).resolve().parents[1]


def test_vi_is_an_alias_of_the_legacy_entry_point() -> None:
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "scripts"
    ]
    assert scripts["vi"] == scripts["venture-intelligence"] == "venture_intelligence.cli:main"


@pytest.mark.parametrize(
    "argv",
    [
        ["capabilities", "--lens", "L1", "--json"],
        ["--json", "capabilities", "--lens", "L1"],
    ],
)
def test_capabilities_json_matches_the_lens_view(argv, capsys) -> None:
    assert main(argv) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == capabilities_for("L1").model_dump(mode="json")
    assert not [item for item in payload["capabilities"] if item["people_data"]]


def test_capabilities_defaults_to_the_technical_lens(capsys) -> None:
    assert main(["capabilities", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["lens"] == "L4" and payload["teasers"] == []


def test_capabilities_human_output_lists_commands_and_teasers(capsys) -> None:
    assert main(["capabilities", "--lens", "L2"]) == 0
    output = capsys.readouterr().out
    assert "Lens L2 (Analyst)" in output
    assert "vi rankings list" in output
    assert "Higher lenses add:" in output and "(L3)" in output


def test_capabilities_rejects_unknown_lens() -> None:
    with pytest.raises(SystemExit) as failure:
        main(["capabilities", "--lens", "L5"])
    assert failure.value.code == 2


def _probe(names: list[str] | None):
    calls: list[str] = []

    async def probe(base_url: str, timeout: float) -> list[str] | None:
        calls.append(base_url)
        return names

    return probe, calls


@pytest.fixture
def ollama_selected(monkeypatch: pytest.MonkeyPatch) -> str:
    # The extractor uses Ollama; the analyst stays deterministic, so its own
    # health check makes no network call.
    monkeypatch.setattr(settings, "ollama_enabled", True)
    monkeypatch.setattr(settings, "analyst_provider", "local")
    monkeypatch.setattr(settings, "ollama_model", "granite3.3:2b")
    return "granite3.3:2b"


def _doctor(tmp_path: Path, probe, capsys) -> tuple[int, dict]:
    code = main(["--json", "--database", str(tmp_path / "doctor.db"), "doctor"], model_probe=probe)
    return code, json.loads(capsys.readouterr().out)


def test_doctor_reports_a_missing_model_by_name(tmp_path, capsys, ollama_selected) -> None:
    probe, calls = _probe(["llama3.2:latest"])
    code, payload = _doctor(tmp_path, probe, capsys)

    assert calls == [settings.ollama_base_url]
    model = payload["model"]
    assert model["status"] == "MISSING"
    assert model["model"] == ollama_selected
    assert f"ollama pull {ollama_selected}" in model["action"]
    assert ollama_selected in model["summary"]
    assert payload["help"][0] == model["action"]
    assert any(hint.startswith("vi inbox") for hint in payload["help"])

    present, _ = _probe([f"{ollama_selected}"])
    present_code, present_payload = _doctor(tmp_path, present, capsys)
    assert present_payload["model"]["status"] == "PRESENT"
    assert present_payload["model"]["action"] is None
    # Model presence is reported, not folded into the existing exit codes.
    assert code == present_code


def test_doctor_reports_unreachable_ollama_explicitly(tmp_path, capsys, ollama_selected) -> None:
    probe, _ = _probe(None)
    _, payload = _doctor(tmp_path, probe, capsys)
    assert payload["model"]["status"] == "UNREACHABLE"
    assert f"ollama pull {ollama_selected}" in payload["model"]["action"]


def test_doctor_accepts_latest_tag_for_untagged_model(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ollama_enabled", True)
    monkeypatch.setattr(settings, "analyst_provider", "local")
    monkeypatch.setattr(settings, "ollama_model", "venture-intelligence-extractor")
    probe, _ = _probe(["venture-intelligence-extractor:latest"])
    _, payload = _doctor(tmp_path, probe, capsys)
    assert payload["model"]["status"] == "PRESENT"


def test_doctor_skips_probe_when_no_feature_uses_ollama(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ollama_enabled", False)
    monkeypatch.setattr(settings, "analyst_provider", "local")
    probe, calls = _probe(["anything"])
    _, payload = _doctor(tmp_path, probe, capsys)
    assert calls == []
    assert payload["model"]["status"] == "NOT_SELECTED"


def test_doctor_human_output_names_the_fix(tmp_path, capsys, ollama_selected) -> None:
    probe, _ = _probe([])
    main(["--database", str(tmp_path / "doctor.db"), "doctor"], model_probe=probe)
    output = capsys.readouterr().out
    assert f"Ollama model {ollama_selected}: MISSING" in output
    assert f"Fix: Run: ollama pull {ollama_selected}" in output
    assert "Next steps:" in output
