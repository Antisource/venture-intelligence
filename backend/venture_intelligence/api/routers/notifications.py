"""Email notification settings, test sends, and partner digests."""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from venture_intelligence.api.routers.analyst import _grounded_analyst_context
from venture_intelligence.config import settings
from venture_intelligence.operations.readiness import EmailRuntimeStatus
from venture_intelligence.pipeline.email import EmailNotificationService

router = APIRouter()


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


@router.get("/api/settings/email", response_model=EmailSettingsResponse)
async def email_settings() -> dict:
    return _email_settings_payload()


@router.put("/api/settings/email", response_model=EmailSettingsResponse)
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


@router.post("/api/settings/email/test")
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


@router.post("/api/settings/email/digest")
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
