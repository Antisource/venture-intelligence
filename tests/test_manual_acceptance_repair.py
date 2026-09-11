"""Focused regressions for configuration truthfulness at product boundaries."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from pydantic import SecretStr
from venture_intelligence.api.routers import v1
from venture_intelligence.config import settings


def test_product_hunt_configured_failure_stays_configured_and_unavailable(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "product_hunt_api_enabled", True)
    monkeypatch.setattr(settings, "product_hunt_access_token", SecretStr("secret"))
    monkeypatch.setattr(settings, "source_settings_path", str(tmp_path / "sources.json"))
    monkeypatch.setattr(settings, "source_state_path", str(tmp_path / "state.json"))
    monkeypatch.setattr(settings, "intelligence_watchlist_path", str(tmp_path / "watchlist.json"))
    monkeypatch.setattr(settings, "source_quality_report_path", str(tmp_path / "quality.json"))
    (tmp_path / "watchlist.json").write_text(json.dumps({"sources": []}), encoding="utf-8")
    diagnostic = SimpleNamespace(
        source_id="product_hunt:known-positive",
        known_positive_control=True,
        state=SimpleNamespace(value="unavailable"),
        error="provider unavailable",
    )
    monkeypatch.setattr(
        v1.SourceQualityReportStore,
        "load",
        lambda _self: SimpleNamespace(
            source_diagnostics=[diagnostic],
            dedicated_osint_tools=[],
            finished_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
        ),
    )
    monkeypatch.setattr(v1, "build_connector_inventory", lambda *_args: [])

    response = asyncio.run(v1.list_source_policies())
    runtime = next(item for item in response.runtime if item.source_id == "product_hunt")

    assert runtime.enabled is True
    assert runtime.configuration_status == "configured"
    assert runtime.credential_present is True
    assert runtime.health == "unavailable"


def test_product_hunt_missing_credential_is_not_configured(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "product_hunt_api_enabled", True)
    monkeypatch.setattr(settings, "product_hunt_access_token", None)
    monkeypatch.setattr(settings, "source_settings_path", str(tmp_path / "sources.json"))
    monkeypatch.setattr(settings, "source_state_path", str(tmp_path / "state.json"))
    monkeypatch.setattr(settings, "intelligence_watchlist_path", str(tmp_path / "watchlist.json"))
    monkeypatch.setattr(settings, "source_quality_report_path", str(tmp_path / "quality.json"))
    (tmp_path / "watchlist.json").write_text(json.dumps({"sources": []}), encoding="utf-8")
    monkeypatch.setattr(v1.SourceQualityReportStore, "load", lambda _self: None)

    response = asyncio.run(v1.list_source_policies())
    runtime = next(item for item in response.runtime if item.source_id == "product_hunt")

    assert runtime.configuration_status == "not_configured"
    assert runtime.credential_present is False
