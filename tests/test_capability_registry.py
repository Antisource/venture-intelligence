"""Capability registry validation, lens views and the /api/v1/capabilities endpoint."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError
from venture_intelligence.api.app import app
from venture_intelligence.capabilities import (
    CapabilityStatus,
    Lens,
    RegistryConsistencyError,
    capabilities_for,
    get_capability_registry,
    help_hints,
    load_registry,
    validate_registry,
)
from venture_intelligence.capabilities.registry import api_routes, parse_registry
from venture_intelligence.cli import _parser


def _entry(**overrides: object) -> dict:
    entry = {
        "id": "programs",
        "title": "Programs",
        "blurb": "See tracked programs.",
        "default_lens": "L1",
        "teaser": "Browse programs.",
        "cli": "programs list",
        "api": {"method": "GET", "route": "/api/v1/programs"},
        "people_data": False,
        "status": "available",
    }
    entry.update(overrides)
    return entry


def _document(*entries: dict) -> dict:
    return {"version": 1, "capabilities": [copy.deepcopy(item) for item in entries]}


def _people(**overrides: object) -> dict:
    values = {
        "id": "rankings",
        "default_lens": "L2",
        "cli": "rankings list",
        "api": {"method": "GET", "route": "/api/v1/intelligence/rankings"},
        "people_data": True,
    }
    values.update(overrides)
    return _entry(**values)


def _planned(**overrides: object) -> dict:
    values = {
        "id": "thesis",
        "default_lens": "L3",
        "cli": None,
        "api": None,
        "status": "planned",
        "slice": "S1",
    }
    values.update(overrides)
    return _entry(**values)


def test_shipped_registry_matches_the_api_and_cli() -> None:
    registry = load_registry()
    validate_registry(registry, routes=api_routes(app), parser=_parser())
    available = registry.available()
    assert available and all(item.slice is None for item in available)
    planned = [item for item in registry.capabilities if item.status is CapabilityStatus.PLANNED]
    assert {item.id for item in planned} >= {
        "thesis",
        "mandate_shortlist",
        "founder_report_card",
        "identity_resolution",
        "delivery_gateway",
        "warm_intros",
        "mcp",
    }
    assert all(item.slice in {"S1", "S2", "S3", "S4", "S5"} for item in planned)


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate capability ids: programs"):
        parse_registry(_document(_entry(), _entry(title="Programs again")))


def test_people_data_can_never_default_to_l1() -> None:
    with pytest.raises(ValidationError, match="cannot default to L1"):
        parse_registry(_document(_people(default_lens="L1")))


@pytest.mark.parametrize(
    "entry, message",
    [
        (_planned(slice=None), "must name its slice"),
        (_planned(slice="S9"), "slice"),
        (_planned(cli="thesis show"), "must not advertise"),
        (_entry(slice="S1"), "must not name a slice"),
        (_entry(default_lens="L7"), "default_lens"),
        (_entry(status="someday"), "status"),
    ],
)
def test_structural_rules_are_enforced(entry: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        parse_registry(_document(entry))


def test_available_entry_with_unknown_route_is_rejected() -> None:
    registry = parse_registry(
        _document(_entry(api={"method": "GET", "route": "/api/v1/not-a-route"}))
    )
    with pytest.raises(RegistryConsistencyError, match="/api/v1/not-a-route does not exist"):
        validate_registry(registry, routes=api_routes(app), parser=_parser())


def test_available_entry_with_wrong_method_is_rejected() -> None:
    registry = parse_registry(
        _document(_entry(api={"method": "POST", "route": "/api/v1/programs"}))
    )
    with pytest.raises(RegistryConsistencyError, match="POST /api/v1/programs"):
        validate_registry(registry, routes=api_routes(app))


@pytest.mark.parametrize(
    "command, message",
    [
        ("teleport now", "unknown command 'teleport'"),
        ("programs", "needs a sub-command"),
        ("rankings list --colour red", "unknown option '--colour'"),
        ("inbox <one> <two>", "more arguments"),
        ("programs list extra", "unexpected token"),
    ],
)
def test_available_entry_with_unknown_cli_command_is_rejected(command: str, message: str) -> None:
    registry = parse_registry(_document(_entry(cli=command, api=None)))
    with pytest.raises(RegistryConsistencyError, match=message):
        validate_registry(registry, parser=_parser())


def test_planned_entries_are_not_checked_against_interfaces() -> None:
    registry = parse_registry(_document(_planned()))
    empty = argparse.ArgumentParser()
    assert validate_registry(registry, routes=set(), parser=empty) is registry


def test_registry_file_must_have_a_capabilities_list(tmp_path: Path) -> None:
    path = tmp_path / "capabilities.yaml"
    path.write_text(yaml.safe_dump({"version": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="top-level 'capabilities' list"):
        load_registry(path)


@pytest.mark.parametrize("lens", list(Lens))
def test_lens_view_partitions_the_registry(lens: Lens) -> None:
    registry = get_capability_registry()
    view = capabilities_for(lens)
    visible = {item.id for item in view.capabilities}
    teased = {item.id for item in view.teasers}
    assert visible.isdisjoint(teased)
    assert visible | teased == {item.id for item in registry.capabilities}
    assert all(item.default_lens.rank <= lens.rank for item in view.capabilities)
    assert all(item.unlock_lens.rank > lens.rank for item in view.teasers)


def test_l1_never_includes_people_level_capabilities() -> None:
    view = capabilities_for(Lens.L1)
    assert view.capabilities
    assert not [item.id for item in view.capabilities if item.people_data]
    people = {item.id for item in get_capability_registry().capabilities if item.people_data}
    assert people <= {item.id for item in view.teasers}


def test_teasers_do_not_expose_commands_or_routes() -> None:
    teaser = capabilities_for(Lens.L1).teasers[0].model_dump()
    assert "cli" not in teaser and "api" not in teaser and "blurb" not in teaser


def test_l1_filter_holds_even_for_a_registry_built_without_validation() -> None:
    registry = get_capability_registry()
    leaked = registry.get("rankings").model_copy(update={"default_lens": Lens.L1})
    forged = registry.model_copy(update={"capabilities": [leaked]})
    view = capabilities_for(Lens.L1, forged)
    assert view.capabilities == []
    assert [item.id for item in view.teasers] == ["rankings"]


def test_l4_sees_everything() -> None:
    view = capabilities_for("L4")
    assert view.teasers == []
    assert len(view.capabilities) == len(get_capability_registry().capabilities)


def test_help_hints_only_name_available_cli_commands() -> None:
    hints = help_hints(Lens.L2)
    assert "vi inbox  # Review new leads, material changes and alerts that need attention." in hints
    assert not any("thesis" in hint for hint in hints)
    assert not any(hint.startswith("vi doctor") for hint in hints)  # L4 only


def test_api_returns_the_requested_lens_view() -> None:
    with TestClient(app) as client:
        explorer = client.get("/api/v1/capabilities", params={"lens": "L1"})
        default = client.get("/api/v1/capabilities")
        partner = client.get("/api/v1/capabilities", params={"lens": "L3"})
        invalid = client.get("/api/v1/capabilities", params={"lens": "L9"})
        schema = client.get("/openapi.json").json()

    assert explorer.status_code == 200
    body = explorer.json()
    assert body["lens"] == "L1" and body["lens_title"] == "Explorer"
    assert body["capabilities"] and not [i for i in body["capabilities"] if i["people_data"]]
    assert default.json() == body
    assert {item["id"] for item in partner.json()["capabilities"]} >= {"crm_pipeline", "rankings"}
    assert invalid.status_code == 422
    assert "/api/v1/capabilities" in schema["paths"]
