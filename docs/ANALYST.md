# Conversational analyst

The analyst is a reasoning boundary over stored JSON evidence; it is not a web
crawler. `AnalystAgent` selects a provider through
`ConversationalModelProvider`. The default local boundary returns a useful,
explicit unavailable/uncertainty response without credentials. When enabled,
`OllamaQwenProvider` sends bounded JSON context to a local Qwen-compatible
Ollama server and validates the JSON response.

`ANALYST_PROVIDER=auto` keeps the deterministic grounded response available and
uses local Ollama when it is healthy. `ANALYST_PROVIDER=qwen` explicitly selects
the Qwen-compatible provider. The preferred model is `qwen2.5-coder:3b`, but the
configured model remains an operator choice. Missing, slow, or malformed model
responses fall back to deterministic synthesis. The JSON boundary is exposed at
`POST /api/analyst/chat`.

Every response includes an epistemic state:

- `known`: stored evidence supports an officially confirmed state;
- `hypothesized`: evidence suggests a candidate but identity or batch remains
  unconfirmed;
- `unknown`: the store has no evidence establishing the claim;
- `contradicted`: current evidence conflicts;
- `unavailable`: an applicable source was not successfully checked.

Checked-with-no-evidence is distinct from unavailable/not-checked. The analyst
never converts source failure into a negative claim. Candidate context includes
server-derived `why_now`, promotion blockers, next checks, coverage,
contradictions, investment attention, and evidence URLs. The deterministic
boundary uses calibrated wording and never turns a percentage into a statement
of membership.

Optional Ollama text is non-authoritative. URLs must belong to the supplied
citation set, and confirmation language is rejected when no supplied candidate
is confirmed. Output that fails these checks is discarded in favor of the
deterministic response. Original observations are never rewritten by analyst or
CRM feedback.

No OpenAI SDK or paid model is required. Live Ollama validation depends on the
operator's local model and is reported separately from deterministic tests.
