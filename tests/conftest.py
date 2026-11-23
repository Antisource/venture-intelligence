"""Test-suite isolation from the operator's machine.

Tests must not need or touch credentials (AGENTS.md). Without this file the
suite read the operator's ``.env`` (pydantic-settings loads it when
``venture_intelligence.config`` is imported) and wrote to the operator's real
``data/`` directory, including the e-mail delivery log. With a destination
configured and ``frequency=immediate``, a test run could send a real e-mail.

This module runs before any test module is imported, so it isolates the
shared ``settings`` object at import time, before collection imports
application modules that might read it:

* ``VENTURE_INTELLIGENCE_ENV_FILE`` points at a missing file, so
  pydantic-settings never opens the operator's ``.env``;
* every setting is reset to its code default, ignoring the environment;
* every data path is redirected into a throwaway directory under the
  repository's gitignored ``tmp/`` (not the system temp directory, which may
  be locked by Windows policy, and not ``.pytest-tmp``, which pytest wipes
  when it first creates ``--basetemp``), never the repository's ``data/`` or
  ``knowledge/`` directories;
* SMTP-related environment variables read directly by ``pipeline/email.py``
  are removed from the process environment;
* ``smtplib.SMTP`` and ``smtplib.SMTP_SSL`` refuse to connect unless a test
  replaces them with its own fake.
"""

from __future__ import annotations

import os
import shutil
import smtplib
import tempfile
from pathlib import Path

import pytest

# Read with os.getenv by pipeline/email.py, so .env isolation alone does not
# cover them if a developer shell exports them.
_SMTP_ENVIRONMENT = (
    "EMAIL_SMTP_HOST",
    "EMAIL_SMTP_PORT",
    "EMAIL_SMTP_USERNAME",
    "EMAIL_SMTP_PASSWORD",
    "EMAIL_SMTP_FROM",
    "EMAIL_SMTP_STARTTLS",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "SMTP_STARTTLS",
)
for _name in _SMTP_ENVIRONMENT:
    os.environ.pop(_name, None)

_REPO_TMP = Path(__file__).resolve().parents[1] / "tmp"
_REPO_TMP.mkdir(exist_ok=True)
ISOLATED_ROOT = Path(tempfile.mkdtemp(prefix="vi-test-data-", dir=_REPO_TMP))
os.environ["VENTURE_INTELLIGENCE_ENV_FILE"] = str(ISOLATED_ROOT / "missing.env")

from venture_intelligence.config import Settings, settings  # noqa: E402

# Settings whose defaults point into the operator's working directory.
DATA_PATH_SETTINGS = (
    "db_path",
    "knowledge_dir",
    "source_settings_path",
    "source_state_path",
    "source_quality_report_path",
    "operations_state_path",
    "operations_lock_path",
    "email_config_path",
)

def _isolate_settings() -> None:
    defaults = Settings.model_construct()  # code defaults only: no .env, no environment
    for name in Settings.model_fields:
        setattr(settings, name, getattr(defaults, name))
    for name in DATA_PATH_SETTINGS:
        target = ISOLATED_ROOT / Path(getattr(defaults, name))
        target.parent.mkdir(parents=True, exist_ok=True)
        setattr(settings, name, str(target))


_isolate_settings()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    shutil.rmtree(ISOLATED_ROOT, ignore_errors=True)


class _RefusedSMTP:
    """Stand-in for smtplib transports: real SMTP connections are refused."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise ConnectionRefusedError(
            "tests must not open real SMTP connections; "
            "monkeypatch smtplib.SMTP with a fake transport"
        )


@pytest.fixture(autouse=True)
def _refuse_real_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smtplib, "SMTP", _RefusedSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _RefusedSMTP)
