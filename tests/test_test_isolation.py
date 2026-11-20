"""The suite never touches the operator's .env, data/ directory or SMTP server.

Oracle: the declared field defaults in ``Settings`` (code, not environment)
and the repository root on disk. A regression that lets ``.env`` values or
real ``data/`` paths leak back into the shared settings object fails here.
"""

from __future__ import annotations

import smtplib
from pathlib import Path

import pytest
from venture_intelligence.config import Settings, settings
from venture_intelligence.pipeline.email import EmailNotificationService

from tests.conftest import DATA_PATH_SETTINGS, ISOLATED_ROOT

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", DATA_PATH_SETTINGS)
def test_data_paths_live_outside_the_repository(name: str) -> None:
    path = Path(getattr(settings, name)).resolve()
    assert path.is_relative_to(ISOLATED_ROOT.resolve())
    assert not path.is_relative_to(REPO_ROOT)


@pytest.mark.parametrize(
    "name",
    [
        "email_smtp_host",
        "email_smtp_port",
        "email_smtp_username",
        "email_smtp_password",
        "email_smtp_from",
        "slack_webhook_url",
    ],
)
def test_delivery_settings_are_code_defaults_not_dotenv(name: str) -> None:
    assert getattr(settings, name) == Settings.model_fields[name].default


def test_real_smtp_connections_are_refused() -> None:
    with pytest.raises(ConnectionRefusedError):
        smtplib.SMTP("localhost", 25)
    with pytest.raises(ConnectionRefusedError):
        smtplib.SMTP_SSL("localhost", 465)


def test_default_email_service_writes_only_inside_the_isolated_root() -> None:
    service = EmailNotificationService(settings.email_config_path)
    service.configure("analyst@example.test")
    written = Path(settings.email_config_path).resolve()
    assert written.exists()
    assert written.is_relative_to(ISOLATED_ROOT.resolve())
