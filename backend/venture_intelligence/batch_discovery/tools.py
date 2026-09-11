"""Policy-safe OSINT tool benchmark and bounded local-tool adapters."""

from __future__ import annotations

import asyncio
import csv
import ipaddress
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field

from venture_intelligence.batch_discovery.models import (
    OsintToolBenchmark,
    OsintToolDecision,
    SourceTaxonomyRole,
)
from venture_intelligence.research.sources import SearchResult

TOOL_BENCHMARKS = [
    OsintToolBenchmark(
        tool="Maigret",
        version_checked="repository checked 2026-08-25",
        license="MIT",
        decision=OsintToolDecision.ADAPTER_ONLY,
        taxonomy_roles=[SourceTaxonomyRole.IDENTITY, SourceTaxonomyRole.ENRICHMENT],
        incremental_discoveries=0,
        useful_corroborations=2,
        false_matches=2,
        request_cost=500,
        runtime_seconds=50.0,
        structured_output_quality=88,
        provenance_quality=62,
        policy_compatibility=28,
        reasoning=(
            "Useful after a username is known, not for unknown-founder discovery. "
            "Offline exports and explicitly gated, bounded public-site execution are "
            "supported; restricted social crawling and bypass modes remain disabled."
        ),
    ),
    OsintToolBenchmark(
        tool="Sherlock",
        version_checked="0.15.0",
        license="MIT",
        decision=OsintToolDecision.ADAPTER_ONLY,
        taxonomy_roles=[SourceTaxonomyRole.IDENTITY, SourceTaxonomyRole.ENRICHMENT],
        incremental_discoveries=0,
        useful_corroborations=1,
        false_matches=2,
        request_cost=400,
        runtime_seconds=35.0,
        structured_output_quality=80,
        provenance_quality=58,
        policy_compatibility=42,
        reasoning=(
            "Username existence checks can corroborate a known handle but generate many "
            "false matches. Offline parsing and bounded allowlisted public-site execution "
            "are supported; username equality is not identity proof."
        ),
    ),
]


def local_osint_tool_diagnostics() -> list[dict[str, Any]]:
    """Truthfully report executable availability without invoking the network."""
    requirements = {
        "Maigret": (
            "Install with `uv tool install maigret`; invoke only through the gated "
            "live diagnostic."
        ),
        "Sherlock": (
            "Install with `uv tool install sherlock-project`; invoke only through the gated "
            "live diagnostic."
        ),
    }
    diagnostics: list[dict[str, Any]] = []
    for tool, executable in (("Maigret", "maigret"), ("Sherlock", "sherlock")):
        installed = shutil.which(executable) is not None
        diagnostics.append(
            {
                "tool": tool,
                "installed": installed,
                "enabled": False,
                "execution_status": "AVAILABLE_NOT_EXECUTED" if installed else "NOT_INSTALLED",
                "failure_reason": (
                    "Live execution was not requested for this installed tool."
                    if installed
                    else f"{executable} executable was not found on PATH"
                ),
                "identity_effect": "none; availability checks never alter identity confidence",
                "install_requirement": requirements[tool],
            }
        )
    return diagnostics


class PublicProfileClue(BaseModel):
    """Deduplicated public-profile clue; never a canonical identity assertion."""

    canonical_url: str
    username: str
    source_domain: str
    acquisition_tools: list[str]
    ambiguous_identity: bool = True
    identity_effect: str = "none"
    provenance: dict[str, Any] = Field(default_factory=dict)


class LocalOsintToolRun(BaseModel):
    """JSON-native result of one bounded local Maigret/Sherlock execution."""

    tool: str
    installed: bool
    enabled: bool
    execution_status: str
    input: str | None = None
    sites_attempted: int = 0
    public_profiles_returned: int = 0
    accepted: int = 0
    rejected: int = 0
    ambiguous: int = 0
    duplicates: int = 0
    execution_time_ms: float = 0
    failure_reason: str | None = None
    observations_created: int = 0
    public_profile_urls: list[str] = Field(default_factory=list)
    identity_effect: str = (
        "none; username equality is an ambiguous public-profile clue and cannot "
        "create, merge, corroborate, or promote a canonical identity"
    )
    install_requirement: str | None = None
    results: list[SearchResult] = Field(default_factory=list, exclude=True)


_SAFE_USERNAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_DEFAULT_LIVE_SITES = ("GitHub", "Reddit", "Medium")


def _safe_public_profile_url(value: str) -> bool:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    host = parsed.hostname.casefold().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return False
    try:
        return not ipaddress.ip_address(host).is_private
    except ValueError:
        return True


def _safe_tool_environment() -> dict[str, str]:
    """Pass only process/runtime variables; never forward application secrets."""
    allowed = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment["PYTHONUTF8"] = "1"
    return environment


