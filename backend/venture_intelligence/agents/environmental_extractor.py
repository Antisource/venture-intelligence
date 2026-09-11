"""Bounded structured extraction using an optional local Ollama server."""

from __future__ import annotations

import json
import re

import httpx
from pydantic import BaseModel, ValidationError

from venture_intelligence.config import Settings, settings
from venture_intelligence.programs import get_program_registry


class EnvironmentalSignal(BaseModel):
    entity_name: str
    platform: str
    public_url: str
    bio_summary: str
    ecosystem_signals_detected: list[str]
    extraction_method_used: str


class EnvironmentalExtractor:
    """Use Ollama when enabled, otherwise fail open to a deterministic parser."""

    def __init__(self, runtime_settings: Settings = settings) -> None:
        self.settings = runtime_settings

    async def extract(
        self, text: str, platform: str, public_url: str
    ) -> EnvironmentalSignal | None:
        """Return only validated Ollama output; unavailable Ollama returns ``None``."""
        bounded = text[: self.settings.ollama_input_chars]
        if not bounded.strip():
            return None
        if not self.settings.ollama_enabled:
            return None
        return await self._ollama_extract(bounded, platform, public_url)

    async def extract_with_fallback(
        self, text: str, platform: str, public_url: str
    ) -> EnvironmentalSignal | None:
        """Use the deterministic fallback explicitly at the connector boundary."""
        bounded = text[: self.settings.ollama_input_chars]
        return await self.extract(bounded, platform, public_url) or self._regex_extract(
            bounded, platform, public_url
        )

    async def _ollama_extract(
        self, text: str, platform: str, public_url: str
    ) -> EnvironmentalSignal | None:
        prompt = (
            "Return ONLY one valid JSON object and never use null. Use exactly these keys "
            'and types: {"entity_name":"string","platform":"string",'
            '"public_url":"string","bio_summary":"string",'
            '"ecosystem_signals_detected":["string"]}. Copy the supplied platform '
            "and public_url exactly; use an empty array when no ecosystem signal is explicit. "
            "Extract only explicit public claims.\n\n"
            f"platform: {platform}\npublic_url: {public_url}\ntext:\n{text}"
        )
        try:
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
            payload.update(
                {"platform": platform, "public_url": public_url, "extraction_method_used": "ollama"}
            )
            return EnvironmentalSignal.model_validate(payload)
        except (httpx.HTTPError, ValueError, ValidationError, json.JSONDecodeError):
            return None

    @staticmethod
    def _regex_extract(text: str, platform: str, public_url: str) -> EnvironmentalSignal | None:
        name_match = re.search(
            r"(?:founder|ceo|building)\s+(?:at|@)\s+([A-Z][\w .'-]{1,80})", text, re.I
        )
        program_terms = {
            term.casefold()
            for program in get_program_registry().active()
            for term in (program.name, *program.keywords)
        }
        terms = {"accelerator", "pre-seed", "funded", *program_terms}
        signals = sorted(keyword for keyword in terms if keyword in text.casefold())
        if not name_match and not signals:
            return None
        return EnvironmentalSignal(
            entity_name=name_match.group(1).strip() if name_match else "Unknown",
            platform=platform,
            public_url=public_url,
            bio_summary=re.sub(r"\s+", " ", text)[:280],
            ecosystem_signals_detected=signals,
            extraction_method_used="regex_fallback",
        )
