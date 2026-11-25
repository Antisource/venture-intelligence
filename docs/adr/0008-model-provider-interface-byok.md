# ADR-0008: Model provider interface and bring-your-own-key

- **Status:** Proposed
- **Date:** 2026-11-23
- **Related:** ADR-0002, ADR-0004, ADR-0006, ADR-0007, D-020, D-021

## Context

- **`agents/analyst.py`** already defines a `ConversationalModelProvider` protocol
  (`:41`), a deterministic `LocalBoundaryProvider` (`:50`) and an `OllamaQwenProvider`
  (`:153`). `ANALYST_PROVIDER=auto` picks between them (`:218-250`). The analyst assigns
  epistemic states and discards unsupported claims (P-240).
- **`agents/environmental_extractor.py`** runs optional Ollama extraction with a regex
  fallback (P-201).
- **AGENTS.md pins the default model** to `qwen2.5-coder:3b`, with a custom Modelfile
  (P-270). The Python default, the Modelfile and the README must stay consistent.
- **Settings:** `OLLAMA_*` (P-330–P-335), `ANALYST_ENABLED` and `ANALYST_PROVIDER`
  (P-336, P-337). There are no cloud providers yet, and there are two analyst-context
  builders (FINDINGS §10).
- **The owner requires:**
  - pluggable models: local Ollama or the user's own key;
  - a deterministic fallback;
  - model output that is never authoritative until checked against evidence;
  - keys kept server-side;
  - open-source dependencies only;
  - an honest UI.

  LANDSCAPE §3: "your key, your cost, zero markup" is a differentiator.

## Decision

1. **A port in `ports/models.py`.**
   - `generate_structured(task, messages, output_schema, budget) -> ModelResult`. The result
     is either parsed output or a typed failure (`timeout`, `invalid_json`,
     `schema_violation`, `refused`, `rate_limited`, `auth_failed`, `provider_error`), plus
     usage (tokens, latency), provider, model and prompt digest.
   - `health()` and `capabilities`: JSON-schema mode, tool calling, context window, and
     whether the model is local or remote.
   - `stream_chat` is optional, for the analyst.
2. **Adapters** use httpx, already a dependency; no vendor SDK is required:
   - **`deterministic`:** always available. It wraps today's `LocalBoundaryProvider` and
     the regex extractor.
   - **`ollama`** (MIT server) is the local default: the native `/api/chat` with `format`
     set to a JSON schema. The default model becomes `granite3.3:2b` (IBM Granite,
     Apache-2.0; verify the Ollama tag), because the `qwen2.5-coder:3b` weights are under
     the non-OSI Qwen Research License (D-021). Qwen stays optional: users may set
     `OLLAMA_MODEL=qwen2.5-coder:3b` or build the Qwen-based custom Modelfile.
   - **`openai_compatible`:** Chat Completions with `response_format: json_schema` where
     the provider supports it; otherwise JSON mode plus validation. It covers OpenAI, Azure
     OpenAI, Mistral, Groq, OpenRouter, vLLM (Apache-2.0), the llama.cpp server (MIT),
     LM Studio, and Gemini's OpenAI-compatible endpoint (verify each).
   - **`anthropic`:** the Messages API, using tool use for structured output.
3. **Routing.** Workspace `model_profiles` map task classes (`extract`, `summarize`,
   `analyst_chat`, `query_plan`) to a provider and model, with a fallback chain that always
   ends in `deterministic`. `ANALYST_PROVIDER=auto` keeps its meaning, and `qwen` stays an
   alias for `ollama`. Budgets:
   - a per-call timeout (generalising P-333);
   - input and output caps (P-334, P-335);
   - a per-workspace daily token cap.

   Costs are shown in tokens, with an optional user-entered unit price: no markup and no
   metering service.
4. **Evidence validation is mandatory (D-020).**
   - **Inputs:** only evidence items with ids, built by the single context builder, each
     carrying its attribution status. Unbound items are labelled and cannot support
     claims.
   - **Outputs:** every claim must cite evidence ids. The validator rejects a claim that:
     - cites an id outside the context, or cites unbound evidence;
     - quotes text that is not a normalised substring of the cited evidence;
     - states a number or date that does not appear in the cited evidence.
   - **Models never decide:** confidence, attribution, identity merges and scores are
     never set by a model. Confidence comes from deterministic rules over validated
     support (ADR-0002).
   - **Extractions are candidates, not facts.** They pass the identity gate (P-227),
     carry provenance `model_extracted_from=<observation id>` and inherit the source
     page's `origin_key`. They are never independent origins.
   - **Every call is recorded** in the append-only `model_runs`: provider, model, prompt
     template version, prompt digest, input evidence ids, output (erasable), validation
     outcome, and any fallback.
   - **The UI is honest about authorship.** It says "drafted by <model>, checked against
     N cited sources" or "deterministic summary".
