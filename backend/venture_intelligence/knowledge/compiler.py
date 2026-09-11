"""Deterministic incremental JSON -> Markdown knowledge compiler.

JSON files are canonical and append-only evidence is copied by value. Markdown
is a reproducible view and is never read as application state.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from venture_intelligence.knowledge.evidence import normalize_evidence, summarize_evidence
from venture_intelligence.knowledge.models import ClaimStatus, KnowledgeClaim, KnowledgePage
from venture_intelligence.pipeline.entity_integrity import is_generic_label

_ENTITY_TYPES = {"company", "founder", "person", "program", "source", "investigation"}
_ENTITY_DIRECTORIES = {
    "company": "companies",
    "founder": "founders",
    "person": "founders",
    "program": "programs",
    "source": "sources",
    "investigation": "investigations",
}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "unknown"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stamp(value: datetime | str | None) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value or datetime.now(timezone.utc).isoformat())


class KnowledgeValidationReport(dict):
    """JSON-native validation result, also convenient as a mapping."""

    @property
    def valid(self) -> bool:
        return not any(
            self.get(key)
            for key in ("contradictions", "unsupported", "broken_references", "orphans")
        )


class KnowledgeCompiler:
    """Store canonical pages and compile deterministic human-readable views."""

    def __init__(self, root: str | Path = "knowledge", stale_after_days: int = 180) -> None:
        self.root = Path(root)
        self.stale_after_days = stale_after_days

    def page_path(self, entity_type: str, entity_id: str) -> Path:
        kind = entity_type.casefold()
        kind = {
            "companies": "company",
            "founders": "founder",
            "persons": "person",
            "programs": "program",
            "sources": "source",
            "investigations": "investigation",
        }.get(kind, kind)
        if kind == "person":
            kind = "founder"
        if kind not in _ENTITY_TYPES:
            raise ValueError(f"unsupported knowledge entity type: {entity_type}")
        return self.root / _ENTITY_DIRECTORIES[kind] / f"{_slug(entity_id)}.json"

    def markdown_path(self, entity_type: str, entity_id: str) -> Path:
        return self.page_path(entity_type, entity_id).with_suffix(".md")

    def load(self, entity_type: str, entity_id: str) -> KnowledgePage | None:
        path = self.page_path(entity_type, entity_id)
        if not path.exists() and path.parent.name == "companies":
            legacy_path = self.root / "companys" / path.name
            if legacy_path.exists():
                path = legacy_path
        if not path.exists():
            return None
        return KnowledgePage.model_validate_json(path.read_text(encoding="utf-8"))

    def find(self, entity_id: str) -> KnowledgePage | None:
        """Find a page by canonical ID when its entity type is not known."""
        for path in sorted(self.root.glob("*/*.json")):
            try:
                page = KnowledgePage.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if page.entity_id == entity_id:
                return page
        return None

    @staticmethod
    def polling_metadata(page: KnowledgePage | None, poll_after_seconds: int = 5) -> dict[str, Any]:
        """Return lightweight revision metadata for polling/SSE consumers."""
        if page is None:
            return {
                "status": "not_found",
                "revision_count": 0,
                "revision_id": None,
                "updated_at": None,
                "etag": None,
                "poll_after_seconds": poll_after_seconds,
            }
        serialized = _json(page.model_dump(mode="json"))
        etag = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]
        latest = page.revisions[-1] if page.revisions else {}
        return {
            "status": "available",
            "revision_count": len(page.revisions),
            "revision_id": latest.get("revision_id") or etag,
            "updated_at": _stamp(page.updated_at),
            "etag": etag,
            "poll_after_seconds": poll_after_seconds,
        }

    @staticmethod
    def evidence_category(item: dict[str, Any]) -> str:
        """Classify evidence without changing the immutable source record."""
        return normalize_evidence(item)["category"]

    @classmethod
    def evidence_view(cls, item: dict[str, Any]) -> dict[str, Any]:
        """Build a JSON-native, UI-safe view while preserving source fields."""
        return normalize_evidence(dict(item))

    def compile_page(
        self,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any] | None = None,
        *,
        claims: list[KnowledgeClaim | dict[str, Any]] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        provenance: list[dict[str, Any]] | None = None,
        confidence: float | None = None,
    ) -> KnowledgePage:
        if is_generic_label(entity_id):
            raise ValueError(f"generic term cannot become a knowledge entity: {entity_id}")
        now = datetime.now(timezone.utc)
        old = self.load(entity_type, entity_id)
        incoming = dict(payload or {})
        payload_confidence = incoming.pop("confidence", None)
        incoming.pop("entity_type", None)
        incoming_claims = [
            KnowledgeClaim.model_validate(item) for item in (claims or incoming.pop("claims", []))
        ]
        incoming_evidence = [
            self.evidence_view(dict(item)) for item in (evidence or incoming.pop("evidence", []))
        ]
        incoming_provenance = list(provenance or incoming.pop("provenance", []))
        if old is None:
            page = KnowledgePage(
                entity_type="founder" if entity_type == "person" else entity_type,
                entity_id=entity_id,
                **incoming,
                claims=incoming_claims,
                evidence=incoming_evidence,
                provenance=incoming_provenance,
                confidence=confidence
                if confidence is not None
                else float(payload_confidence or 0.0),
                created_at=now,
                updated_at=now,
            )
        else:
            before = old.model_dump(mode="json")
            page = old.model_copy(deep=True)
            page.updated_at = now
            page.identity.update(incoming.pop("identity", {}))
            for field in ("known_facts", "historical_information", "related_entities"):
                additions = incoming.pop(field, [])
                existing = getattr(page, field)
                merged = {_json(item): item for item in existing}
                merged.update({_json(item): item for item in additions})
                setattr(page, field, [merged[key] for key in sorted(merged)])
            page.evidence = self._merge_evidence(page.evidence, incoming_evidence)
            page.provenance = self._merge_evidence(page.provenance, incoming_provenance)
            page.claims = self._merge_claims(page.claims, incoming_claims)
            if confidence is not None:
                page.confidence = max(page.confidence, confidence)
        page = self._reconcile(page)
        self._refresh_evidence_summary(page)
        if old is not None:
            digest = hashlib.sha256(_json(before).encode()).hexdigest()[:16]
            before_categories = before.get("category_confidence", {})
            changed_categories = sorted(
                category
                for category in set(before_categories) | set(page.category_confidence)
                if before_categories.get(category) != page.category_confidence.get(category)
            )
            page.revisions.append(
                {
                    "revision_id": digest,
                    "timestamp": _stamp(now),
                    "content_hash": digest,
                    "changed_categories": changed_categories,
                    "confidence_delta": round(
                        page.confidence - float(before.get("confidence", 0.0)), 4
                    ),
                    "category_confidence": {
                        key: page.category_confidence[key] for key in changed_categories
                    },
                    "snapshot": before,
                }
            )
        self._write(page)
        return page

    @staticmethod
    def _refresh_evidence_summary(page: KnowledgePage) -> None:
        summary = summarize_evidence(page.evidence)
        page.evidence = summary["items"]
        page.evidence_by_category = summary["categories"]
        page.category_confidence = summary["category_confidence"]
        page.overall_confidence_pct = summary["overall_confidence_pct"]
        page.overall_confidence_label = summary["overall_confidence_label"]
        page.confidence = max(0.0, min(1.0, float(summary["overall_confidence"])))

    def compile_run(self, run: Any) -> KnowledgePage:
        """Incrementally compile a ResearchRun without asserting an entity."""
        attached = [
            item
            for item in run.evidence
            if str(getattr(item.attribution_status, "value", item.attribution_status))
            == "attached"
        ]
        unbound = [item for item in run.evidence if item not in attached]
        promoted = bool(run.entities and attached and run.confidence > 0)
        entity_type = "founder" if run.target.type.value == "person" else run.target.type.value
        if entity_type not in _ENTITY_TYPES:
            entity_type = "investigation"
        entity_id = run.target.name
        if not promoted or is_generic_label(entity_id):
            # Preserve weak evidence as a candidate investigation, never as a
            # canonical person/company named after a platform or generic term.
            entity_type = "investigation"
            entity_id = f"candidate-{_slug(entity_id)}"

        def claims_for(items: list[Any]) -> list[KnowledgeClaim]:
            return [
                KnowledgeClaim(
                    claim_id=f"research:{item.evidence_id}",
                    statement=(
                        item.title
                        or item.snippet
                        or f"Public source mentions {run.target.name}"
                    ),
                    status=(
                        ClaimStatus.CONTRADICTED
                        if str(
                            getattr(item.attribution_status, "value", item.attribution_status)
                        )
                        == "contradictory"
                        else ClaimStatus.UNKNOWN
                        if str(
                            getattr(item.attribution_status, "value", item.attribution_status)
                        )
                        == "unbound"
                        else ClaimStatus.HISTORICAL
                        if item.temporal_status == "historical"
                        else ClaimStatus.SUPPORTED
                    ),
                    confidence=item.confidence,
                    evidence_ids=[item.evidence_id],
                    provenance=item.provenance,
                )
                for item in items
            ]

        canonical_items = attached if promoted else run.evidence
        page = self.compile_page(
            entity_type,
            entity_id,
            {
                "identity": {
                    "name": run.target.name,
                    "target_type": run.target.type.value,
                    "validation_state": "promoted" if promoted else "unverified",
                    "research_run_id": run.run_id,
                    "stop_reason": run.stop_reason,
                }
            },
            claims=claims_for(canonical_items),
            evidence=[item.model_dump(mode="json") for item in canonical_items],
            provenance=[item.provenance for item in canonical_items if item.provenance],
            confidence=run.confidence,
        )
        if promoted and unbound:
            self.compile_page(
                "investigation",
                f"candidate-{_slug(run.target.name)}",
                {
                    "identity": {
                        "name": run.target.name,
                        "target_type": run.target.type.value,
                        "validation_state": "unbound",
                        "research_run_id": run.run_id,
                        "stop_reason": run.stop_reason,
                    }
                },
                claims=claims_for(unbound),
                evidence=[item.model_dump(mode="json") for item in unbound],
                provenance=[item.provenance for item in unbound if item.provenance],
                confidence=0.0,
            )
        return page

    def validate(self) -> KnowledgeValidationReport:
        report = KnowledgeValidationReport(
            contradictions=[],
            unsupported=[],
            broken_references=[],
            orphans=[],
            duplicates=[],
            stale=[],
        )
        pages: dict[str, KnowledgePage] = {}
        for path in sorted(self.root.glob("*/*.json")):
            try:
                page = KnowledgePage.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception as exc:
                report["broken_references"].append({"path": str(path), "error": str(exc)})
                continue
            pages[page.entity_id] = page
            evidence_ids = {str(item.get("evidence_id")) for item in page.evidence}
            for claim in page.claims:
                if not claim.evidence_ids:
                    report["unsupported"].append(
                        {"entity_id": page.entity_id, "claim_id": claim.claim_id}
                    )
                missing = sorted(set(claim.evidence_ids) - evidence_ids)
                if missing:
                    report["broken_references"].append(
                        {"claim_id": claim.claim_id, "missing": missing}
                    )
                if claim.status == ClaimStatus.STALE:
                    report["stale"].append(
                        {"entity_id": page.entity_id, "claim_id": claim.claim_id}
                    )
            for item in page.evidence:
                if (
                    item.get("temporal_status") == "historical"
                    and item.get("source_domain") != "web.archive.org"
                ):
                    report["broken_references"].append(
                        {
                            "evidence_id": item.get("evidence_id"),
                            "error": "historical evidence missing archive provenance",
                        }
                    )
            seen: set[str] = set()
            for item in page.evidence:
                key = str(item.get("canonical_url") or item.get("url") or item.get("title"))
                if key in seen:
                    report["duplicates"].append({"entity_id": page.entity_id, "key": key})
                seen.add(key)
            report["contradictions"].extend(page.contradictions)
        referenced = {
            item.get("entity_id") for page in pages.values() for item in page.related_entities
        }
        report["orphans"] = sorted(item for item in referenced if item and item not in pages)
        report["valid"] = report.valid
        return report

    def render_markdown(self, page: KnowledgePage) -> str:
        lines = [
            f"# {page.identity.get('name', page.entity_id)}",
            "",
            f"- Entity type: `{page.entity_type}`",
            f"- Entity ID: `{page.entity_id}`",
            f"- Confidence: `{page.overall_confidence_pct}% ({page.overall_confidence_label})`",
            f"- Updated: `{_stamp(page.updated_at)}`",
            "",
        ]
        self._section(lines, "Known facts", page.known_facts)
        self._section(
            lines,
            "Evidence categories",
            [{"category": key, **value} for key, value in sorted(page.category_confidence.items())],
        )
        self._section(lines, "Claims", [item.model_dump(mode="json") for item in page.claims])
        self._section(lines, "Evidence", page.evidence)
        self._section(lines, "Provenance", page.provenance)
        self._section(lines, "Historical information", page.historical_information)
        self._section(lines, "Contradictions", page.contradictions)
        self._section(lines, "Related entities", page.related_entities)
        self._section(lines, "Revision history", page.revisions)
        return "\n".join(lines).rstrip() + "\n"

    def _write(self, page: KnowledgePage) -> None:
        path = self.page_path(page.entity_type, page.entity_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = page.model_dump(mode="json")
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        self.markdown_path(page.entity_type, page.entity_id).write_text(
            self.render_markdown(page), encoding="utf-8"
        )

    @staticmethod
    def _merge_evidence(
        existing: list[dict[str, Any]], additions: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        merged = {
            str(item.get("evidence_id") or item.get("canonical_url") or _json(item)): item
            for item in existing
        }
        for item in additions:
            key = str(item.get("evidence_id") or item.get("canonical_url") or _json(item))
            if key not in merged:
                merged[key] = item
        return [merged[key] for key in sorted(merged)]

    @staticmethod
    def _merge_claims(
        existing: list[KnowledgeClaim], additions: list[KnowledgeClaim]
    ) -> list[KnowledgeClaim]:
        merged = {item.claim_id: item for item in existing}
        for item in additions:
            if item.claim_id in merged:
                prior = merged[item.claim_id]
                prior.evidence_ids = sorted(set(prior.evidence_ids + item.evidence_ids))
                prior.confidence = max(prior.confidence, item.confidence)
                prior.updated_at = datetime.now(timezone.utc)
            else:
                merged[item.claim_id] = item
        return [merged[key] for key in sorted(merged)]

    def _reconcile(self, page: KnowledgePage) -> KnowledgePage:
        evidence_ids = {str(item.get("evidence_id")) for item in page.evidence}
        unbound_evidence_ids = {
            str(item.get("evidence_id"))
            for item in page.evidence
            if item.get("attribution_status") == "unbound"
        }
        now = datetime.now(timezone.utc)
        for claim in page.claims:
            claim.evidence_ids = sorted(set(claim.evidence_ids))
            if not claim.evidence_ids:
                claim.status = ClaimStatus.UNKNOWN
            elif (
                claim.status == ClaimStatus.UNKNOWN
                and not set(claim.evidence_ids) & unbound_evidence_ids
            ):
                claim.status = ClaimStatus.SUPPORTED
            if claim.status != ClaimStatus.HISTORICAL:
                times = [
                    item.get("retrieved_at")
                    for item in page.evidence
                    if item.get("evidence_id") in claim.evidence_ids
                ]
                if times and all(
                    _stamp(t) < (now - timedelta(days=self.stale_after_days)).isoformat()
                    for t in times
                ):
                    claim.status = ClaimStatus.STALE
            if any(item not in evidence_ids for item in claim.evidence_ids):
                claim.status = ClaimStatus.UNKNOWN
        page.contradictions = self._find_contradictions(page.claims)
        return page

    @staticmethod
    def _find_contradictions(claims: list[KnowledgeClaim]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for left in claims:
            for right in claims:
                if left.claim_id >= right.claim_id:
                    continue
                left_text = left.statement.casefold()
                right_text = right.statement.casefold()
                if (
                    left_text.replace("not ", " ").strip() == right_text.strip()
                    or right_text.replace("not ", " ").strip() == left_text.strip()
                ):
                    result.append(
                        {
                            "claim_ids": [left.claim_id, right.claim_id],
                            "reason": "opposing claim text",
                        }
                    )
        return result

    @staticmethod
    def _section(lines: list[str], title: str, values: list[dict[str, Any]]) -> None:
        lines.extend([f"## {title}", ""])
        if not values:
            lines.extend(["_None recorded._", ""])
            return
        for value in sorted(values, key=lambda item: _json(item)):
            lines.extend([f"- `{_json(value)}`", ""])
