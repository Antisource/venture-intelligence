"""Shared API state and request helpers used by the domain routers."""

from __future__ import annotations

from fastapi import HTTPException, Request

from venture_intelligence.crm.workflow import CRMWorkflow
from venture_intelligence.storage.db import Database

_db: Database | None = None
_crm: CRMWorkflow | None = None


def _get_db() -> Database:
    if _db is None:
        raise HTTPException(503, "Database not initialised")
    return _db


def _get_crm() -> CRMWorkflow:
    if _crm is None:
        raise HTTPException(503, "CRM not initialised")
    return _crm


def _accepts_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "").casefold()
