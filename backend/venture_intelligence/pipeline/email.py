"""Optional SMTP email notifications with durable, duplicate-safe delivery logs."""

from __future__ import annotations

import json
import logging
import re
import smtplib
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def validate_email(address: str) -> bool:
    return bool(_EMAIL_RE.fullmatch(address.strip()))


@dataclass
class EmailPreferences:
    enabled: bool = False
    new_high_confidence: bool = True
    evidence_changes: bool = True
    contradictions: bool = True
    program_matches: bool = True
    threshold_alerts: bool = True
    research_failures: bool = True
    new_founder_discovery: bool = True
    stale_knowledge: bool = True
    crm_status_change: bool = True
    investigation_completed: bool = True
    frequency: str = "immediate"
    minimum_confidence: float = 0.65

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class EmailNotificationService:
    """SMTP adapter; configuration/logs are separate from knowledge JSON."""

    def __init__(
        self,
        config_path: str | Path = "data/email_notifications.json",
        *,
        smtp_sender: Callable[[EmailMessage], object] | None = None,
        max_retries: int = 2,
        backoff_seconds: float = 0.05,
        smtp_host: str | None = None,
        smtp_port: int = 587,
        smtp_username: str | None = None,
        smtp_password: str | None = None,
        smtp_from: str | None = None,
    ) -> None:
        self.path = Path(config_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.smtp_sender = smtp_sender
        self.max_retries = max(0, max_retries)
        self.backoff_seconds = max(0.0, backoff_seconds)
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.smtp_from = smtp_from

    def load(self) -> dict:
        if not self.path.exists():
            return {
                "destination": None,
                "preferences": EmailPreferences().as_dict(),
                "deliveries": [],
            }
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            defaults = EmailPreferences().as_dict()
            value["preferences"] = {**defaults, **value.get("preferences", {})}
            value.setdefault("deliveries", [])
            return value
        except (OSError, ValueError):
            return {
                "destination": None,
                "preferences": EmailPreferences().as_dict(),
                "deliveries": [],
            }

    def configure(self, destination: str | None, *, preferences: dict | None = None) -> dict:
        if destination and not validate_email(destination):
            raise ValueError("destination must be a valid email address")
        data = self.load()
        data["destination"] = destination.strip() if destination else None
        if preferences:
            current = data["preferences"]
            for key, value in preferences.items():
                if key not in current:
                    continue
                if key == "frequency":
                    if value not in {"immediate", "daily", "weekly"}:
                        raise ValueError("frequency must be immediate, daily, or weekly")
                    current[key] = value
                elif key == "minimum_confidence":
                    numeric = float(value)
                    if not 0 <= numeric <= 1:
                        raise ValueError("minimum_confidence must be between 0 and 1")
                    current[key] = numeric
                else:
                    current[key] = bool(value)
        self._save(data)
        return data

    def set_enabled(self, enabled: bool) -> dict:
        data = self.load()
        data["preferences"]["enabled"] = bool(enabled)
        self._save(data)
        return data

    def status(self) -> dict[str, str | None]:
        """Return non-secret transport, destination, and effective-send state."""
        data = self.load()
        transport_configured = bool(self.smtp_host)
        destination_configured = bool(
            data.get("destination") and validate_email(str(data["destination"]))
        )
        sending_enabled = bool(
            transport_configured
            and destination_configured
            and data.get("preferences", {}).get("enabled", False)
        )
        deliveries = data.get("deliveries", [])
        last_outcome = deliveries[-1].get("status") if deliveries else None
        return {
            "transport_status": "CONFIGURED" if transport_configured else "NOT_CONFIGURED",
            "destination_status": (
                "CONFIGURED" if destination_configured else "NOT_CONFIGURED"
            ),
            "sending_status": "ENABLED" if sending_enabled else "DISABLED",
            "last_outcome": str(last_outcome).upper() if last_outcome else None,
        }

    def send_test(self) -> dict:
        return self.send(
            "test-email",
            "Venture Intelligence test email",
            "Email notifications are configured.",
            force=True,
        )

    def preview(
        self,
        subject: str,
        body: str,
        *,
        links: list[str] | None = None,
    ) -> dict:
        """Render a local brief without recording or claiming external delivery."""
        data = self.load()
        configured = bool(
            data.get("destination")
            and (self.smtp_host or _env("EMAIL_SMTP_HOST") or _env("SMTP_HOST"))
        )
        return {
            "status": "preview",
            "delivery_configured": configured,
            "notice": (
                "DELIVERY CONFIGURED — preview has not been sent"
                if configured
                else "DELIVERY NOT CONFIGURED — PREVIEW AVAILABLE"
            ),
            "destination": data.get("destination"),
            "subject": subject,
            "body": body,
            "links": links or [],
        }

    def send(
        self,
        notification_id: str,
        subject: str,
        body: str,
        *,
        force: bool = False,
        links: list[str] | None = None,
        category: str = "threshold_alerts",
        confidence: float | None = None,
    ) -> dict:
        data = self.load()
        destination = data.get("destination")
        pref = data.get("preferences", {})
        if not destination or (not pref.get("enabled", False) and not force):
            return self._record(data, notification_id, "suppressed", None)
        if not force and not pref.get(category, True):
            return self._record(data, notification_id, "suppressed", f"preference:{category}")
        if (
            not force
            and confidence is not None
            and confidence < float(pref.get("minimum_confidence", 0.0))
        ):
            return self._record(data, notification_id, "suppressed", "minimum_confidence")
        if not force and pref.get("frequency", "immediate") != "immediate":
            return self._record(data, notification_id, "queued", f"frequency:{pref['frequency']}")
        if not validate_email(destination):
            return self._record(data, notification_id, "failed", "invalid destination")
        if any(
            item.get("notification_id") == notification_id and item.get("status") == "sent"
            for item in data["deliveries"]
        ):
            return self._record(data, notification_id, "suppressed", "duplicate")
        message = EmailMessage()
        message["To"] = destination
        message["From"] = self.smtp_from or _smtp_from()
        message["Subject"] = subject
        message.set_content(body + ("\n\nLinks:\n" + "\n".join(links) if links else ""))
        self._record(data, notification_id, "pending", None)
        error: str | None = None
        for attempt in range(self.max_retries + 1):
            try:
                if self.smtp_sender:
                    refused = self.smtp_sender(message)
                    if isinstance(refused, dict) and refused:
                        raise RuntimeError("SMTP provider refused one or more recipients")
                else:
                    host = self.smtp_host or _env("EMAIL_SMTP_HOST") or _env("SMTP_HOST")
                    if not host:
                        raise RuntimeError("SMTP_HOST is not configured")
                    with smtplib.SMTP(host, self.smtp_port, timeout=10) as smtp:
                        if (
                            _env("EMAIL_SMTP_STARTTLS") or _env("SMTP_STARTTLS", "true")
                        ).casefold() == "true":
                            smtp.starttls()
                        user = (
                            self.smtp_username
                            or _env("EMAIL_SMTP_USERNAME")
                            or _env("SMTP_USERNAME")
                        )
                        password = (
                            self.smtp_password
                            or _env("EMAIL_SMTP_PASSWORD")
                            or _env("SMTP_PASSWORD")
                        )
                        if user and password:
                            smtp.login(user, password)
                        refused = smtp.send_message(message)
                        if refused:
                            raise RuntimeError("SMTP provider refused one or more recipients")
                return self._record(data, notification_id, "sent", None, attempt + 1)
            except Exception as exc:  # delivery must never crash research
                error = str(exc)[:500]
                if attempt < self.max_retries:
                    time.sleep(self.backoff_seconds * (2**attempt))
        logger.warning("email delivery failed: %s", error)
        return self._record(data, notification_id, "failed", error, self.max_retries + 1)

    def _record(
        self, data: dict, notification_id: str, status: str, error: str | None, attempts: int = 0
    ) -> dict:
        item = {
            "alert_id": notification_id,
            "notification_id": notification_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "attempts": attempts,
            "error": error,
        }
        data.setdefault("deliveries", []).append(item)
        self._save(data)
        return item

    def _save(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _env(name: str, default: str | None = None) -> str | None:
    import os

    return os.getenv(name, default)


def _smtp_from() -> str:
    return (
        _env("EMAIL_SMTP_FROM")
        or _env("SMTP_FROM")
        or _env("EMAIL_SMTP_USERNAME")
        or _env("SMTP_USERNAME")
        or "venture-intelligence@localhost"
    )