5. **BYOK keys stay on the server.**
   - Keys come from `.env` as `SecretStr` (local) or from encrypted workspace secrets
     (hosted; ADR-0006).
   - Keys are never sent to the browser (only `configured` and the last four
     characters), never put in job payloads (payloads carry `model_profile_id`), and never
     logged: an httpx hook redacts `Authorization` and `x-api-key`, and so does structlog.
   - **SSRF guard:** in hosted mode, a user-supplied `base_url` must be https and must not
     resolve to a private, loopback or link-local address unless an admin allowlists it,
     for example for an internal Ollama.
   - **Data notice:** selecting a remote provider shows what leaves the deployment. A
     workspace setting can forbid sending people-level data to remote providers.
6. **The evaluation harness** is `vi eval models`, also available as a pytest marker.
   - **Datasets:** versioned golden JSONL files in `tests/fixtures/model_eval/`, each case
     holding input evidence, expected extractions and claims, and forbidden claims.
   - **Metrics** follow the evaluation package convention (numerator, denominator, sample
     size, definition):
     - schema-valid rate;
     - hallucination rate before validation;
     - accepted unsupported claims (must be 0);
     - extraction precision and recall;
     - fallback rate;
     - p50 and p95 latency;
     - tokens.
   - **Offline CI** runs the harness against `deterministic` and against recorded
     responses for every adapter. Live runs need `VENTURE_INTELLIGENCE_LIVE_TESTS=1` (P-353)
     plus a key or a local Ollama.
   - Changing the default model or provider requires a recorded evaluation run.

## Alternatives considered

- **LiteLLM** (MIT core; its `enterprise/` directory is under a commercial licence):
  more than 100 providers and price tables, but a large, fast-moving dependency tree, and
  the licence split needs care. Possible later as one adapter behind the port.
- **LangChain or LlamaIndex (MIT):** breadth, but heavy abstractions and API churn when we
  need only structured output plus validation. Rejected.
- **Pydantic AI (MIT):** native Pydantic structured output across providers, a credible
  fit, but we would give up direct control of fallback and validation semantics for
  little gain. Deferred; it could back the remote adapters later.
- **Vendor SDKs (`openai`, Apache-2.0; `anthropic`, MIT):** official, but one dependency
  per vendor. Optional, not required.
- **Instructor (MIT):** convenient retry on schema failure, but it patches client SDKs,
  and our validator already covers its role. Rejected.

## Consequences

- **We maintain three remote adapters.** Recorded-response tests and the harness catch
  provider API drift.
- **The local default model changes** to an Apache-2.0 model of similar size (D-021),
  and the deterministic path keeps every feature usable with no model.
- **Migration steps:** (1) wrap the existing providers in the port, with no behaviour
  change; (2) the single context builder; (3) generalise the analyst's unsupported-claim
  discard into a shared validator; (4) `model_runs`; (5) BYOK settings and encrypted
  storage; (6) the OpenAI-compatible and Anthropic adapters; (7) the harness in CI;
  (8) switch the default model in one change to the Python default, `.env.example`, the
  README and AGENTS.md, documenting the Modelfile as the optional Qwen custom model.
- **Parity risks:** P-201, P-240, P-270, P-330, P-331 and P-333–P-337 must keep their
  meaning, and existing `.env` files must keep working. P-332 (the `OLLAMA_MODEL`
  default) is an intended parity change from `qwen2.5-coder:3b` to `granite3.3:2b`
  (D-021). Readiness (`operations/readiness.py`, `/readyz`, ADR-0004) and `vi doctor`
  report a missing configured model by name, with the pull command, instead of silently
  degrading to the deterministic extractor.
- **Default model licence (resolved, D-021):** the Hugging Face card for
  `Qwen/Qwen2.5-Coder-3B` lists the `qwen-research` licence, which is not
  OSI-compatible, so the default switches to `granite3.3:2b` (Apache-2.0) and Qwen stays
  optional. Existing `.env` files that set `OLLAMA_MODEL` keep their model.

## How parity is verified

- These tests pass unchanged:
  - `test_analyst_qwen_provider_is_optional_and_json_native`;
  - `test_extractor_returns_none_when_ollama_is_disabled`;
  - `test_extractor_rejects_malformed_ollama_json`;
  - `test_modelfile_uses_required_qwen_parameters`;
  - `test_email_preview_and_deterministic_analyst_are_truthful`;
  - `test_entity_scoped_assistant_refuses_global_list_question`.
- New tests check that:
  - a key never appears in API responses, logs or job payloads;
  - the SSRF guard refuses private addresses;
  - claims that are uncited, unbound or misquoted are rejected;
  - each failure type falls back to `deterministic`;
  - the offline harness passes in CI.
