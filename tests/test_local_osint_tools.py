"""Regressions for bounded Maigret/Sherlock application integrations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from venture_intelligence.batch_discovery import tools


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["maigret", "sherlock"])
async def test_bounded_tool_normalizes_without_identity_promotion(
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
) -> None:
    monkeypatch.setattr(tools.shutil, "which", lambda _name: f"/{tool}")
    issued_command: list[str] = []

    async def execute(
        command: list[str],
        *,
        directory: Path,
        timeout_seconds: float,
    ) -> tuple[int, str, str, None]:
        assert timeout_seconds <= 45
        issued_command.extend(command)
        if tool == "maigret":
            (directory / "report_torvalds_simple.json").write_text(
                json.dumps(
                    {
                        "GitHub": {
                            "url_user": "https://github.com/torvalds",
                            "status": {"status": "Claimed"},
                        }
                    }
                ),
                encoding="utf-8",
            )
        else:
            (directory / "torvalds.csv").write_text(
                "username,name,url_main,url_user,exists,http_status,response_time_s\n"
                "torvalds,GitHub,https://github.com,https://github.com/torvalds,"
                "QueryStatus.CLAIMED,200,0.1\n",
                encoding="utf-8",
            )
        return 0, "", "", None

    monkeypatch.setattr(tools, "_execute_bounded_tool", execute)
    monkeypatch.setenv("VENTURE_INTELLIGENCE_LIVE_TESTS", "1")
    run = await tools.run_local_osint_tool(
        tool,
        username="torvalds",
        allow_network=True,
    )

    assert run.execution_status == "LIVE_VALIDATED"
    if tool == "maigret":
        resolver_index = issued_command.index("--dns-resolver")
        assert issued_command[resolver_index + 1] in {"async", "threaded"}
    assert run.accepted == 1
    assert run.ambiguous == 1
    assert run.identity_effect.startswith("none")
    assert run.results[0].metadata["identity_confidence"] == 0.0
    assert run.results[0].metadata["promotion_eligible"] is False
    assert run.results[0].provenance["tool_network_execution"] is True


@pytest.mark.asyncio
async def test_tool_policy_rejects_private_and_non_https_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tools.shutil, "which", lambda _name: "/maigret")

    async def execute(
        _command: list[str],
        *,
        directory: Path,
        timeout_seconds: float,
    ) -> tuple[int, str, str, None]:
        del timeout_seconds
        (directory / "report_alice_simple.json").write_text(
            json.dumps(
                {
                    "Private": {
                        "url_user": "https://127.0.0.1/alice",
                        "status": {"status": "Claimed"},
                    },
                    "Plain": {
                        "url_user": "http://example.com/alice",
                        "status": {"status": "Claimed"},
                    },
                }
            ),
            encoding="utf-8",
        )
        return 0, "", "", None

    monkeypatch.setattr(tools, "_execute_bounded_tool", execute)
    monkeypatch.setenv("VENTURE_INTELLIGENCE_LIVE_TESTS", "1")
    run = await tools.run_local_osint_tool(
        "maigret",
        username="alice",
        allow_network=True,
    )

    assert run.execution_status == "HEALTHY_EMPTY"
    assert run.accepted == 0
    assert run.rejected == 2


@pytest.mark.parametrize("second_url", ["https://github.com/torvalds", "https://www.github.com/torvalds/"])
def test_cross_tool_urls_are_one_origin_and_never_identity_proof(second_url: str) -> None:
    first = tools.LocalOsintToolRun(
        tool="Maigret",
        installed=True,
        enabled=True,
        execution_status="LIVE_VALIDATED",
        results=tools.ManualUsernameExportAdapter().parse(
            "maigret",
            {"GitHub": {"url": second_url, "status": "claimed"}},
            username="torvalds",
        ),
    )
    second = tools.LocalOsintToolRun(
        tool="Sherlock",
        installed=True,
        enabled=True,
        execution_status="LIVE_VALIDATED",
        results=tools.ManualUsernameExportAdapter().parse(
            "sherlock",
            {"GitHub": {"url": "https://github.com/torvalds", "status": "claimed"}},
            username="torvalds",
        ),
    )

    clues = tools.deduplicate_public_profile_clues(
        [first, second],
        username="torvalds",
    )

    assert len(clues) == 1
    assert clues[0].acquisition_tools == ["Maigret", "Sherlock"]
    assert clues[0].provenance["independent_evidence_origins"] == 1
    assert clues[0].identity_effect == "none"


def test_profile_origin_preserves_unverified_host_and_case_distinctions() -> None:
    assert tools.public_profile_origin("https://www.example.com/Alice") != tools.public_profile_origin("https://example.com/Alice")
    assert tools.public_profile_origin("https://example.com/Alice") != tools.public_profile_origin("https://example.com/alice")
    results = tools.ManualUsernameExportAdapter().parse(
        "sherlock",
        {"GitHub": {"url": "https://www.github.com/torvalds", "status": "claimed"}},
        username="torvalds",
    )
    accepted, rejected, _ = tools._policy_filter_results(results)
    assert rejected == 0
    assert accepted[0].url == "https://github.com/torvalds"
    assert accepted[0].provenance["acquisition_url"] == "https://www.github.com/torvalds"


def test_subprocess_environment_never_forwards_application_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    environment = tools._safe_tool_environment()
    assert "GITHUB_TOKEN" not in environment
    assert "OPENAI_API_KEY" not in environment


@pytest.mark.asyncio
async def test_invalid_username_is_rejected_before_subprocess() -> None:
    with pytest.raises(ValueError, match="simple public identifier"):
        await tools.run_local_osint_tool("maigret", username="alice; --all-sites")


@pytest.mark.asyncio
async def test_known_positive_empty_is_failed_not_reported_as_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VENTURE_INTELLIGENCE_LIVE_TESTS", "1")
    monkeypatch.setattr(tools.shutil, "which", lambda _name: "/maigret")

    async def execute(
        _command: list[str],
        *,
        directory: Path,
        timeout_seconds: float,
    ) -> tuple[int, str, str, None]:
        del timeout_seconds
        (directory / "report_torvalds_simple.json").write_text("{}", encoding="utf-8")
        return 0, "", "", None

    monkeypatch.setattr(tools, "_execute_bounded_tool", execute)
    run = await tools.run_local_osint_tool(
        "maigret",
        username="torvalds",
        allow_network=True,
        known_positive_expected=True,
    )
    assert run.execution_status == "DEGRADED_EXTERNAL_TOOL"
    assert "known-positive" in (run.failure_reason or "")
