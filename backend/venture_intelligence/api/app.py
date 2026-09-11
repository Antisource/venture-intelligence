"""FastAPI application and legacy dashboard-compatible routes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from venture_intelligence.agents.analyst import AnalystAgent
from venture_intelligence.api.routers.batch_discovery import router as batch_discovery_router
from venture_intelligence.api.routers.v1 import list_programs
from venture_intelligence.api.routers.v1 import router as v1_router
from venture_intelligence.batch_discovery.repository import BatchDiscoveryRepository
from venture_intelligence.config import settings
from venture_intelligence.crm.workflow import CRMWorkflow
from venture_intelligence.knowledge.compiler import KnowledgeCompiler
from venture_intelligence.knowledge.evidence import normalize_evidence, summarize_evidence
from venture_intelligence.knowledge.models import KnowledgePage
from venture_intelligence.operations.cycle import OperationsStateStore
from venture_intelligence.operations.readiness import EmailRuntimeStatus
from venture_intelligence.pipeline.email import EmailNotificationService
from venture_intelligence.pipeline.entity_integrity import is_generic_label, validated_entity_record
from venture_intelligence.programs import get_program_registry
from venture_intelligence.shared.models import CRMStage
from venture_intelligence.storage.db import Database

logger = logging.getLogger(__name__)
_db: Database | None = None
_crm: CRMWorkflow | None = None
_frontend_root = Path(__file__).resolve().parents[3] / "frontend"
_frontend_dist = _frontend_root / "dist" / "index.html"
_SSE_MAX_FRAMES = 12
_SSE_MAX_FRAME_BYTES = 256_000
_WS_MAX_MESSAGES = 120
_WS_MAX_MESSAGE_CHARS = 512
_WS_IDLE_TIMEOUT_SECONDS = 300


def _sse_json_frame(event: str, payload: dict, *, event_id: str | None = None) -> str:
    """Serialize one bounded SSE frame; never stream an oversized JSON page."""
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    prefix = f"id: {event_id}\n" if event_id else ""
    frame = f"{prefix}event: {event}\ndata: {data}\n\n"
    if len(frame.encode("utf-8")) <= _SSE_MAX_FRAME_BYTES:
        return frame
    fallback = json.dumps(
        {"type": "stream.error", "error": "sse_frame_too_large"},
        separators=(",", ":"),
    )
    return f"event: stream.error\ndata: {fallback}\n\n"


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _db, _crm
    _db = Database(settings.db_path)
    _crm = CRMWorkflow(_db)
    logger.info("venture-intelligence API started db=%s", settings.db_path)
    yield
    logger.info("venture-intelligence API stopped")


app = FastAPI(
    title="Venture Intelligence",
    description="Configuration-driven, evidence-oriented venture intelligence.",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure a narrow origin list before remote deployment.
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["*"],
)
app.include_router(v1_router)
app.include_router(batch_discovery_router)
app.mount("/frontend", StaticFiles(directory=_frontend_root), name="frontend")


def _get_db() -> Database:
    if _db is None:
        raise HTTPException(503, "Database not initialised")
    return _db


def _get_crm() -> CRMWorkflow:
    if _crm is None:
        raise HTTPException(503, "CRM not initialised")
    return _crm


class AdvanceRequest(BaseModel):
    to_stage: str
    partner: str | None = None
    note: str | None = None


class NoteRequest(BaseModel):
    note: str
    partner: str | None = None


class AnalystRequest(BaseModel):
    question: str
    context_name: str | None = Field(default=None, max_length=200)


class EmailSettingsRequest(BaseModel):
    destination: str | None = None
    enabled: bool = False
    preferences: dict[str, object] = Field(default_factory=dict)
    frequency: str = "immediate"
    minimum_confidence: float = Field(default=0.65, ge=0, le=1)


class EmailSettingsResponse(BaseModel):
    destination: str | None = None
    preferences: dict[str, object]
    deliveries: list[dict[str, object]] = Field(default_factory=list)
    runtime: EmailRuntimeStatus


class EmailDigestRequest(BaseModel):
    send: bool = False


class HealthResponse(BaseModel):
    status: str
    project: str
    db: str


@app.get("/api/events")
async def events(limit: int = 50) -> dict:
    """Return recent canonical events for legacy dashboard consumers."""
    db = _get_db()
    bounded = max(1, min(limit, 100))
    with db._connect() as connection:
        rows = connection.execute(
            "SELECT * FROM events ORDER BY ingested_at DESC LIMIT ?", (bounded,)
        ).fetchall()
    return {"events": [db._row_to_event(row).model_dump(mode="json") for row in rows]}


@app.get("/api/evaluation")
async def evaluation() -> dict:
    """Return persisted evaluation results for the dashboard."""
    return {"evaluations": _get_db().get_evaluations()}


@app.post("/api/analyst/chat")
async def analyst_chat(request: AnalystRequest) -> dict:
    """Bounded analyst boundary; cloud reasoning remains opt-in and separate."""
    question = request.question.strip()
    if not question or len(question) > 2_000:
        raise HTTPException(422, "question must contain 1-2000 characters")
    context_name = (request.context_name or "").strip()
    if context_name and _is_cross_entity_question(question):
        return {
            "question": question,
            "answer": (
                f"This assistant is scoped to {context_name}. "
                "Use Discover or command search for cross-company questions."
            ),
            "model": "deterministic-scope-boundary",
            "evidence_sufficient": False,
            "epistemic_state": "unavailable",
            "caveats": ["No global answer was generated from entity-local evidence."],
            "citations": [],
        }
    result = await AnalystAgent().answer(
        question[:2_000],
        context=_grounded_analyst_context(
            f"{context_name} {question}" if context_name else question
        ),
    )
    return {"question": question, **result.model_dump(mode="json")}


def _is_cross_entity_question(question: str) -> bool:
    normalized = " ".join(question.casefold().split())
    return bool(
        re.search(
            r"\b(?:list|show|give me|compare)\b.{0,80}"
            r"\b(?:company|companies|founder|founders|startup|startups|entity|entities)\b",
            normalized,
        )
        or re.search(
            r"\b(?:all|every|which)\b.{0,80}"
            r"\b(?:company|companies|founder|founders|startup|startups|entity|entities)\b",
            normalized,
        )
    )


def _grounded_analyst_context(question: str) -> dict:
    database = _get_db()
    from venture_intelligence.intelligence.service import IntelligenceService

    entities = IntelligenceService(database).analyst_entities(question)
    repository = BatchDiscoveryRepository(database)
    candidates: list[dict] = []
    citations: list[dict[str, str]] = [
        {"label": item["claim"], "url": url}
        for entity in entities for item in entity["evidence"] for url in item["source_urls"]
    ]
    terms = {token for token in question.casefold().replace("?", " ").split() if len(token) > 2}
    for program in get_program_registry().active():
        for candidate in repository.latest_hypotheses(
            program.id,
            program.batch,
            include_rejected=True,
        ):
            searchable = " ".join(
                [
                    candidate.display_name,
                    candidate.candidate_name or "",
                    candidate.company_name or "",
                    *candidate.aliases,
                    *candidate.usernames,
                ]
            ).casefold()
            if terms and not any(term in searchable for term in terms):
                continue
            observations = repository.observations_by_ids(candidate.observation_ids)
            sources = [
                {
                    "label": item.source_domain,
                    "url": item.canonical_url,
                    "evidence_id": item.observation_id,
                    "stance": item.stance.value,
                }
                for item in observations[:8]
            ]
            citations.extend({"label": item["label"], "url": item["url"]} for item in sources)
            candidates.append(
                {
                    "hypothesis_id": candidate.hypothesis_id,
                    "display_name": candidate.display_name,
                    "confidence_pct": candidate.assessment.confidence_pct,
                    "identity_confidence_pct": (candidate.assessment.identity_confidence_pct),
                    "state": candidate.state.value,
                    "epistemic_state": (
                        "known"
                        if candidate.state.value == "confirmed"
                        else "contradicted"
                        if candidate.assessment.contradictory_observations
                        and not candidate.assessment.batch_evidence_present
                        else "unavailable"
                        if candidate.assessment.source_coverage.checked_families == 0
                        and candidate.assessment.source_coverage.unavailable_families > 0
                        else "hypothesized"
                    ),
                    "reason": (
                        candidate.assessment.components[0].explanation
                        if candidate.assessment.components
                        else "Insufficient evidence — candidate not promoted"
                    ),
                    "contradictions": candidate.assessment.contradictory_observations,
                    "promotion_blockers": candidate.assessment.promotion_blockers,
                    "why_now": candidate.why_now,
                    "next_best_checks": candidate.assessment.next_best_checks,
                    "source_coverage": candidate.assessment.source_coverage.model_dump(
                        mode="json"
                    ),
                    "investment_attention_pct": (
                        candidate.profile.investment_attention_pct
                    ),
                    "sources": sources,
                }
            )
            if len(candidates) >= 8:
                break
    operations = OperationsStateStore(settings.operations_state_path).load()
    raw_changes = [
        change.model_dump(mode="json")
        for cycle in operations.cycles[:3]
        for result in cycle.program_results
        for change in result.changes
    ]
    changes_by_event: dict[tuple[str, tuple[str, ...], tuple[str, ...]], dict] = {}
    for change in raw_changes:
        key = (
            str(change["hypothesis_id"]),
            tuple(sorted(change.get("causal_observation_ids", []))),
            tuple(sorted(change.get("change_types", []))),
        )
        changes_by_event.setdefault(key, change)
    changes = list(changes_by_event.values())[:8]
    unavailable_statuses = {
        "blocked",
        "circuit_open",
        "failed",
        "network_error",
        "rate_limited",
        "timeout",
        "unavailable",
    }
    important_sources_unavailable = any(
        str(skipped.get("status", "")).casefold() in unavailable_statuses
        for cycle in operations.cycles[:1]
        for result in cycle.program_results
        for skipped in result.providers_skipped
    )
    return {
        "source": "canonical_database",
        "entities": entities,
        "question": question,
        "candidates": candidates,
        "changes": changes,
        "citations": citations[:16],
        "important_sources_unavailable": important_sources_unavailable,
    }


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    if not _frontend_dist.is_file():
        raise HTTPException(
            503,
            "React production build is unavailable; run `pnpm build`. "
            "Use `pnpm dev` for frontend development.",
        )
    return FileResponse(_frontend_dist)


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        project="venture-intelligence",
        db=settings.db_path,
    )


@app.get("/api/stats")
async def stats() -> dict:
    db = _get_db()
    with db._connect() as connection:
        return {
            "total_events": connection.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "total_companies": connection.execute("SELECT COUNT(*) FROM companies").fetchone()[0],
            "total_persons": connection.execute("SELECT COUNT(*) FROM persons").fetchone()[0],
            "total_signals": connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0],
            "total_alerts": connection.execute("SELECT COUNT(*) FROM alerts").fetchone()[0],
        }


@app.get("/api/founders")
async def list_founders(request: Request, limit: int = 50, offset: int = 0):
    if request.url.path == "/founders" and _accepts_html(request):
        return RedirectResponse("/#/founders", status_code=307)
    return _list_founders_payload(limit, offset)


def _list_founders_payload(limit: int, offset: int) -> dict:
    db = _get_db()
    rankings = db.get_rankings(limit=max(1, min(limit, 100)) + max(offset, 0))[offset:]
    results = []
    for ranking in rankings:
        entity = db.get_company(ranking.entity_id) or db.get_person(ranking.entity_id)
        if (
            not entity
            or not db.is_entity_validated(ranking.entity_id)
            or not validated_entity_record(entity)
        ):
            continue
        results.append(
            {
                "entity_id": ranking.entity_id,
                "name": entity.canonical_name if entity else ranking.entity_id,
                "rank": ranking.rank,
                "composite_score": ranking.composite_score,
                "confidence": ranking.confidence,
                "reasoning": ranking.reasoning,
            }
        )
    return {"total": len(results), "results": results}


@app.get("/api/review-queue")
async def review_queue(limit: int = 50, offset: int = 0) -> dict:
    """Explicit integrity-gated alias for the ranked review queue."""
    return _list_founders_payload(limit, offset)


@app.get("/api/founders/{entity_id}")
async def founder_detail(request: Request, entity_id: str):
    if request.url.path.startswith("/founders/") and _accepts_html(request):
        return RedirectResponse(f"/#/founders/{entity_id}", status_code=307)
    db = _get_db()
    entity = db.get_company(entity_id) or db.get_person(entity_id)
    if not entity or not db.is_entity_validated(entity_id) or not validated_entity_record(entity):
        raise HTTPException(404, f"Entity {entity_id!r} not found")
    ranking = db.get_ranking_for_entity(entity_id)
    return {
        "entity": entity.model_dump(mode="json"),
        "ranking": ranking.model_dump(mode="json") if ranking else None,
        "signals": [item.model_dump(mode="json") for item in db.get_signals_for_entity(entity_id)],
        "evidence": [
            item.model_dump(mode="json") for item in db.get_evidence_for_entity(entity_id)
        ],
        "events": [item.model_dump(mode="json") for item in db.get_events_for_entity(entity_id)],
    }


@app.get("/api/alerts")
async def alerts(limit: int = 50) -> dict:
    return {"alerts": _get_db().get_recent_alerts(limit=max(1, min(limit, 100)))}


@app.get("/api/crm")
async def crm_board() -> dict:
    db = _get_db()
    board = _get_crm().get_board()
    return {
        stage: [
            _crm_record_view(record, db)
            for record in records
            if _is_valid_canonical_entity(record.get("entity_id"))
        ]
        for stage, records in board.items()
    }


@app.post("/api/crm/{entity_id}/advance")
async def advance_crm(entity_id: str, request: AdvanceRequest) -> dict:
    if not _is_valid_canonical_entity(entity_id):
        raise HTTPException(404, "Validated canonical entity not found")
    try:
        record = _get_crm().advance_stage(
            entity_id, CRMStage(request.to_stage), request.partner, request.note
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"status": "ok", "stage": record.stage, "entity_id": entity_id}


@app.post("/api/crm/{entity_id}/note")
async def add_note(entity_id: str, request: NoteRequest) -> dict:
    if not _is_valid_canonical_entity(entity_id):
        raise HTTPException(404, "Validated canonical entity not found")
    record = _get_crm().add_note(entity_id, request.note, request.partner)
    return {"status": "ok", "notes": record.notes}


@app.get("/api/programs")
async def legacy_programs() -> dict:
    """Unversioned alias retained for the dashboard migration."""
    return await list_programs()


@app.get("/api/metrics")
async def metrics() -> dict:
    from venture_intelligence.observability.metrics import metrics as collector

    return collector.snapshot()


@app.get("/api/knowledge/validate")
async def knowledge_validate() -> dict:
    """Validate canonical JSON pages and their claim/evidence references."""
    return dict(KnowledgeCompiler(settings.knowledge_dir).validate())


@app.get("/api/knowledge/stream/{entity_id}")
@app.get("/api/v1/knowledge/stream/{entity_id}")
async def knowledge_stream(entity_id: str) -> StreamingResponse:
    """Bounded SSE stream of JSON wiki revisions.

    The stream sends an initial snapshot, then at most twelve revision/heartbeat
    frames.  Clients must reconnect or use the ordinary JSON page endpoint for
    long-lived polling; this prevents idle connections from becoming an
    unbounded resource.
    """
    compiler = KnowledgeCompiler(settings.knowledge_dir)

    async def frames():
        last_etag: str | None = None
        for _ in range(_SSE_MAX_FRAMES):
            page = compiler.find(entity_id)
            metadata = compiler.polling_metadata(page)
            if page is None:
                payload = {
                    "type": "knowledge.unavailable",
                    "entity_id": entity_id,
                    "metadata": metadata,
                }
                yield _sse_json_frame("knowledge.unavailable", payload)
                return
            if metadata["etag"] != last_etag:
                last_etag = metadata["etag"]
                payload = {
                    "type": "knowledge.updated",
                    "entity_id": entity_id,
                    "revision": metadata,
                    "page": page.model_dump(mode="json"),
                }
                yield _sse_json_frame(
                    "knowledge.updated", payload, event_id=str(metadata["revision_id"])
                )
            else:
                yield ": heartbeat\n\n"
            await asyncio.sleep(min(5, max(1, int(metadata.get("poll_after_seconds", 5)))))

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Content-Type-Options": "nosniff",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/events/stream")
@app.get("/api/v1/events/stream")
async def dashboard_event_stream() -> StreamingResponse:
    """Bounded dashboard-wide revision hint stream used by graph/CRM views."""
    compiler = KnowledgeCompiler(settings.knowledge_dir)

    async def frames():
        last_etag: str | None = None
        for _ in range(_SSE_MAX_FRAMES):
            pages = []
            for path in sorted(compiler.root.glob("*/*.json")):
                try:
                    page = compiler.load(path.parent.name, path.stem)
                except (OSError, ValueError):
                    page = None
                if page is not None:
                    pages.append(compiler.polling_metadata(page))
            digest = json.dumps(pages, sort_keys=True, separators=(",", ":"))
            if digest != last_etag:
                last_etag = digest
                yield _sse_json_frame(
                    "dashboard.updated", {"type": "dashboard.updated", "pages": pages}
                )
            else:
                yield ": heartbeat\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Content-Type-Options": "nosniff",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/knowledge/{entity_type}/{entity_id}", response_model=KnowledgePage)
async def knowledge_page(entity_type: str, entity_id: str) -> KnowledgePage:
    page = KnowledgeCompiler(settings.knowledge_dir).load(entity_type, entity_id)
    if page is None:
        raise HTTPException(404, "knowledge page not found")
    return page


@app.get("/api/settings/email", response_model=EmailSettingsResponse)
async def email_settings() -> dict:
    return _email_settings_payload()


@app.put("/api/settings/email", response_model=EmailSettingsResponse)
async def configure_email(request: EmailSettingsRequest) -> dict:
    try:
        service = _email_service()
        service.configure(
            request.destination,
            preferences={
                **request.preferences,
                "enabled": request.enabled,
                "frequency": request.frequency,
                "minimum_confidence": request.minimum_confidence,
            },
        )
        return _email_settings_payload(service)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.post("/api/settings/email/test")
async def test_email() -> dict:
    return _email_service().send_test()


def _digest_content() -> tuple[str, str, list[str]]:
    context = _grounded_analyst_context("")
    candidates = context["candidates"]
    changes_by_event: dict[tuple[str, tuple[str, ...], tuple[str, ...]], dict] = {}
    for change in context["changes"]:
        key = (
            str(change.get("hypothesis_id", change.get("display_name", ""))),
            tuple(sorted(change.get("causal_observation_ids", []))),
            tuple(sorted(change.get("change_types", []))),
        )
        changes_by_event.setdefault(key, change)
    changes = list(changes_by_event.values())
    subject = "Venture Intelligence partner digest"
    if not candidates and not changes:
        return (
            subject,
            "No validated candidates or meaningful intelligence changes are currently available.",
            [],
        )
    lines = ["Evidence-backed intelligence digest", ""]
    for item in candidates[:8]:
        lines.append(
            f"- {item['display_name']}: {item['confidence_pct']}% batch confidence "
            f"({item['state']})"
        )
    for item in changes[:5]:
        lines.append(f"- Change: {item['display_name']} {item['delta_pct']:+d} confidence points")
    links = list(
        dict.fromkeys(
            source["url"]
            for item in candidates
            for source in item["sources"]
            if source["url"].startswith(("http://", "https://"))
        )
    )[:20]
    return subject, "\n".join(lines), links


@app.post("/api/settings/email/digest")
async def email_digest(request: EmailDigestRequest) -> dict:
    subject, body, links = _digest_content()
    service = _email_service()
    if not request.send:
        return service.preview(subject, body, links=links)
    configured = service.preview(subject, body, links=links)
    if not configured["delivery_configured"]:
        return configured
    digest_id = hashlib.sha256(f"{subject}|{body}".encode()).hexdigest()[:24]
    return service.send(
        f"partner-digest-{digest_id}",
        subject,
        body,
        links=links,
        force=True,
    )


def _email_service() -> EmailNotificationService:
    return EmailNotificationService(
        settings.email_config_path,
        smtp_host=settings.email_smtp_host,
        smtp_port=settings.email_smtp_port,
        smtp_username=settings.email_smtp_username,
        smtp_password=settings.email_smtp_password,
        smtp_from=settings.email_smtp_from,
    )


def _email_settings_payload(service: EmailNotificationService | None = None) -> dict:
    selected = service or _email_service()
    return {**selected.load(), "runtime": selected.status()}


@app.get("/api/investigations/{entity_id}")
async def investigation(entity_id: str) -> dict:
    """Return the latest human-review investigation for an entity."""
    return {"entity_id": entity_id, "investigation": _get_db().get_investigation(entity_id)}


@app.get("/api/investigations")
async def investigations(limit: int = 50) -> dict:
    """Return recent human-review investigations for legacy clients."""
    db = _get_db()
    bounded = max(1, min(limit, 100))
    db._ensure_investigation_table()
    with db._connect() as connection:
        rows = connection.execute(
            "SELECT * FROM investigations ORDER BY updated_at DESC LIMIT ?", (bounded,)
        ).fetchall()
    return {"investigations": [dict(row) for row in rows]}


@app.get("/api/graph/{entity_id}")
async def graph_neighbors(entity_id: str) -> dict:
    """Return the bounded first-hop graph neighborhood for an entity."""
    return {"entity_id": entity_id, "neighbors": _get_db().get_node_neighbors(entity_id)}


@app.get("/api/graph")
async def graph_snapshot(limit: int = 100) -> dict:
    """Return a bounded graph snapshot for legacy dashboard consumers."""
    db = _get_db()
    bounded = max(1, min(limit, 500))
    with db._connect() as connection:
        nodes = connection.execute(
            "SELECT * FROM graph_nodes ORDER BY updated_at DESC LIMIT ?", (bounded,)
        ).fetchall()
        edges = connection.execute(
            "SELECT * FROM graph_edges ORDER BY observed_at DESC LIMIT ?", (bounded,)
        ).fetchall()
    safe_nodes = [
        _decode_graph_row(dict(row)) for row in nodes if _is_safe_graph_node(dict(row), db)
    ]
    node_ids = {item["node_id"] for item in safe_nodes}
    return {
        "nodes": safe_nodes,
        "edges": [
            _decode_graph_row(dict(row))
            for row in edges
            if row["source_id"] in node_ids and row["target_id"] in node_ids
        ],
    }


@app.get("/api/companies/evidence")
async def company_evidence(limit: int = 100) -> dict:
    """Canonical, integrity-gated rows for the interactive company evidence table."""
    db = _get_db()
    rows: list[dict] = []
    for company in db.all_companies():
        if not db.is_entity_validated(company.company_id) or not validated_entity_record(company):
            continue
        evidence = db.get_evidence_details_for_entity(company.company_id)
        events = db.get_events_for_entity(company.company_id)
        evidence_summary = summarize_evidence(evidence)
        sources = list(
            dict.fromkeys(url for item in evidence for url in item.get("source_urls", []) if url)
        )
        sources.extend(
            event.source_url
            for event in events
            if event.source_url and event.source_url not in sources
        )
        founder_ids = list(company.founder_ids)
        if not founder_ids:
            with db._connect() as connection:
                founder_ids = [
                    row["source_id"]
                    for row in connection.execute(
                        """
                        SELECT source_id FROM graph_edges
                        WHERE target_id=? AND edge_type='FOUNDED'
                        ORDER BY observed_at DESC
                        """,
                        (company.company_id,),
                    ).fetchall()
                ]
        founders = [db.get_person(founder_id) for founder_id in founder_ids]
        founder_details = []
        for person in founders:
            if person is None or not validated_entity_record(person):
                continue
            parts = person.canonical_name.split()
            founder_evidence = db.get_evidence_details_for_entity(person.person_id)
            founder_details.append(
                {
                    "person_id": person.person_id,
                    "full_name": person.canonical_name,
                    "first_name": parts[0] if parts else None,
                    "last_name": parts[-1] if len(parts) > 1 else None,
                    "founder_role": person.properties.get("founder_role")
                    or person.properties.get("role")
                    or "Founder",
                    "is_founder": bool(person.properties.get("is_founder", True)),
                    "confidence": person.identity_confidence,
                    "evidence": [normalize_evidence(item) for item in founder_evidence],
                    "source_urls": list(
                        dict.fromkeys(
                            url
                            for item in founder_evidence
                            for url in item.get("source_urls", [])
                            if url
                        )
                    ),
                }
            )
        knowledge = KnowledgeCompiler(settings.knowledge_dir).find(company.company_id)
        rows.append(
            {
                "entity_id": company.company_id,
                "company": company.canonical_name,
                "founders": [item["full_name"] for item in founder_details],
                "founder_details": founder_details,
                "website": company.website or company.yc_url,
                "evidence_count": len(evidence),
                "founder_background": "Unknown / insufficient public evidence",
                "confidence": company.identity_confidence,
                "sources": sources,
                "evidence": [normalize_evidence(item) for item in evidence],
                "evidence_by_category": evidence_summary["categories"],
                "evidence_categories": evidence_summary["categories"],
                "category_confidence": evidence_summary["category_confidence"],
                "overall_confidence": evidence_summary["overall_confidence"],
                "overall_confidence_pct": evidence_summary["overall_confidence_pct"],
                "knowledge_revision": KnowledgeCompiler.polling_metadata(knowledge),
            }
        )
    return {"results": rows[: max(1, min(limit, 100))]}


@app.get("/api/candidates/rejections")
async def rejected_candidates(limit: int = 100) -> dict:
    """Audit-only candidate lifecycle records; these are never entities."""
    return {"rejections": _get_db().get_candidate_rejections(max(1, min(limit, 100)))}


def _is_valid_canonical_entity(entity_id: str | None) -> bool:
    if not entity_id:
        return False
    db = _get_db()
    entity = db.get_company(entity_id) or db.get_person(entity_id)
    return bool(entity and db.is_entity_validated(entity_id) and validated_entity_record(entity))


def _is_safe_graph_node(node: dict, db: Database) -> bool:
    label = str(node.get("label") or "")
    node_type = str(node.get("node_type") or "")
    if is_generic_label(label):
        return False
    if node_type in {"Company", "Person"}:
        entity = db.get_company(node["node_id"]) or db.get_person(node["node_id"])
        return bool(
            entity and db.is_entity_validated(node["node_id"]) and validated_entity_record(entity)
        )
    return node_type in {"Accelerator", "Community"}


def _crm_record_view(record: dict, db: Database) -> dict:
    """Join the workflow record to canonical evidence without duplicating it."""
    entity_id = record["entity_id"]
    entity = db.get_company(entity_id) or db.get_person(entity_id)
    assert entity is not None  # checked by the caller
    evidence = db.get_evidence_for_entity(entity_id)
    events = db.get_events_for_entity(entity_id)
    result = dict(record)
    result.update(
        {
            "canonical_name": entity.canonical_name,
            "entity_type": "company" if db.get_company(entity_id) else "person",
            "website": getattr(entity, "website", None)
            or getattr(entity, "yc_url", None)
            or getattr(entity, "linkedin_url", None),
            "confidence": entity.identity_confidence,
            "evidence_count": len(evidence),
            "source_urls": list(
                dict.fromkeys(event.source_url for event in events if event.source_url)
            ),
            "next_action": "Review evidence and advance when conviction changes.",
        }
    )
    return result


def _decode_graph_row(row: dict) -> dict:
    for field in ("properties", "evidence_ids"):
        value = row.get(field)
        if isinstance(value, str):
            try:
                row[field] = json.loads(value)
            except json.JSONDecodeError:
                pass
    return row


@app.websocket("/ws")
@app.websocket("/api/ws")
async def websocket_updates(websocket: WebSocket) -> None:
    """Small health/stats channel retained for dashboard clients."""
    await websocket.accept()
    message_count = 0
    try:
        await websocket.send_json({"type": "connected", "project": "venture-intelligence"})
        while True:
            if message_count >= _WS_MAX_MESSAGES:
                await websocket.close(code=1008, reason="message limit exceeded")
                return
            try:
                raw_message = await asyncio.wait_for(
                    websocket.receive_text(), timeout=_WS_IDLE_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                await websocket.close(code=1000, reason="idle timeout")
                return
            if len(raw_message) > _WS_MAX_MESSAGE_CHARS:
                await websocket.close(code=1009, reason="message too large")
                return
            message_count += 1
            message = raw_message.strip().lower()
            if message in {"ping", '{"type":"ping"}'}:
                await websocket.send_json({"type": "pong"})
            elif message in {"stats", '{"type":"stats"}'}:
                await websocket.send_json({"type": "stats", "data": await stats()})
            else:
                await websocket.send_json({"type": "ack", "message": message[:200]})
    except WebSocketDisconnect:
        return


def _accepts_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "").casefold()


@app.get("/{spa_path:path}", include_in_schema=False)
async def spa_fallback(request: Request, spa_path: str):
    """Redirect clean links into HashRouter without losing investigation context."""
    if spa_path.startswith(("api/", "frontend/")) or not _accepts_html(request):
        raise HTTPException(404, "Not found")
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"/#/{spa_path}{query}", status_code=307)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("venture_intelligence.api.app:app", host=settings.api_host, port=settings.api_port)