async def _execute_bounded_tool(
    command: list[str],
    *,
    directory: Path,
    timeout_seconds: float,
) -> tuple[int | None, str, str, str | None]:
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=directory,
        env=_safe_tool_environment(),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        process.kill()
        stdout, stderr = await process.communicate()
        return (
            None,
            stdout.decode("utf-8", "replace")[-4_000:],
            stderr.decode("utf-8", "replace")[-4_000:],
            "bounded local-tool timeout; child process terminated and reaped",
        )
    return (
        process.returncode,
        stdout.decode("utf-8", "replace")[-4_000:],
        stderr.decode("utf-8", "replace")[-4_000:],
        None,
    )


def _maigret_payload(directory: Path, username: str) -> Any:
    report = directory / f"report_{username}_simple.json"
    return json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}


def _sherlock_payload(directory: Path, username: str) -> list[dict[str, Any]]:
    report = directory / f"{username}.csv"
    if not report.exists():
        return []
    with report.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def public_profile_origin(url: str) -> str:
    """Normalize a verified host alias, not arbitrary www hosts or usernames."""
    value = url.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.netloc.casefold() in {"github.com", "www.github.com"}:
        return urlunsplit((parsed.scheme.lower(), "github.com", parsed.path, parsed.query, ""))
    return value


def _policy_filter_results(results: list[SearchResult]) -> tuple[list[SearchResult], int, int]:
    accepted: list[SearchResult] = []
    seen: set[str] = set()
    rejected = 0
    duplicates = 0
    for result in results:
        original = result.url
        if not _safe_public_profile_url(original):
            rejected += 1
            continue
        canonical = public_profile_origin(original)
        key = canonical
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        result.url = canonical
        result.provenance.setdefault("acquisition_url", original)
        result.metadata.update(
            {
                "ambiguous_identity": True,
                "identity_confidence": 0.0,
                "canonical_identity_effect": "none",
                "promotion_eligible": False,
            }
        )
        accepted.append(result)
    return accepted, rejected, duplicates


async def run_local_osint_tool(
    tool: str,
    *,
    username: str,
    sites: tuple[str, ...] = _DEFAULT_LIVE_SITES,
    timeout_seconds: float = 30.0,
    allow_network: bool = False,
    known_positive_expected: bool = False,
) -> LocalOsintToolRun:
    """Execute one installed tool through a bounded, non-evasive application path."""
    normalized = tool.casefold().strip()
    if normalized not in {"maigret", "sherlock"}:
        raise ValueError("only Maigret and Sherlock are supported")
    if not _SAFE_USERNAME.fullmatch(username):
        raise ValueError("username must be a simple public identifier")
    if (
        not sites
        or len(sites) > 5
        or any(not re.fullmatch(r"[A-Za-z0-9_. -]{1,40}", site) for site in sites)
    ):
        raise ValueError("sites must contain between one and five simple site names")
    if not allow_network or os.getenv("VENTURE_INTELLIGENCE_LIVE_TESTS") != "1":
        raise PermissionError(
            "local OSINT execution requires the explicit live-test gate and network flag"
        )
    executable = shutil.which(normalized)
    display = normalized.title()
    package = "maigret" if normalized == "maigret" else "sherlock-project"
    requirement = f"Install with `uv tool install {package}`."
    if executable is None:
        return LocalOsintToolRun(
            tool=display,
            installed=False,
            enabled=False,
            execution_status="NOT_INSTALLED",
            input=username,
            failure_reason=f"{normalized} executable was not found on PATH",
            install_requirement=requirement,
        )
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=f"venture-intelligence-{normalized}-") as temp:
        directory = Path(temp)
        site_args = [argument for site in sites for argument in ("--site", site)]
        if normalized == "maigret":
            command = [
                executable,
                username,
                *site_args,
                "--timeout",
                "5",
                "--retries",
                "0",
                "--dns-resolver",
                "threaded" if os.name == "nt" else "async",
                "--max-connections",
                "3",
                "--no-recursion",
                "--no-extracting",
                "--no-autoupdate",
                "--no-color",
                "--no-progressbar",
                "--json",
                "simple",
                "--folderoutput",
                str(directory),
            ]
        else:
            command = [
                executable,
                username,
                *site_args,
                "--timeout",
                "5",
                "--folderoutput",
                str(directory),
                "--csv",
                "--print-found",
                "--no-color",
                "--no-txt",
            ]
        try:
            returncode, stdout, stderr, timeout_error = await _execute_bounded_tool(
                command,
                directory=directory,
                timeout_seconds=min(max(timeout_seconds, 1.0), 45.0),
            )
            payload = (
                _maigret_payload(directory, username)
                if normalized == "maigret"
                else _sherlock_payload(directory, username)
            )
            raw_results = ManualUsernameExportAdapter().parse(
                normalized,
                payload,
                username=username,
            )
            accepted, rejected, duplicates = _policy_filter_results(raw_results)
            for result in accepted:
                result.provenance.update(
                    {
                        "adapter": f"{normalized}-bounded-local-tool",
                        "access_mode": "local_tool",
                        "tool_network_execution": True,
                        "sites_requested": list(sites),
                        "policy_filtered": True,
                    }
                )
            failure = timeout_error
            if failure is None and returncode not in {0, None}:
                detail = (stderr or stdout)[-500:].strip()
                failure = f"tool exited with code {returncode}: {detail}".strip()
            if failure:
                status = "DEGRADED_EXTERNAL_TOOL"
            elif accepted:
                status = "LIVE_VALIDATED"
            elif known_positive_expected:
                status = "DEGRADED_EXTERNAL_TOOL"
                failure = (
                    "known-positive username returned no accepted public profiles; "
                    + (
                        (stderr or stdout)[-300:].strip()
                        or "tool emitted no structured match"
                    )
                )
            else:
                status = "HEALTHY_EMPTY"
            return LocalOsintToolRun(
                tool=display,
                installed=True,
                enabled=True,
                execution_status=status,
                input=username,
                sites_attempted=len(sites),
                public_profiles_returned=len(raw_results),
                accepted=len(accepted),
                rejected=rejected,
                ambiguous=len(accepted),
                duplicates=duplicates,
                execution_time_ms=(time.monotonic() - started) * 1_000,
                failure_reason=failure,
                observations_created=len(accepted),
                public_profile_urls=[item.url for item in accepted[:5]],
                install_requirement=requirement,
                results=accepted,
            )
        except (OSError, ValueError, json.JSONDecodeError, csv.Error) as error:
            return LocalOsintToolRun(
                tool=display,
                installed=True,
                enabled=True,
                execution_status="DEGRADED_EXTERNAL_TOOL",
                input=username,
                sites_attempted=len(sites),
                execution_time_ms=(time.monotonic() - started) * 1_000,
                failure_reason=f"{error.__class__.__name__}: {str(error)[:500]}",
                install_requirement=requirement,
            )


