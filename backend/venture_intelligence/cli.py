"""First-class local CLI backed by the same application services as the UI."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from venture_intelligence.agents.analyst import AnalystAgent
from venture_intelligence.batch_discovery.repository import BatchDiscoveryRepository
from venture_intelligence.capabilities import Lens, capabilities_for, help_hints
from venture_intelligence.config import settings
from venture_intelligence.intelligence.service import IntelligenceService
from venture_intelligence.operations.cycle import (
    CycleAlreadyRunning,
    CycleStatus,
    IntelligenceCycleRecord,
    IntelligenceCycleService,
    OperationsStateStore,
)
from venture_intelligence.operations.readiness import (
    ModelProbe,
    model_readiness,
    ollama_tags_probe,
    system_readiness,
)
from venture_intelligence.operations.validation import run_live_validation
from venture_intelligence.pipeline.email import EmailNotificationService
from venture_intelligence.programs import get_program_registry
from venture_intelligence.research.factory import build_research_agent
from venture_intelligence.research.provider_policy import BUILTIN_PROVIDER_POLICIES
from venture_intelligence.research.watchlist import (
    IntelligenceWatchlist,
    SourceSettingsStore,
    SourceStateStore,
)
from venture_intelligence.shared.bus import EventBus
from venture_intelligence.shared.models import ResearchTarget, ResearchTargetType
from venture_intelligence.storage.cleanup import reconcile_polluted_entities
from venture_intelligence.storage.db import Database


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _emit(value: Any, *, as_json: bool) -> None:
    payload = _jsonable(value)
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return
    if isinstance(payload, dict) and "readiness" in payload and "analyst" in payload:
        for item in payload["readiness"]["components"]:
            print(f"{item['component']}: {item['status']} — {item['summary']}")
        analyst = payload["analyst"]
        print(f"Local analyst runtime: {analyst['status']} ({analyst.get('provider', 'unknown')})")
        model = payload.get("model")
        if model:
            print(f"Ollama model {model['model']}: {model['status']} — {model['summary']}")
            if model.get("action"):
                print(f"  Fix: {model['action']}")
        if payload.get("help"):
            print("Next steps:")
            for hint in payload["help"]:
                print(f"  {hint}")
        return
    if isinstance(payload, dict) and "lens" in payload and "teasers" in payload:
        print(f"Lens {payload['lens']} ({payload['lens_title']})")
        for item in payload["capabilities"]:
            planned = f" [planned, {item['slice']}]" if item["status"] == "planned" else ""
            command = f"  vi {item['cli']}" if item["cli"] else ""
            print(f"- {item['title']}{planned}: {item['blurb']}{command}")
        if payload["teasers"]:
            print("Higher lenses add:")
            for item in payload["teasers"]:
                print(f"- {item['title']} ({item['unlock_lens']}): {item['teaser']}")
        return
    if isinstance(payload, dict):
        for key, item in payload.items():
            if isinstance(item, (dict, list)):
                print(f"{key}: {json.dumps(item, default=str)}")
            else:
                print(f"{key}: {item}")
    elif isinstance(payload, list):
        if not payload:
            print("No records.")
        for item in payload:
            print(json.dumps(item, sort_keys=True, default=str))
    else:
        print(payload)


def _database(path: str | None) -> Database:
    return Database(path or settings.db_path)


def _repository(path: str | None) -> BatchDiscoveryRepository:
    return BatchDiscoveryRepository(_database(path))


def _candidate_context(database: Database, limit: int = 8) -> dict[str, Any]:
    entities = IntelligenceService(database).analyst_entities("", limit)
    repository = BatchDiscoveryRepository(database)
    candidates: list[dict[str, Any]] = []
    citations: list[dict[str, str]] = []
    for program in get_program_registry().active():
        for candidate in repository.latest_hypotheses(
            program.id, program.batch, include_rejected=True
        ):
            observations = repository.observations_by_ids(candidate.observation_ids)
            sources = [
                {"label": item.source_domain, "url": item.canonical_url}
                for item in observations[:6]
            ]
            citations.extend(sources)
            candidates.append(
                {
                    "hypothesis_id": candidate.hypothesis_id,
                    "display_name": candidate.display_name,
                    "confidence_pct": candidate.assessment.confidence_pct,
                    "state": candidate.state.value,
                    "reason": (
                        candidate.assessment.components[0].explanation
                        if candidate.assessment.components
                        else "Insufficient evidence — candidate not promoted"
                    ),
                    "sources": sources,
                }
            )
            if len(candidates) >= limit:
                break
    state = OperationsStateStore(settings.operations_state_path).load()
    changes = [
        change.model_dump(mode="json")
        for cycle in state.cycles[:3]
        for program in cycle.program_results
        for change in program.changes
    ][:8]
    citations.extend(
        {"label": evidence["claim"], "url": url}
        for entity in entities
        for evidence in entity["evidence"]
        for url in evidence["source_urls"]
    )
    return {
        "entities": entities,
        "candidates": candidates,
        "changes": changes,
        "citations": citations[:16],
    }


def _email_service() -> EmailNotificationService:
    return EmailNotificationService(
        settings.email_config_path,
        smtp_host=settings.email_smtp_host,
        smtp_port=settings.email_smtp_port,
        smtp_username=settings.email_smtp_username,
        smtp_password=settings.email_smtp_password,
        smtp_from=settings.email_smtp_from,
    )


def _digest(database: Database) -> tuple[str, str, list[str]]:
    context = _candidate_context(database)
    lines = ["Venture Intelligence partner digest", ""]
    for item in context["candidates"]:
        lines.append(f"- {item['display_name']}: {item['confidence_pct']}% ({item['state']})")
    for item in context["changes"][:5]:
        lines.append(f"- Change: {item['display_name']} {item['delta_pct']:+d} confidence points")
    if len(lines) == 2:
        lines.append("No validated candidates or meaningful changes are available.")
    links = list(
        dict.fromkeys(source["url"] for item in context["candidates"] for source in item["sources"])
    )[:20]
    return "Venture Intelligence partner digest", "\n".join(lines), links


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="venture-intelligence",
        description="Local operator diagnostics and one shared production cycle.",
        epilog=(
            "Use COMMAND --help for details. Advanced read/export commands are "
            "documented in docs/PRODUCT_OPERATIONS.md."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument("--database", help="Use an explicit SQLite database path.")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    commands.add_parser("doctor", help="Check readiness and configuration (no secrets).")
    capabilities = commands.add_parser(
        "capabilities", help="List what this installation can do for a lens."
    )
    capabilities.add_argument(
        "--lens",
        choices=[item.value for item in Lens],
        default=Lens.L4.value,
        help="L1 Explorer, L2 Analyst, L3 Partner, L4 Technical VC (default).",
    )
    capabilities.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Emit machine-readable JSON.",
    )

    programs = commands.add_parser("programs").add_subparsers(dest="action", required=True)
    programs.add_parser("list")
    batches = commands.add_parser("batches").add_subparsers(dest="action", required=True)
    batch_list = batches.add_parser("list")
    batch_list.add_argument("--program", required=True)

    sources = commands.add_parser(
        "sources", help="Source policy and gated live checks."
    ).add_subparsers(dest="action", required=True)
    sources.add_parser("status")
    source_check = sources.add_parser("check")
    source_check.add_argument("--program", default="yc_current")
    source_check.add_argument("--allow-network", action="store_true")
    source_check.add_argument("--max-queries", type=int, default=4)
    source_check.add_argument("--max-requests", type=int, default=12)

    search = commands.add_parser("search").add_subparsers(dest="action", required=True)
    search.add_parser("providers")
    feeds = commands.add_parser("feeds").add_subparsers(dest="action", required=True)
    feeds.add_parser("list")
    feeds.add_parser("status")
    watchlist = commands.add_parser("watchlist").add_subparsers(dest="action", required=True)
    watchlist.add_parser("list")
    watchlist.add_parser("status")
    funding = commands.add_parser("funding").add_subparsers(dest="action", required=True)
    funding.add_parser("recent")
    investors = commands.add_parser("investors").add_subparsers(dest="action", required=True)
    investors.add_parser("recent")
    partner = commands.add_parser("partner").add_subparsers(dest="action", required=True)
    partner.add_parser("mentions")

    discovery = commands.add_parser("discovery").add_subparsers(dest="action", required=True)
    discovery_list = discovery.add_parser("candidates")
    discovery_list.add_argument("--program", default="yc_current")
    discovery_list.add_argument("--batch")
    discovery_list.add_argument("--include-rejected", action="store_true")

    candidate = commands.add_parser("candidate").add_subparsers(dest="action", required=True)
    for name in ("show", "evidence", "investigate", "export"):
        item = candidate.add_parser(name)
        item.add_argument("candidate_id")
        if name == "export":
            item.add_argument("--format", choices=("json", "markdown"), default="json")
            item.add_argument("--output")

    rankings = commands.add_parser("rankings").add_subparsers(dest="action", required=True)
    ranking_list = rankings.add_parser("list")
    ranking_list.add_argument("--program")
    ranking_list.add_argument("--batch")

    changes = commands.add_parser("changes").add_subparsers(dest="action", required=True)
    changes_list = changes.add_parser("list")
    changes_list.add_argument("--since", default="24h")
    commands.add_parser("inbox")

    graph = commands.add_parser("graph").add_subparsers(dest="action", required=True)
    graph_show = graph.add_parser("show")
    graph_show.add_argument("entity_id")

    research = commands.add_parser("research")
    research.add_argument("query")
    research.add_argument("--program")
    research.add_argument(
        "--type",
        choices=[item.value for item in ResearchTargetType],
        default=ResearchTargetType.OTHER.value,
    )

    analyst = commands.add_parser("analyst").add_subparsers(dest="action", required=True)
    analyst_ask = analyst.add_parser("ask")
    analyst_ask.add_argument("question")

    email = commands.add_parser("email").add_subparsers(dest="action", required=True)
    for name in ("preview", "send"):
        item = email.add_parser(name)
        item.add_argument("--digest", action="store_true", required=True)

    cycle = commands.add_parser(
        "cycle", help="Run one locked, persistent intelligence cycle."
    ).add_subparsers(dest="action", required=True)
    cycle_run = cycle.add_parser("run")
    cycle_run.add_argument("--program", action="append")

    scheduler = commands.add_parser(
        "scheduler", help="Start, stop, or inspect scheduled cycles."
    ).add_subparsers(dest="action", required=True)
    scheduler_start = scheduler.add_parser("start")
    scheduler_start.add_argument("--interval", default="12h")
    scheduler_start.add_argument("--once", action="store_true")
    scheduler.add_parser("status")
    scheduler.add_parser("stop")

    runs = commands.add_parser("runs").add_subparsers(dest="action", required=True)
    runs_list = runs.add_parser("list")
    runs_list.add_argument("--program")
    runs_show = runs.add_parser("show")
    runs_show.add_argument("run_id")

    database = commands.add_parser("db").add_subparsers(dest="action", required=True)
    database.add_parser("status")
    fresh = database.add_parser("fresh")
    fresh.add_argument("--path", required=True)
    fresh.add_argument("--confirm", action="store_true")
    fresh.add_argument("--confirm-operational", action="store_true")

    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--apply", action="store_true")
    return parser


def _hours(value: str) -> float:
    normalized = value.strip().casefold()
    if normalized.endswith("h"):
        return float(normalized[:-1])
    raise ValueError("interval must use hours, for example 12h")


def _doctor_help(model: Any) -> list[str]:
    steps = [model.action] if model.action else []
    return steps + help_hints(Lens.L4, exclude={"system_health"})


async def _async_command(args: argparse.Namespace) -> Any:
    registry = get_program_registry()
    if args.command == "doctor":
        readiness = system_readiness(
            settings.model_copy(update={"db_path": args.database or settings.db_path})
        )
        probe = getattr(args, "model_probe", None) or ollama_tags_probe
        model = await model_readiness(settings, probe=probe)
        return {
            "readiness": readiness,
            "analyst": await AnalystAgent().health(),
            "model": model,
            "help": _doctor_help(model),
            "database": str(Path(args.database or settings.db_path).resolve()),
            "secrets_redacted": True,
        }
    database = _database(args.database)
    if args.command == "sources" and args.action == "check":
        return await run_live_validation(
            program_id=args.program,
            report_path=settings.source_quality_report_path,
            allow_network=args.allow_network,
            max_queries=args.max_queries,
            max_requests=args.max_requests,
        )
    if args.command == "research":
        program = registry.get(args.program) if args.program else None
        agent = build_research_agent(
            program_name=program.name if program else None,
            program_batch=program.batch if program else None,
        )
        try:
            return await agent.run(
                ResearchTarget(
                    name=args.query,
                    type=ResearchTargetType(args.type),
                    program_id=args.program,
                ),
                event_bus=EventBus(database),
            )
        finally:
            await agent.close()
    if args.command == "analyst":
        return await AnalystAgent().answer(
            args.question,
            _candidate_context(database),
        )
    if args.command == "cycle":
        return await IntelligenceCycleService(database=database).run_once(
            program_ids=args.program,
            trigger="cli",
        )
    if args.command == "scheduler" and args.action == "start":
        interval = _hours(args.interval)
        service = IntelligenceCycleService(database=database)
        await service.run_scheduler(interval_hours=interval, once=args.once)
        return service.store.scheduler()
    raise RuntimeError("not an asynchronous command")


def _sync_command(args: argparse.Namespace) -> Any:
    if args.command == "capabilities":
        return capabilities_for(args.lens)
    registry = get_program_registry()
    database = _database(args.database)
    repository = BatchDiscoveryRepository(database)
    if args.command == "programs":
        return registry.serializable()
    if args.command == "batches":
        program = registry.get(args.program)
        if program is None:
            raise LookupError(args.program)
        return [item.model_dump(mode="json") for item in program.supported_batches]
    if args.command == "sources":
        return system_readiness().provider_policies
    if args.command == "search":
        overrides = SourceSettingsStore(settings.source_settings_path)
        return [
            {
                **policy.model_dump(mode="json"),
                "enabled": overrides.enabled(policy.provider_id, policy.default_enabled),
            }
            for policy in BUILTIN_PROVIDER_POLICIES
            if policy.access_method.value == "search_discovery"
        ]
    if args.command in {"feeds", "watchlist"}:
        configured = IntelligenceWatchlist.load(settings.intelligence_watchlist_path)
        if args.action == "list":
            entries = (
                [item for item in configured.entries if item.feed_urls]
                if args.command == "feeds"
                else configured.entries
            )
            return entries
        return SourceStateStore(settings.source_state_path).load()
    if args.command in {"funding", "investors", "partner"}:
        candidates = [
            item
            for program in registry.active()
            for item in repository.latest_hypotheses(
                program.id, program.batch, include_rejected=True
            )
        ]
        if args.command == "funding":
            return [
                {
                    "candidate": item.display_name,
                    "company": item.company_name,
                    "batch_confidence_pct": item.assessment.confidence_pct,
                    "investment_attention_pct": item.profile.investment_attention_pct,
                    "funding": round_item,
                }
                for item in candidates
                for round_item in item.profile.financial_claims
            ]
        signals = [
            {
                "candidate": item.display_name,
                "company": item.company_name,
                "signal": signal,
            }
            for item in candidates
            for signal in item.profile.investor_signals
        ]
        if args.command == "partner":
            return [item for item in signals if item["signal"].partner]
        return signals
    if args.command == "discovery":
        return repository.latest_hypotheses(
            args.program,
            args.batch or registry.get(args.program).batch,
            include_rejected=args.include_rejected,
        )
    if args.command == "candidate":
        detail = repository.hypothesis(args.candidate_id)
        if detail is None:
            raise LookupError("candidate not found")
        observations = repository.observations_by_ids(detail.observation_ids)
        if args.action == "show":
            return detail
        if args.action in {"evidence", "investigate"}:
            return {
                "candidate": detail,
                "observations": observations,
                "why_now": detail.why_now,
                "why_not_promoted": detail.assessment.promotion_blockers,
                "next_best_checks": detail.assessment.next_best_checks,
                "source_coverage": detail.assessment.source_coverage,
            }
        export = {
            "candidate": detail.model_dump(mode="json"),
            "evidence": [item.model_dump(mode="json") for item in observations],
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
        output = (
            json.dumps(export, indent=2, sort_keys=True)
            if args.format == "json"
            else _markdown_export(export)
        )
        if args.output:
            Path(args.output).write_text(output + "\n", encoding="utf-8")
            return {"status": "exported", "path": str(Path(args.output).resolve())}
        return output
    if args.command == "rankings":
        response = IntelligenceService(database).rankings(program_id=args.program)
        if args.batch:
            response.items = [item for item in response.items if item.entity.batch_id == args.batch]
        return response
    if args.command == "changes":
        cutoff = datetime.now(timezone.utc) - timedelta(hours=_hours(args.since))
        state = OperationsStateStore(settings.operations_state_path).load()
        return [
            change
            for cycle in state.cycles
            if cycle.started_at >= cutoff
            for result in cycle.program_results
            for change in result.changes
        ]
    if args.command == "inbox":
        return [
            item
            for program in registry.active()
            for item in repository.latest_hypotheses(
                program.id, program.batch, include_rejected=True
            )
            if item.review_state == "unreviewed"
        ]
    if args.command == "graph":
        return {
            "entity_id": args.entity_id,
            "neighbors": database.get_node_neighbors(args.entity_id),
            "evidence": database.get_evidence_details_for_entity(args.entity_id),
        }
    if args.command == "email":
        subject, body, links = _digest(database)
        service = _email_service()
        if args.action == "preview":
            return service.preview(subject, body, links=links)
        preview = service.preview(subject, body, links=links)
        if not preview["delivery_configured"]:
            return preview
        digest_id = hashlib.sha256(f"{subject}|{body}".encode()).hexdigest()[:24]
        return service.send(
            f"partner-digest-{digest_id}",
            subject,
            body,
            links=links,
            force=True,
        )
    if args.command == "scheduler":
        service = IntelligenceCycleService(database=database)
        if args.action == "stop":
            return service.set_scheduler_enabled(False)
        return service.store.scheduler()
    if args.command == "runs":
        if args.action == "list":
            return repository.runs(program_id=args.program)
        run = repository.run(args.run_id)
        if run is None:
            raise LookupError("run not found")
        return run
    if args.command == "db":
        path = Path(args.database or settings.db_path).resolve()
        if args.action == "status":
            return {
                "path": str(path),
                "exists": path.exists(),
                "tables": repository.state_counts(),
            }
        target = Path(args.path).resolve()
        operational = Path(settings.db_path).resolve()
        if not args.confirm:
            raise ValueError("db fresh requires --confirm")
        if target == operational and not args.confirm_operational:
            raise ValueError("refusing to reset the operational DB without --confirm-operational")
        for item in (target, Path(f"{target}-wal"), Path(f"{target}-shm")):
            item.unlink(missing_ok=True)
        Database(str(target))
        return {"status": "fresh", "path": str(target)}
    if args.command == "reconcile":
        return reconcile_polluted_entities(database, apply=args.apply)
    raise RuntimeError("unknown command")


def _markdown_export(payload: dict[str, Any]) -> str:
    candidate = payload["candidate"]
    lines = [
        f"# {candidate['display_name']}",
        "",
        f"- Program/batch: {candidate['program_id']} · {candidate['batch_id']}",
        f"- Batch confidence: {candidate['assessment']['confidence_pct']}%",
        f"- State: {candidate['state']}",
        f"- First observed: {candidate['first_seen_at']}",
        f"- Last observed: {candidate['last_updated_at']}",
        "",
        "## Evidence",
        "",
    ]
    for item in payload["evidence"]:
        lines.append(
            f"- [{item['source_domain']}]({item['canonical_url']}) — "
            f"{item['stance']}: {item['claim']}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None, *, model_probe: ModelProbe | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    args.model_probe = model_probe  # injected Ollama probe for doctor (tests); None = real
    asynchronous = (
        args.command in {"doctor", "research", "analyst", "cycle"}
        or args.command == "sources"
        and args.action == "check"
        or args.command == "scheduler"
        and args.action == "start"
    )
    try:
        result = asyncio.run(_async_command(args)) if asynchronous else _sync_command(args)
        _emit(result, as_json=args.json)
        if isinstance(result, IntelligenceCycleRecord):
            return 0 if result.status is CycleStatus.COMPLETED else 1
        if args.command == "doctor":
            statuses = {item.status.value for item in result["readiness"].components}
            if statuses & {"BROKEN", "DEGRADED"}:
                return 1
            if "USER_ACTION_REQUIRED" in statuses:
                return 2
        return 0
    except CycleAlreadyRunning as error:
        payload = error.record or {"status": "ALREADY_RUNNING", "error": str(error)}
        _emit(payload, as_json=args.json)
        return 3
    except (OSError, sqlite3.Error):
        # Paths and driver messages can contain secrets. Expected storage/IO
        # failures need an operator action, not a traceback or raw exception.
        _emit(
            {"status": "BROKEN", "error": "Storage or configuration is unavailable. Run doctor."},
            as_json=args.json,
        )
        return 1
    except (LookupError, ValueError, RuntimeError) as error:
        _emit({"status": "error", "error": str(error)}, as_json=args.json)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
