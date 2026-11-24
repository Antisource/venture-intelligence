"""Synthetic demo dataset: counts, determinism, synthetic labelling and CLI safety."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from venture_intelligence.cli import main
from venture_intelligence.config import settings
from venture_intelligence.intelligence.service import IntelligenceService
from venture_intelligence.storage.db import Database

SEED_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "demo" / "seed.py"


def _seeder():
    name = "venture_intelligence_demo_seed"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, SEED_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _rows(path: Path, table: str, columns: str) -> list[tuple]:
    with sqlite3.connect(path) as connection:
        return connection.execute(f"SELECT {columns} FROM {table} ORDER BY 1").fetchall()


def test_default_dataset_has_enough_rows_for_every_view() -> None:
    counts = _seeder().build_dataset().counts()
    assert counts["programs"] == 3
    assert counts["companies"] == 12
    assert 12 <= counts["persons"] <= 24
    assert counts["rankings"] == counts["companies"] + counts["persons"]
    assert counts["evidence"] == counts["observations"] == counts["events"]
    assert counts["graph_edges"] == counts["companies"] + counts["persons"]
    assert counts["crm_records"] == counts["crm_events"] == 6
    assert counts["alerts"] == counts["recommendations"] == 5


def test_generation_is_deterministic_per_seed() -> None:
    seeder = _seeder()
    first, again, other = seeder.build_dataset(3), seeder.build_dataset(3), seeder.build_dataset(4)
    assert first.digest() == again.digest()
    assert first.digest() != other.digest()


def test_dataset_is_clearly_synthetic() -> None:
    dataset = _seeder().build_dataset()
    assert all(event.metadata["synthetic"] is True for event in dataset.events)
    assert all(".example.test" in event.source_url for event in dataset.events)
    assert all(company.website.endswith(".example.test") for company in dataset.companies)
    assert all(item.properties["synthetic"] is True for item in dataset.persons)
    assert all(item.provenance["synthetic"] is True for item in dataset.crm_events)
    assert all(item.properties["synthetic"] is True for item in dataset.graph_nodes)


def test_seed_writes_expected_counts_and_views_read_them(tmp_path: Path) -> None:
    path = tmp_path / "demo.db"
    summary = _seeder().seed_database(path, seed=5, companies=4)
    counts = summary["counts"]
    assert summary["synthetic"] is True and summary["database"] == str(path.resolve())

    for table, key in (
        ("companies", "companies"),
        ("persons", "persons"),
        ("events", "events"),
        ("evidence", "evidence"),
        ("rankings", "rankings"),
        ("graph_nodes", "graph_nodes"),
        ("graph_edges", "graph_edges"),
        ("crm_records", "crm_records"),
        ("crm_events", "crm_events"),
        ("alerts", "alerts"),
    ):
        assert len(_rows(path, table, "1")) == counts[key], table
    pending = _rows(path, "event_queue", "status")
    assert pending and {row[0] for row in pending} == {"done"}

    database = Database(str(path))
    rankings = IntelligenceService(database).rankings()
    assert len(rankings.items) == counts["rankings"]
    company = database.all_companies()[0]
    entity = IntelligenceService(database).entity(company.company_id)
    assert entity is not None and len(entity.evidence) == 2
    assert len(database.get_recent_alerts()) == counts["alerts"]
    assert sum(len(items) for items in database.get_crm_board().values()) == counts["crm_records"]
    assert database.get_node_neighbors(company.founder_ids[0])


def test_seeded_databases_are_identical_for_the_same_seed(tmp_path: Path) -> None:
    seeder = _seeder()
    first, second = tmp_path / "a.db", tmp_path / "b.db"
    assert seeder.seed_database(first, seed=9, companies=2)["digest"] == seeder.seed_database(
        second, seed=9, companies=2
    )["digest"]
    for table, columns in (
        ("companies", "company_id, canonical_name, website, batch, founder_ids"),
        ("persons", "person_id, canonical_name, current_company_id"),
        ("evidence", "evidence_id, entity_id, claim, observation_ids, observed_at"),
        ("rankings", "ranking_id, entity_id, rank, composite_score, dimensions"),
        ("graph_edges", "edge_id, source_id, target_id, edge_type"),
        ("alerts", "alert_id, entity_id, action, rank"),
    ):
        assert _rows(first, table, columns) == _rows(second, table, columns), table


def test_cli_demo_seed_writes_the_given_path(tmp_path: Path, capsys) -> None:
    path = tmp_path / "cli-demo.db"
    assert main(["--json", "demo", "seed", "--database", str(path), "--seed", "2"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "seeded" and summary["seed"] == 2
    assert summary["counts"]["companies"] == 12
    assert summary["digest"] == _seeder().build_dataset(2).digest()
    assert len(_rows(path, "companies", "1")) == summary["counts"]["companies"]


def test_cli_demo_seed_refuses_the_operational_database(capsys) -> None:
    code = main(["--json", "demo", "seed", "--database", settings.db_path])
    assert code == 2
    assert "operational database" in json.loads(capsys.readouterr().out)["error"]


def test_script_refuses_the_operational_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    operational = tmp_path / "intelligence.db"
    monkeypatch.setattr(settings, "db_path", str(operational))
    assert _seeder().main(["--database", str(operational)]) == 2
    assert "operational database" in capsys.readouterr().err
    assert not operational.exists()


def test_seed_refuses_an_existing_database_unless_overwrite(tmp_path: Path, capsys) -> None:
    path = tmp_path / "store.db"
    Database(str(path)).write_alert(_seeder().build_dataset(1, companies=1).alerts[0])
    before = _rows(path, "alerts", "alert_id")

    assert main(["--json", "demo", "seed", "--database", str(path)]) == 2
    assert "existing database" in json.loads(capsys.readouterr().out)["error"]
    assert _rows(path, "alerts", "alert_id") == before

    assert main(["--json", "demo", "seed", "--database", str(path), "--overwrite"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert len(_rows(path, "alerts", "1")) == summary["counts"]["alerts"]
    assert len(_rows(path, "companies", "1")) == 12


def test_seed_rejects_an_out_of_range_company_count(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="companies"):
        _seeder().seed_database(tmp_path / "x.db", companies=0)
    assert not (tmp_path / "x.db").exists()


def test_cli_demo_seed_requires_a_database() -> None:
    with pytest.raises(SystemExit) as failure:
        main(["demo", "seed"])
    assert failure.value.code == 2
