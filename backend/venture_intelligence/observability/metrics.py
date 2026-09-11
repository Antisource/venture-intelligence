"""
observability/metrics.py — lightweight in-process metrics (§9 Observability).

Collects key operational counters without requiring Prometheus in V1.
All metrics are stored in-memory and exposed via the /metrics API endpoint.
In V2, swap MetricsCollector.emit() for prometheus_client counters.

Tracked metrics:
  events_ingested_total      — total events written to DB
  events_by_source           — breakdown by Source enum
  entities_created_total     — new persons + companies created
  signals_computed_total     — signals written by SignalWorker
  alerts_dispatched_total    — alerts sent to partners
  agent_investigations_total — investigations triggered by AgentLoop
  pipeline_errors_total      — worker errors caught by monitor loop
  connector_scan_duration_ms — time per connector scan cycle
"""

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from threading import Lock

logger = logging.getLogger(__name__)


class MetricsCollector:
    """
    Thread-safe in-memory counter store.

    Usage:
        metrics = MetricsCollector()
        metrics.inc("events_ingested_total")
        metrics.inc("events_by_source", label="yc_website")
        snapshot = metrics.snapshot()
    """

    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._labeled: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._timers: dict[str, list[float]] = defaultdict(list)
        self._source_health: dict[str, dict] = {}
        self._connector_health: dict[str, dict] = {}
        self._lock = Lock()
        self._started_at = datetime.now(timezone.utc)

    def inc(self, name: str, label: str | None = None, amount: int = 1) -> None:
        """Increment a counter, optionally with a label."""
        with self._lock:
            self._counters[name] += amount
            if label:
                self._labeled[name][label] += amount

    def record_duration(self, name: str, duration_ms: float) -> None:
        """Record a duration sample (milliseconds)."""
        with self._lock:
            self._timers[name].append(duration_ms)
            # Keep only last 100 samples to bound memory
            if len(self._timers[name]) > 100:
                self._timers[name] = self._timers[name][-100:]

    def record_source(
        self,
        source: str,
        status_code: int | None,
        success: bool | None,
        latency_ms: float,
        *,
        transport_success: bool | None = None,
        usable_success: bool | None = None,
        source_status: str | None = None,
        request_made: bool = True,
        evidence_success: bool | None = None,
    ) -> None:
        """Track transport, source usability, and evidence semantics separately.

        ``success`` remains accepted for older callers, but is treated as both
        transport and usability only when the explicit fields are omitted.
        """
        transport_ok = bool(success) if transport_success is None else transport_success
        usable_ok = bool(success) if usable_success is None else usable_success
        normalized_status = source_status or ("AVAILABLE" if usable_ok else "FAILED")
        with self._lock:
            item = self._source_health.setdefault(
                source,
                {
                    "attempts": 0,
                    "requests": 0,
                    "transport_successes": 0,
                    "transport_failures": 0,
                    "transport_skipped": 0,
                    "usable_successes": 0,
                    "unusable_responses": 0,
                    "evidence_successes": 0,
                    "evidence_empty": 0,
                    "successes": 0,
                    "failures": 0,
                    "status_counts": {},
                    "source_status_counts": {},
                    "latencies_ms": [],
                    "last_transport_success": None,
                    "last_usable_success": None,
                    "last_success": None,
                    "circuit_state": "closed",
                },
            )
            item["attempts"] += 1
            if request_made:
                item["requests"] += 1
            if request_made:
                item["transport_successes" if transport_ok else "transport_failures"] += 1
            else:
                item["transport_skipped"] += 1
            item["usable_successes" if usable_ok else "unusable_responses"] += 1
            item["successes" if usable_ok else "failures"] += 1
            if evidence_success is not None:
                item["evidence_successes" if evidence_success else "evidence_empty"] += 1
            key = str(status_code) if status_code is not None else "network_error"
            item["status_counts"][key] = item["status_counts"].get(key, 0) + 1
            item["source_status_counts"][normalized_status] = (
                item["source_status_counts"].get(normalized_status, 0) + 1
            )
            item["latencies_ms"].append(round(latency_ms, 1))
            item["latencies_ms"] = item["latencies_ms"][-100:]
            now = datetime.now(timezone.utc).isoformat()
            if request_made and transport_ok:
                item["last_transport_success"] = now
            if usable_ok:
                item["last_usable_success"] = now
                item["last_success"] = now

    def record_connector_outcome(
        self,
        source: str,
        status: str,
        *,
        events_found: int,
        final_url: str | None = None,
        detail: str | None = None,
    ) -> None:
        """Expose parser/source drift independently from HTTP health."""
        with self._lock:
            self._connector_health[source] = {
                "status": status,
                "events_found": events_found,
                "final_url": final_url,
                "detail": detail,
                "observed_at": datetime.now(timezone.utc).isoformat(),
            }

    def record_evidence_outcome(self, source: str, success: bool) -> None:
        """Record extraction/evidence semantics without recounting transport."""
        with self._lock:
            item = self._source_health.setdefault(
                source,
                {
                    "attempts": 0,
                    "requests": 0,
                    "transport_successes": 0,
                    "transport_failures": 0,
                    "transport_skipped": 0,
                    "usable_successes": 0,
                    "unusable_responses": 0,
                    "evidence_successes": 0,
                    "evidence_empty": 0,
                    "successes": 0,
                    "failures": 0,
                    "status_counts": {},
                    "source_status_counts": {},
                    "latencies_ms": [],
                    "last_transport_success": None,
                    "last_usable_success": None,
                    "last_success": None,
                    "circuit_state": "closed",
                },
            )
            item["evidence_successes" if success else "evidence_empty"] += 1

    def set_circuit_state(self, source: str, state: str) -> None:
        with self._lock:
            item = self._source_health.get(source)
            if item is None:
                item = {
                    "attempts": 0,
                    "requests": 0,
                    "transport_successes": 0,
                    "transport_failures": 0,
                    "transport_skipped": 0,
                    "usable_successes": 0,
                    "unusable_responses": 0,
                    "evidence_successes": 0,
                    "evidence_empty": 0,
                    "successes": 0,
                    "failures": 0,
                    "status_counts": {},
                    "source_status_counts": {},
                    "latencies_ms": [],
                    "last_transport_success": None,
                    "last_usable_success": None,
                    "last_success": None,
                    "circuit_state": "closed",
                }
                self._source_health[source] = item
            item["circuit_state"] = state

    def snapshot(self) -> dict:
        """Return a complete snapshot of all metrics."""
        with self._lock:
            timer_stats = {}
            for name, samples in self._timers.items():
                if samples:
                    timer_stats[name] = {
                        "count": len(samples),
                        "avg_ms": round(sum(samples) / len(samples), 1),
                        "max_ms": round(max(samples), 1),
                        "min_ms": round(min(samples), 1),
                    }

            return {
                "uptime_seconds": (datetime.now(timezone.utc) - self._started_at).total_seconds(),
                "counters": dict(self._counters),
                "labeled": {k: dict(v) for k, v in self._labeled.items()},
                "timers": timer_stats,
                "source_health": {
                    key: {
                        **value,
                        "average_latency_ms": round(
                            sum(value["latencies_ms"]) / len(value["latencies_ms"]), 1
                        )
                        if value["latencies_ms"]
                        else None,
                        "latencies_ms": None,
                    }
                    for key, value in self._source_health.items()
                },
                "connector_health": {
                    key: dict(value) for key, value in self._connector_health.items()
                },
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }

    def time_it(self, name: str):
        """Context manager that records execution duration."""
        return _Timer(self, name)


class _Timer:
    def __init__(self, collector: MetricsCollector, name: str) -> None:
        self._collector = collector
        self._name = name
        self._start = 0.0

    def __enter__(self):
        self._start = time.monotonic()
        return self

    def __exit__(self, *_):
        elapsed_ms = (time.monotonic() - self._start) * 1000
        self._collector.record_duration(self._name, elapsed_ms)


# Module-level singleton — import this everywhere
metrics = MetricsCollector()
