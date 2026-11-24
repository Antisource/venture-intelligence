"""Provider-neutral conversational analyst boundary.

The analyst may summarize already-collected JSON evidence, but it is never a
source of truth. The default provider is a deterministic local boundary; an
optional local Qwen model can be selected through Ollama without adding a
hosted-model or OpenAI dependency.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

from venture_intelligence.config import Settings, settings


class EpistemicState(str, Enum):
    KNOWN = "known"
    HYPOTHESIZED = "hypothesized"
    UNKNOWN = "unknown"
    CONTRADICTED = "contradicted"
    UNAVAILABLE = "unavailable"


class AnalystResult(BaseModel):
    """JSON-native response returned by every analyst provider."""

    answer: str
    model: str
    evidence_sufficient: bool = False
    epistemic_state: EpistemicState = EpistemicState.UNKNOWN
    caveats: list[str] = Field(default_factory=list)
    citations: list[dict[str, str]] = Field(default_factory=list)


class ConversationalModelProvider(Protocol):
    """Provider contract for local or future model runtimes."""

    name: str

    async def answer(self, question: str, context: dict[str, Any]) -> AnalystResult:
        """Analyze structured context without inventing evidence."""


class LocalBoundaryProvider:
    """Deterministic grounded fallback; it never uses general model knowledge."""

    name = "local-boundary"

    async def answer(self, question: str, context: dict[str, Any]) -> AnalystResult:
        entities = context.get("entities", [])[:5]
        if entities:
            lines = []
            for record in entities:
                entity = record["entity"]
                claims = "; ".join(item["claim"] for item in record["evidence"][:4])
                lines.append(
                    f"{entity['canonical_name']} — recorded program "
                    f"{entity.get('program_id') or 'unknown'}, "
                    f"cohort {entity.get('batch_id') or 'unknown'}. Source evidence: {claims}. "
                    "This recorded evidence does not establish membership in today's latest cohort."
                )
            return AnalystResult(
                answer="\n".join(lines),
                model=self.name,
                evidence_sufficient=True,
                epistemic_state=EpistemicState.HYPOTHESIZED,
                citations=context.get("citations", []),
                caveats=[
                    "Deterministic synthesis of the Research evidence view; not new evidence."
                ],
            )
        candidates = list(context.get("candidates", []))[:8]
        changes = list(context.get("changes", []))[:8]
        if candidates:
            lines: list[str] = []
            citations: list[dict[str, str]] = []
            for item in candidates[:5]:
                confidence = item.get("confidence_pct", 0)
                state = item.get("state", "unreviewed")
                epistemic = EpistemicState(
                    item.get("epistemic_state", EpistemicState.HYPOTHESIZED.value)
                )
                name = item.get("display_name", "Candidate")
                if epistemic is EpistemicState.KNOWN:
                    statement = f"{name} is officially confirmed for the target batch."
                elif epistemic is EpistemicState.CONTRADICTED:
                    statement = f"Current evidence conflicts on {name}'s target-batch status."
                elif epistemic is EpistemicState.UNAVAILABLE:
                    statement = f"Relevant sources for {name} were not successfully checked."
                else:
                    statement = (
                        f"Evidence suggests {name} is a candidate, but target-batch status "
                        "is not confirmed."
                    )
                lines.append(
                    f"{statement} Current batch confidence is {confidence}% ({state}). "
                    f"{item.get('reason', 'Inspect the underlying evidence.')}"
                )
                for source in item.get("sources", [])[:2]:
                    citations.append(
                        {
                            "label": str(source.get("label") or source.get("url")),
                            "url": str(source.get("url")),
                        }
                    )
            if changes:
                lines.append(
                    "Recent material changes: "
                    + "; ".join(
                        f"{item.get('display_name')}: {item.get('delta_pct', 0):+d} points"
                        for item in changes[:3]
                    )
                )
            return AnalystResult(
                answer="\n".join(lines),
                model=self.name,
                evidence_sufficient=True,
                epistemic_state=(
                    EpistemicState.KNOWN
                    if all(item.get("epistemic_state") == "known" for item in candidates[:5])
                    else EpistemicState.CONTRADICTED
                    if all(item.get("epistemic_state") == "contradicted" for item in candidates[:5])
                    else EpistemicState.HYPOTHESIZED
                ),
                caveats=[
                    "This deterministic summary uses only canonical application data; "
                    "inspect citations before acting."
                ],
                citations=citations,
            )
        unavailable = bool(context.get("important_sources_unavailable"))
        return AnalystResult(
            answer=(
                "Relevant acquisition sources were not successfully checked, so absence "
                "cannot be treated as negative evidence."
                if unavailable
                else "The system has no evidence establishing this claim. Run Discovery "
                "or import analyst-supplied evidence first."
            ),
            model=self.name,
            evidence_sufficient=False,
            epistemic_state=(EpistemicState.UNAVAILABLE if unavailable else EpistemicState.UNKNOWN),
            caveats=["No conversational model knowledge was substituted for missing evidence."],
        )


class OllamaQwenProvider:
    """Optional local Qwen provider using Ollama's JSON response mode."""

    name = "qwen-ollama"

    def __init__(self, runtime_settings: Settings = settings) -> None:
        self.settings = runtime_settings

    async def answer(self, question: str, context: dict[str, Any]) -> AnalystResult:
        prompt = (
            "You are a cautious venture analyst. Analyze only the supplied JSON evidence. "
            "Do not invent sources, entities, events, or confidence. If evidence is absent, "
            "say so. Preserve every supplied entity batch_id verbatim. Program IDs such as "
            "yc_current are registry keys, not batch names or proof of current membership. "
            "Never relabel recorded cohorts as current/latest. Return only JSON with keys "
            "answer (string), evidence_sufficient (boolean), "
            "and caveats (array of strings).\n\n"
            f"question: {question}\ncontext: {json.dumps(context, default=str)[:12000]}"
        )
        async with httpx.AsyncClient(timeout=self.settings.ollama_timeout_seconds) as client:
            response = await client.post(
                f"{self.settings.ollama_base_url.rstrip('/')}/api/generate",
                json={
                    "model": self.settings.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": 0,
                        "num_predict": self.settings.ollama_output_tokens,
                    },
                },
            )
            response.raise_for_status()
        payload = json.loads(response.json().get("response", "{}"))
        return AnalystResult(
            answer=str(payload.get("answer", "Evidence is insufficient for a grounded answer.")),
            model=self.name,
            evidence_sufficient=bool(payload.get("evidence_sufficient", False)),
            epistemic_state=EpistemicState.HYPOTHESIZED,
            caveats=[str(item) for item in payload.get("caveats", []) if str(item).strip()],
            citations=[
                {
                    "label": str(item.get("label", item.get("url", "Evidence"))),
                    "url": str(item.get("url", "")),
                }
                for item in context.get("citations", [])
                if isinstance(item, dict)
                and str(item.get("url", "")).startswith(("http://", "https://"))
            ],
        )


