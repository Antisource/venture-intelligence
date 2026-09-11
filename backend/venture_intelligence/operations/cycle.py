"""Reusable intelligence cycle and deterministic foreground scheduler.

The API, CLI, and scheduler call this service.  Discovery remains bounded and
source failures are persisted as partial results instead of crashing a cycle.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field

from venture_intelligence.batch_discovery.factory import build_batch_discovery_service
from venture_intelligence.batch_discovery.models import (
    BatchDiscoveryRequest,
    SourceIndependence,
)
from venture_intelligence.batch_discovery.repository import BatchDiscoveryRepository
from venture_intelligence.config import Settings, settings
from venture_intelligence.programs import ProgramRegistry, get_program_registry
from venture_intelligence.shared.bus import EventBus
from venture_intelligence.storage.db import Database


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CycleStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED_OVERLAP = "skipped_overlap"


class IntelligenceChange(BaseModel):
    hypothesis_id: str
    display_name: str
    before_confidence_pct: int | None = None
    after_confidence_pct: int
    delta_pct: int
    before_state: str | None = None
    after_state: str
    causal_observation_ids: list[str] = Field(default_factory=list)
    change_types: list[str] = Field(default_factory=list)
    material_reasons: list[str] = Field(default_factory=list)
    explanation: str


_MEANINGFUL_CONFIDENCE_DELTA_PCT = 5


class CycleProgramResult(BaseModel):
    program_id: str
    batch_id: str
    discovery_run_id: str | None = None
    status: str
    stop_reason: str
    queries: int = 0
    requests: int = 0
    retries: int = 0
    providers_eligible: list[str] = Field(default_factory=list)
    providers_skipped: list[dict[str, Any]] = Field(default_factory=list)
    request_budget: dict[str, int | float] = Field(default_factory=dict)
    latency_ms: float = 0
    observations: int = 0
    hypotheses: int = 0
    candidates_created: int = 0
    candidates_updated: int = 0
    promotions: int = 0
    contradictions: int = 0
    enrichments: int = 0
    affected_entities: int = 0
    alerts: int = 0
    digests: int = 0
    changes: list[IntelligenceChange] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class IntelligenceCycleRecord(BaseModel):
    schema_version: str = "1.0"
    cycle_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: CycleStatus = CycleStatus.RUNNING
    trigger: str = "manual"
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None
    next_run_at: datetime | None = None
    program_results: list[CycleProgramResult] = Field(default_factory=list)
    error: str | None = None
    lock_recovery_reason: str | None = None


class SchedulerState(BaseModel):
    enabled: bool = True
    running: bool = False
    interval_hours: float = 12.0
    last_cycle_id: str | None = None
    last_run_at: datetime | None = None
    last_successful_run_at: datetime | None = None
    last_failed_run_at: datetime | None = None
    last_status: CycleStatus | None = None
    last_failure_reason: str | None = None
    next_run_at: datetime | None = None
    updated_at: datetime = Field(default_factory=_now)


class OperationsState(BaseModel):
    scheduler: SchedulerState = Field(default_factory=SchedulerState)
    cycles: list[IntelligenceCycleRecord] = Field(default_factory=list)


class CycleAlreadyRunning(RuntimeError):
    def __init__(self, message: str, record: IntelligenceCycleRecord | None = None) -> None:
        super().__init__(message)
        self.record = record


class OperationsStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> OperationsState:
        if not self.path.exists():
            return OperationsState()
        try:
            return OperationsState.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return OperationsState()

    def save(self, state: OperationsState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def upsert_cycle(self, cycle: IntelligenceCycleRecord) -> None:
        state = self.load()
        state.cycles = [item for item in state.cycles if item.cycle_id != cycle.cycle_id]
        state.cycles.insert(0, cycle)
        state.cycles = state.cycles[:100]
        self.save(state)

    def scheduler(self, value: SchedulerState | None = None) -> SchedulerState:
        state = self.load()
        if value is not None:
            state.scheduler = value
            self.save(state)
        return state.scheduler


class IntelligenceCycleService:
    def __init__(
        self,
        *,
        database: Database | None = None,
        registry: ProgramRegistry | None = None,
        runtime_settings: Settings = settings,
        store: OperationsStateStore | None = None,
        discovery_factory: Callable[..., object] = build_batch_discovery_service,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self.settings = runtime_settings
        self.database = database or Database(runtime_settings.db_path)
        self.registry = registry or get_program_registry()
        self.store = store or OperationsStateStore(runtime_settings.operations_state_path)
        self.discovery_factory = discovery_factory
        self.now = now
        self.lock_path = Path(runtime_settings.operations_lock_path)

    async def run_once(
        self,
        *,
        program_ids: list[str] | None = None,
        trigger: str = "manual",
        schedule_interval_hours: float | None = None,
    ) -> IntelligenceCycleRecord:
        active_ids = {program.id for program in self.registry.active()}
        unknown = set(program_ids or []) - active_ids
        if unknown:
            raise ValueError("Unknown or inactive program: " + ", ".join(sorted(unknown)))
        if not active_ids:
            raise ValueError("No active programs are configured")
        started = self.now()
        try:
            lock_token, lock_recovery_reason = self._acquire_lock()
        except CycleAlreadyRunning as error:
            record = IntelligenceCycleRecord(
                status=CycleStatus.SKIPPED_OVERLAP,
                trigger=trigger,
                started_at=started,
                finished_at=self.now(),
                error=str(error),
            )
            self.store.upsert_cycle(record)
            self._record_scheduler_outcome(record)
            raise CycleAlreadyRunning(str(error), record) from error
        record = IntelligenceCycleRecord(trigger=trigger, started_at=started)
        record.lock_recovery_reason = lock_recovery_reason
        interval = timedelta(
            hours=schedule_interval_hours or self.settings.scheduler_interval_hours
        )
        record.next_run_at = started + interval
        self.store.upsert_cycle(record)
        try:
            selected = [
                item
                for item in self.registry.active()
                if not program_ids or item.id in set(program_ids)
            ]
            for program in selected:
                record.program_results.append(await self._run_program(program.id))
            statuses = {item.status for item in record.program_results}
            record.status = (
                CycleStatus.COMPLETED if statuses <= {"completed"} else CycleStatus.PARTIAL
            )
        except Exception as error:
            record.status = CycleStatus.FAILED
            record.error = str(error)[:500]
        finally:
            record.finished_at = self.now()
            self.store.upsert_cycle(record)
            self._record_scheduler_outcome(record)
            self._release_lock(lock_token)
        return record

    def _record_scheduler_outcome(self, record: IntelligenceCycleRecord) -> None:
        scheduler = self.store.scheduler()
        scheduler.last_cycle_id = record.cycle_id
        scheduler.last_run_at = record.finished_at
        scheduler.last_status = record.status
        if record.next_run_at is not None:
            scheduler.next_run_at = record.next_run_at
        scheduler.updated_at = self.now()
        if record.status is CycleStatus.COMPLETED:
            scheduler.last_successful_run_at = record.finished_at
        elif record.status in {CycleStatus.PARTIAL, CycleStatus.FAILED}:
            scheduler.last_failed_run_at = record.finished_at
            scheduler.last_failure_reason = record.error or self._cycle_failure_reason(
                record
            )
        elif record.status is CycleStatus.SKIPPED_OVERLAP:
            scheduler.last_failure_reason = record.error
        self.store.scheduler(scheduler)

    @staticmethod
    def _cycle_failure_reason(record: IntelligenceCycleRecord) -> str:
        details = [
            error
            for program in record.program_results
            for error in program.errors
            if error
        ]
        if details:
            return "; ".join(details)[:500]
        stop_reasons = [
            program.stop_reason
            for program in record.program_results
            if program.stop_reason and program.status != "completed"
        ]
        return "; ".join(stop_reasons)[:500] or record.status.value

    async def _run_program(self, program_id: str) -> CycleProgramResult:
        program = self.registry.get(program_id)
        if program is None:
            raise LookupError(program_id)
        repository = BatchDiscoveryRepository(self.database)
        before = {
            item.hypothesis_id: item
            for item in repository.latest_hypotheses(
                program.id, program.batch, include_rejected=True
            )
        }
        program_started = self.now()
        service = self.discovery_factory(
            program.id,
            database=self.database,
            registry=self.registry,
            runtime_settings=self.settings,
        )
        try:
            run = await service.run(
                BatchDiscoveryRequest(program_id=program.id, batch_id=program.batch),
                event_bus=EventBus(self.database),
            )
        except Exception as error:
            return CycleProgramResult(
                program_id=program.id,
                batch_id=program.batch,
                status="failed",
                stop_reason="provider_or_pipeline_failure",
                errors=[str(error)[:500]],
            )
        finally:
            await service.close()
        affected_entities = await self.process_canonical_events()
        after = {item.hypothesis_id: item for item in run.hypotheses}
        changes: list[IntelligenceChange] = []
        for hypothesis_id, item in after.items():
            previous = before.get(hypothesis_id)
            before_confidence = previous.assessment.confidence_pct if previous else None
            delta = item.assessment.confidence_pct - (before_confidence or 0)
            causal_ids = [
                value
                for value in item.observation_ids
                if previous is None or value not in previous.observation_ids
            ]
            causal_observations = repository.observations_by_ids(causal_ids)
            new_independent = any(
                value.independence is SourceIndependence.INDEPENDENT
                for value in causal_observations
            )
            change_types: list[str] = []
            reasons: list[str] = []
            if previous is None:
                change_types.append("candidate_hypothesis_created")
                reasons.append("new_venture_hypothesis")
            if abs(delta) >= _MEANINGFUL_CONFIDENCE_DELTA_PCT:
                change_types.append("material_confidence_change")
                reasons.append(
                    f"confidence_delta_at_least_{_MEANINGFUL_CONFIDENCE_DELTA_PCT}_points"
                )
            if previous is not None and previous.state != item.state:
                change_types.append("lifecycle_state_changed")
                reasons.append("candidate_state_transition")
            if new_independent:
                change_types.append("new_independent_evidence")
                reasons.append("new_independent_origin")
            if previous is not None and (
                previous.assessment.contradictory_observations
                != item.assessment.contradictory_observations
            ):
                change_types.append("contradiction_changed")
                reasons.append("contradiction_introduced_or_resolved")
            if previous is not None and (
                len(previous.profile.financial_claims) != len(item.profile.financial_claims)
            ):
                change_types.append("funding_event")
                reasons.append("funding_claim_added_or_resolved")
            if previous is not None and (
                len(previous.profile.investor_signals) != len(item.profile.investor_signals)
            ):
                change_types.append("investor_relationship")
                reasons.append("investor_claim_added_or_resolved")
            if previous is not None and (
                previous.assessment.batch_evidence_present
                != item.assessment.batch_evidence_present
            ):
                change_types.append("accelerator_evidence")
                reasons.append("target_program_evidence_changed")
            if change_types:
                changes.append(
                    IntelligenceChange(
                        hypothesis_id=hypothesis_id,
                        display_name=item.display_name,
                        before_confidence_pct=before_confidence,
                        after_confidence_pct=item.assessment.confidence_pct,
                        delta_pct=delta,
                        before_state=previous.state.value if previous else None,
                        after_state=item.state.value,
                        causal_observation_ids=causal_ids,
                        change_types=change_types,
                        material_reasons=reasons,
                        explanation=(
                            "New candidate hypothesis"
                            if previous is None
                            else "; ".join(reasons)
                        ),
                    )
                )
        return CycleProgramResult(
            program_id=program.id,
            batch_id=program.batch,
            discovery_run_id=run.run_id,
            status=run.status,
            stop_reason=run.stop_reason,
            queries=len(run.queries),
            requests=sum(item.request_attempts for item in run.source_outcomes),
            retries=sum(item.retry_requests for item in run.source_outcomes),
            providers_eligible=sorted(
                {item.provider_id for item in run.source_outcomes if item.request_attempts > 0}
            ),
            providers_skipped=[
                {
                    "provider_id": item.provider_id,
                    "status": item.status.value,
                    "reason": item.error or item.status.value,
                }
                for item in run.source_outcomes
                if item.request_attempts == 0
            ],
            request_budget=run.budget.model_dump(mode="json"),
            latency_ms=max(
                0.0,
                (self.now() - program_started).total_seconds() * 1_000,
            ),
            observations=len(run.observations),
            hypotheses=len(run.hypotheses),
            candidates_created=sum(1 for item in after if item not in before),
            candidates_updated=sum(1 for item in after if item in before),
            promotions=run.promoted_count,
            contradictions=sum(
                item.assessment.contradictory_observations for item in run.hypotheses
            ),
            enrichments=sum(1 for item in run.hypotheses if item.profile.completeness_pct > 0),
            affected_entities=len(affected_entities),
            changes=changes,
            errors=[item.error for item in run.source_outcomes if item.error],
        )

    async def process_canonical_events(self) -> set[str]:
        """Drain promotions through canonical projections without external delivery."""
        from venture_intelligence.monitor import pipeline_tick
        from venture_intelligence.pipeline.entity_resolution import EntityResolver
        from venture_intelligence.pipeline.evidence_builder import EvidenceBuilder
        from venture_intelligence.pipeline.graph import GraphBuilder
        from venture_intelligence.pipeline.ranking_worker import RankingWorker
        from venture_intelligence.pipeline.recommendation_worker import RecommendationWorker
        from venture_intelligence.pipeline.signal_worker import SignalWorker
        from venture_intelligence.pipeline.validator import EventValidator

        class _LocalNotificationBoundary:
            @staticmethod
            def dispatch_pending() -> int:
                return 0

        return await pipeline_tick(
            EventBus(self.database),
            self.database,
            EventValidator(),
            EntityResolver(self.database),
            GraphBuilder(self.database),
            EvidenceBuilder(self.database),
            SignalWorker(self.database),
            RankingWorker(self.database),
            RecommendationWorker(self.database),
            _LocalNotificationBoundary(),
        )

    async def run_scheduler(
        self,
        *,
        interval_hours: float | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        once: bool = False,
    ) -> None:
        interval = interval_hours or self.settings.scheduler_interval_hours
        state = self.store.scheduler()
        state.enabled = True
        state.running = True
        state.interval_hours = interval
        state.updated_at = self.now()
        self.store.scheduler(state)
        try:
            while self.store.scheduler().enabled:
                await self.run_once(
                    trigger="scheduler",
                    schedule_interval_hours=interval,
                )
                if once:
                    break
                await sleep(interval * 3600)
        finally:
            state = self.store.scheduler()
            state.running = False
            state.updated_at = self.now()
            self.store.scheduler(state)

    def set_scheduler_enabled(self, enabled: bool) -> SchedulerState:
        state = self.store.scheduler()
        state.enabled = enabled
        state.updated_at = self.now()
        if not enabled:
            state.running = False
        return self.store.scheduler(state)

    def _acquire_lock(self) -> tuple[str, str | None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        token = str(uuid.uuid4())
        try:
            descriptor = os.open(
                self.lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError as error:
            stale_after = self.settings.operations_lock_stale_seconds
            try:
                age = self.now().timestamp() - self.lock_path.stat().st_mtime
            except OSError:
                age = 0
            try:
                payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload = {}
            pid = payload.get("pid")
            if isinstance(pid, int) and self._process_alive(pid):
                raise CycleAlreadyRunning("an intelligence cycle is already running") from error
            if not isinstance(pid, int) and age <= stale_after:
                raise CycleAlreadyRunning("an intelligence cycle is already running") from error
            recovery_reason = (
                f"recovered lock owned by non-running pid {pid}"
                if isinstance(pid, int)
                else f"recovered invalid lock older than {int(stale_after)} seconds"
            )
            self.lock_path.unlink(missing_ok=True)
            token, _ = self._acquire_lock()
            return token, recovery_reason
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "token": token,
                    "pid": os.getpid(),
                    "created_at": self.now().isoformat(),
                },
                handle,
            )
        return token, None

    @staticmethod
    def _process_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            # Windows does not provide POSIX kill(pid, 0) semantics. Querying
            # the process handle avoids accidentally signalling the owner.
            import ctypes

            process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not process:
                return False
            try:
                exit_code = ctypes.c_ulong()
                queried = ctypes.windll.kernel32.GetExitCodeProcess(
                    process,
                    ctypes.byref(exit_code),
                )
                return bool(queried and exit_code.value == 259)
            finally:
                ctypes.windll.kernel32.CloseHandle(process)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _release_lock(self, token: str) -> None:
        try:
            payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
            if payload.get("token") == token:
                self.lock_path.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


__all__ = [
    "CycleAlreadyRunning",
    "CycleProgramResult",
    "CycleStatus",
    "IntelligenceChange",
    "IntelligenceCycleRecord",
    "IntelligenceCycleService",
    "OperationsState",
    "OperationsStateStore",
    "SchedulerState",
]
