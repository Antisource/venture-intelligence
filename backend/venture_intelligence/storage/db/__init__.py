"""
storage/db.py — SQLite database layer.

Responsibilities:
  1. DDL — create all tables on first run, migrate schema if needed
  2. Write operations — every domain object has one write method
  3. Read operations — everything the pipeline workers need to query

Design principles:
  • Events table is append-only — no UPDATE or DELETE ever
  • All timestamps stored as ISO-8601 UTC strings
  • JSON columns store Pydantic .model_dump_json() output
  • Every foreign-key relationship is enforced by Python, not SQLite
    constraints (kept simple for V1 SQLite — add FK pragma in V2)

§5 note: This is the V1 local-first database.  The interface is designed
to be replaced by PostgreSQL + asyncpg in V2 without touching callers.
"""

from venture_intelligence.storage.db._base import DatabaseBase
from venture_intelligence.storage.db.alerts import AlertsMixin
from venture_intelligence.storage.db.crm import CRMMixin
from venture_intelligence.storage.db.entities import EntitiesMixin
from venture_intelligence.storage.db.evaluation import EvaluationMixin
from venture_intelligence.storage.db.events import EventsMixin
from venture_intelligence.storage.db.evidence import EvidenceMixin
from venture_intelligence.storage.db.features import FeaturesMixin
from venture_intelligence.storage.db.graph import GraphMixin
from venture_intelligence.storage.db.investigations import InvestigationsMixin
from venture_intelligence.storage.db.rankings import RankingsMixin
from venture_intelligence.storage.db.schema import SchemaMixin


class Database(
    SchemaMixin,
    EventsMixin,
    EntitiesMixin,
    GraphMixin,
    EvidenceMixin,
    RankingsMixin,
    AlertsMixin,
    CRMMixin,
    EvaluationMixin,
    FeaturesMixin,
    InvestigationsMixin,
    DatabaseBase,
):
    """
    Thin wrapper around sqlite3.

    Usage pattern:
        db = Database("data/intelligence.db")
        db.write_event(event)
        events = db.unprocessed_events("validator")
    """


__all__ = ["Database"]