def deduplicate_public_profile_clues(
    runs: list[LocalOsintToolRun],
    *,
    username: str,
) -> list[PublicProfileClue]:
    """Merge acquisition paths without treating tools as independent origins."""
    clues: dict[str, PublicProfileClue] = {}
    restricted_hosts = {
        "linkedin.com",
        "x.com",
        "twitter.com",
        "instagram.com",
        "threads.net",
    }
    for run in runs:
        for result in run.results:
            key = public_profile_origin(result.url)
            existing = clues.get(key)
            if existing is not None:
                existing.acquisition_tools = sorted(
                    set(existing.acquisition_tools) | {run.tool}
                )
                existing.provenance["acquisition_tools"] = existing.acquisition_tools
                continue
            clues[key] = PublicProfileClue(
                canonical_url=key,
                username=username,
                source_domain=result.source_domain,
                acquisition_tools=[run.tool],
                provenance={
                    "origin_key": key,
                    "acquisition_tools": [run.tool],
                    "independent_evidence_origins": 1,
                    "restricted_profile_not_directly_crawled": (
                        result.source_domain in restricted_hosts
                    ),
                },
            )
    return list(clues.values())


class ManualUsernameExportAdapter:
    """Parse analyst-supplied Maigret/Sherlock JSON without executing either tool."""

    supported_tools = {"maigret", "sherlock"}

    def parse(self, tool: str, payload: Any, *, username: str) -> list[SearchResult]:
        normalized_tool = tool.casefold().strip()
        if normalized_tool not in self.supported_tools:
            raise ValueError("only Maigret and Sherlock manual JSON exports are supported")
        records = list(payload.values()) if isinstance(payload, dict) else payload
        if not isinstance(records, (list, tuple)):
            raise ValueError("OSINT export must be a JSON object or array")
        results: list[SearchResult] = []
        seen: set[str] = set()
        for record in records:
            if not isinstance(record, dict):
                continue
            url = str(
                record.get("url_user") or record.get("url") or record.get("profile_url") or ""
            ).strip()
            status = str(
                record.get("status")
                or record.get("status_enum")
                or record.get("exists")
                or ""
            ).casefold()
            claimed = record.get("exists") is True or any(
                marker in status for marker in ("claimed", "found", "exists")
            )
            if not claimed or not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            domain = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
            results.append(
                SearchResult(
                    url=url,
                    title=f"{username} public account on {domain}",
                    snippet="Analyst-supplied username-tool result; identity lead only.",
                    source_domain=domain,
                    metadata={
                        "candidate_name": None,
                        "usernames": [username],
                        "source_roles": ["IDENTITY", "ENRICHMENT"],
                        "stance": "neutral",
                        "identity_confidence": 0.55,
                        "source_reliability": 0.5,
                        "manual_tool_export": True,
                    },
                    provenance={
                        "adapter": f"{normalized_tool}-manual-json",
                        "access_mode": "manual_import",
                        "tool_network_execution": False,
                    },
                )
            )
        return results
