# D-020: Own thin model-provider port over httpx; models never set confidence

- **Date:** 2026-11-23
- **Area:** Model providers
- **Differs from owner instruction:** No (adds the abstraction choice and validation rules)

1. **Chosen:** a `ModelProvider` port with four adapters written on httpx, with no
   vendor SDK required:
   - `deterministic`, which is always available;
   - `ollama`, the default, with the Apache-2.0 `granite3.3:2b` as its default model and
     `qwen2.5-coder:3b` optional (D-021);
   - `openai_compatible`;
   - `anthropic`.

   Every fallback chain ends in `deterministic`. Output is accepted only when every claim
   cites in-context, attached evidence and its quotes, numbers and dates match. Models
   never set confidence, attribution, merges or scores. Every call is logged in
   append-only `model_runs`. BYOK keys stay server-side, encrypted in hosted mode, and are
   never sent to the browser, put in job payloads or logged. In hosted mode an SSRF guard
   restricts custom `base_url`s. See ADR-0008.
2. **Owner instruction:** pluggable models (local Ollama or the user's own key);
   deterministic fallback; model output never authoritative without evidence validation;
   open-source dependencies only.
3. **Why:** we need structured output, typed failures, fallback and validation, not a
   framework. LiteLLM, LangChain and Pydantic AI add large or fast-moving dependency
   trees, or take over the fallback semantics we must control. Pydantic AI stays a
   credible later backing for the remote adapters.
   **Trade-off:** we maintain three remote adapters ourselves. Recorded-response tests and
   the evaluation harness catch provider API drift.
4. **Parity:** the existing analyst and extractor tests (P-201, P-240, P-270) pass
   unchanged. The `OLLAMA_*`, `ANALYST_ENABLED` and `ANALYST_PROVIDER` variables
   (P-330, P-331, P-333–P-337) keep their meaning, and `qwen` stays an alias for `ollama`.
   P-332, the `OLLAMA_MODEL` default, is an intended parity change to `granite3.3:2b`,
   with readiness and `vi doctor` reporting a missing model explicitly (D-021).