class AnalystAgent:
    """Select a configured provider and fail safely back to the local boundary."""

    def __init__(
        self,
        runtime_settings: Settings = settings,
        provider: ConversationalModelProvider | None = None,
    ) -> None:
        self.settings = runtime_settings
        self.fallback = LocalBoundaryProvider()
        self.provider = provider or self._provider_from_settings()

    def _provider_from_settings(self) -> ConversationalModelProvider:
        if self.settings.analyst_uses_ollama:
            return OllamaQwenProvider(self.settings)
        return self.fallback

    async def _ollama_available(self) -> bool:
        try:
            async with httpx.AsyncClient(
                timeout=min(self.settings.ollama_timeout_seconds, 1.5)
            ) as client:
                response = await client.get(f"{self.settings.ollama_base_url.rstrip('/')}/api/tags")
                response.raise_for_status()
            names = {
                str(item.get("name", "")).split(":latest", 1)[0]
                for item in response.json().get("models", [])
            }
            configured = self.settings.ollama_model.split(":latest", 1)[0]
            return configured in names
        except (httpx.HTTPError, ValueError, TypeError):
            return False

    async def answer(self, question: str, context: dict[str, Any] | None = None) -> AnalystResult:
        grounded_context = context or {}
        has_evidence = any(
            grounded_context.get(key) for key in ("candidates", "changes", "citations")
        )
        if not has_evidence:
            return await self.fallback.answer(question, grounded_context)
        if (
            self.settings.analyst_provider.casefold() == "auto"
            and not await self._ollama_available()
        ):
            result = await self.fallback.answer(question, grounded_context)
            result.caveats.append(
                "Local Ollama/Qwen was not detected; deterministic grounded analysis used."
            )
            return result
        try:
            result = await self.provider.answer(question, grounded_context)
            if not self._grounded_result(result, grounded_context):
                fallback = await self.fallback.answer(question, grounded_context)
                fallback.caveats.append(
                    "Local model output failed citation/epistemic validation and was discarded."
                )
                return fallback
            states = {
                str(item.get("epistemic_state", "hypothesized"))
                for item in grounded_context.get("candidates", [])
            }
            result.epistemic_state = (
                EpistemicState.KNOWN
                if states == {"known"}
                else EpistemicState.CONTRADICTED
                if states == {"contradicted"}
                else EpistemicState.HYPOTHESIZED
            )
            return result
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError, json.JSONDecodeError):
            result = await self.fallback.answer(question, grounded_context)
            result.caveats.append(
                "Configured Ollama/Qwen was unavailable; deterministic grounded fallback used."
            )
            return result

    @staticmethod
    def _grounded_result(result: AnalystResult, context: dict[str, Any]) -> bool:
        entities = context.get("entities", [])
        if entities:
            # Conservative temporal boundary: free-form inference cannot establish
            # current membership from a recorded cohort. Even a negated ambiguous
            # phrase falls back to the exact canonical summary.
            if re.search(
                r"\b(?:current|latest|today['’]s)[\s'’\"]*(?:batch|cohort)\b",
                result.answer,
                re.IGNORECASE,
            ):
                return False
            for record in entities:
                batch = record.get("entity", {}).get("batch_id")
                if batch and not re.search(
                    r"(?<!\w)" + re.escape(batch) + r"(?!\w)", result.answer, re.I
                ):
                    return False
        allowed_urls = {
            str(item.get("url")) for item in context.get("citations", []) if isinstance(item, dict)
        }
        if any(item.get("url") not in allowed_urls for item in result.citations):
            return False
        confirmed = any(
            item.get("epistemic_state") == "known" for item in context.get("candidates", [])
        )
        if not confirmed and re.search(
            r"\b(?:is in|is a member of|officially confirmed|confirmed for)\b",
            result.answer,
            re.IGNORECASE,
        ):
            return False
        answer_urls = set(re.findall(r"https?://[^\s)\]>]+", result.answer))
        return answer_urls <= allowed_urls

    async def health(self) -> dict[str, Any]:
        """Check configured local inference without exposing configuration secrets."""
        if not self.settings.analyst_enabled or self.settings.analyst_provider.casefold() not in {
            "qwen",
            "auto",
        }:
            return {
                "status": "HEALTHY",
                "provider": "local-boundary",
                "model": None,
                "action": (
                    "Set ANALYST_ENABLED=true and ANALYST_PROVIDER=qwen to use optional "
                    "local Ollama/Qwen."
                ),
            }
        try:
            async with httpx.AsyncClient(
                timeout=min(self.settings.ollama_timeout_seconds, 5)
            ) as client:
                response = await client.get(f"{self.settings.ollama_base_url.rstrip('/')}/api/tags")
                response.raise_for_status()
            names = {
                str(item.get("name", "")).split(":latest", 1)[0]
                for item in response.json().get("models", [])
            }
            configured = self.settings.ollama_model
            present = configured in names or configured.split(":latest", 1)[0] in names
            return {
                "status": "HEALTHY" if present else "MODEL_MISSING",
                "provider": "ollama",
                "model": configured,
                "action": None if present else f"Run ollama pull {configured}.",
            }
        except (httpx.HTTPError, ValueError, TypeError):
            auto = self.settings.analyst_provider.casefold() == "auto"
            return {
                "status": "HEALTHY" if auto else "UNAVAILABLE",
                "provider": "local-boundary" if auto else "ollama",
                "model": self.settings.ollama_model if not auto else None,
                "augmentation": "ollama_unavailable" if auto else None,
                "action": (
                    "Deterministic analysis is active; start Ollama for optional Qwen augmentation."
                    if auto
                    else "Start Ollama and verify OLLAMA_BASE_URL."
                ),
            }


__all__ = [
    "AnalystAgent",
    "AnalystResult",
    "ConversationalModelProvider",
    "EpistemicState",
    "LocalBoundaryProvider",
    "OllamaQwenProvider",
]
