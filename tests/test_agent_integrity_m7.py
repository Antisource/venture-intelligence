from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from venture_intelligence.agent.loop import AgentLoop
from venture_intelligence.shared.models import (
    CanonicalEvent,
    EntityType,
    EventType,
    Source,
)
from venture_intelligence.storage.cleanup import reconcile_polluted_entities
from venture_intelligence.storage.db import Database

OBSERVED = datetime(2025, 1, 1, tzinfo=timezone.utc)


class _RejectingBus:
    def publish(self, _event: CanonicalEvent) -> None:
        raise AssertionError("an orphaned generic identity reached EventBus")


class _RejectingCoordinator:
    async def route(self, _event: CanonicalEvent) -> int:
        raise AssertionError("an orphaned generic identity reached connectors")


def _orphaned_pollution(database: Database, entity_id: str = "yc__twitter") -> None:
    database.write_event(
        CanonicalEvent(
            event_id="immutable-generic-input",
            event_type=EventType.COMPANY_LISTED,
            entity_type=EntityType.COMPANY,
            entity_id=entity_id,
            source=Source.CRM,
            source_url="agent://legacy-investigation",
            occurred_at=OBSERVED,
            observed_at=OBSERVED,
            ingested_at=OBSERVED,
            confidence=0.2,
            raw_payload={"name": "Twitter"},
        )
    )
    with database._connect() as connection:
        connection.execute(
            """
            INSERT INTO rankings
              (ranking_id, entity_id, rank, composite_score, dimensions,
               hypothesis_ids, evidence_ids, signal_ids, feature_ids,
               crm_action_ids, ranking_version, input_digest, reasoning,
               confidence, ranked_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "orphaned-ranking",
                entity_id,
                1,
                0.95,
                "[]",
                "[]",
                "[]",
                "[]",
                "[]",
                "[]",
                "legacy-1",
                "orphaned",
                "legacy pollution",
                0.4,
                OBSERVED.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO crm_records
              (crm_id, entity_id, stage, notes, decision, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                "orphaned-crm",
                entity_id,
                "discovery",
                "[]",
                None,
                OBSERVED.isoformat(),
                OBSERVED.isoformat(),
            ),
        )


@pytest.mark.asyncio
async def test_agent_and_reconciliation_reject_orphaned_polluted_derived_state(
    tmp_path: Path,
) -> None:
    database = Database(str(tmp_path / "agent-integrity.db"))
    _orphaned_pollution(database)

    report = reconcile_polluted_entities(database)
    assert report["canonical_entities"] == []
    assert report["orphaned_derived_entities"] == [
        {
            "entity_id": "yc__twitter",
            "reason": "generic_label",
            "tables": ["crm_records", "rankings"],
        }
    ]
    loop = AgentLoop(database, _RejectingBus(), _RejectingCoordinator())  # type: ignore[arg-type]
    assert await loop._refresh_stale() == 0

    applied = reconcile_polluted_entities(database, apply=True)
    assert applied["derived_rows"]["rankings"] == 1
    assert applied["derived_rows"]["crm_records"] == 1
    with database._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM rankings").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM crm_records").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM quarantined_entities WHERE entity_id='yc__twitter'"
            ).fetchone()[0]
            == 1
        )
    assert reconcile_polluted_entities(database)["orphaned_derived_entities"] == []
