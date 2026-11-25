# Feature-parity checklist (revamp contract)

Baseline: HEAD `c563225` (product code identical to `main` `88609e6`). Each row is a
user-visible capability the revamp must preserve, move, or retire through a recorded
decision in `DECISIONS.md`. "Current entry point" is `path:line` at the baseline. "How
to verify" names an existing pytest function (`tests/*.py`) or a vitest `it(...)` title
(`frontend/src/test/*`). "needs test" means no existing test exercises the capability at
that entry point; write one before rewriting it.

Totals: 353 items (278 capabilities + 75 environment variables). 154 items are marked
"needs test" (80 capabilities, 74 env vars), and 7 more capabilities are only partly
covered ("needs test" with a related test noted), so 161 items need new tests, 87 of them
outside the env-var rows.

| Area | Items |
|---|---:|
| API: app shell (/, SPA fallback, /frontend, OpenAPI/docs, CORS, lifespan) | 6 |
| API: legacy /api | 28 |
| API: WebSocket | 2 |
| API: /api/v1 (incl. SSE aliases) | 29 |
| API: batch discovery | 5 |
| CLI: venture-intelligence | 33 |
| CLI: modules/scripts/CI | 12 |
| UI: routes / shell / Inbox / Discover / Portfolio / Research / Graph / Decisions / System | 17 / 5 / 5 / 5 / 1 / 12 / 5 / 5 / 14 |
| Connectors (monitor) | 17 |
| Research providers + crawler | 14 |
| Batch discovery | 4 |
| Jobs / loops | 7 |
| Pipeline / domain | 18 |
| Notifications | 10 |
| Exports / artifacts | 6 |
| Replay / evaluation / graph services | 6 |
| Config files / state | 12 |
| Env vars (72 Settings fields + EMAIL_SMTP_STARTTLS/SMTP_* aliases + VENTURE_INTELLIGENCE_LIVE_TESTS) | 75 |

Notes for the contract:

- Legacy `/api/*` routes must keep working (AGENTS.md) even where no UI consumes them.
- Rows marked "not registered" or "not wired" (Firecrawl, ClassificationWorker) and the
  dead items in FINDINGS §10 need an explicit keep/retire decision, not silent loss.
- Untested API routes: /api/health, /api/stats, /api/founders/{id}, /api/alerts,
  /api/crm/{id}/advance, /api/crm/{id}/note, /api/programs, /api/metrics,
  /api/knowledge/stream/{id}, /api/events/stream, /api/v1/events/stream,
  /api/settings/email/digest, /api/ws, /api/v1/cycles, /api/v1/cycles/{id},
  /api/v1/cycles/run, /api/v1/scheduler, and /api/v1/research (OpenAPI-only coverage).
- The S0 parity suite turns each "needs test" row into an executable check before the
  code behind it is rewritten.

| ID | Area | Capability | Current entry point | How to verify |
|---|---|---|---|---|
| P-001 | API: app shell | GET / serves React production build (503 with guidance if dist missing) | `backend/venture_intelligence/api/app.py:326` | test_port_8000_root_is_react_and_legacy_is_explicit; test_frontend_is_served |
| P-002 | API: app shell | GET /{spa_path} redirects clean HTML links (e.g. /founders, /research/x?q) to hash routes /#/...; 404 for api/ and non-HTML | `backend/venture_intelligence/api/app.py:915` | test_clean_react_deep_links_preserve_research_and_filter_context |
| P-003 | API: app shell | Static mount /frontend/* (whole frontend dir, incl. dist assets) | `backend/venture_intelligence/api/app.py:87` | test_frontend_is_served |
| P-004 | API: app shell | FastAPI generated GET /openapi.json, /docs, /redoc | `backend/venture_intelligence/api/app.py:73` | test_versioned_api_is_in_openapi |
| P-005 | API: app shell | CORS middleware (all origins, GET/POST/PUT) | `backend/venture_intelligence/api/app.py:79` | needs test |
| P-006 | API: app shell | Lifespan initialises Database + CRMWorkflow; 503 when uninitialised | `backend/venture_intelligence/api/app.py:63` | needs test |
| P-007 | API: legacy /api | GET /api/health -> {status, project, db} | `backend/venture_intelligence/api/app.py:337` | needs test |
| P-008 | API: legacy /api | GET /api/stats -> counts of events/companies/persons/signals/alerts | `backend/venture_intelligence/api/app.py:346` | needs test |
| P-009 | API: legacy /api | GET /api/events?limit (1-100) recent canonical events | `backend/venture_intelligence/api/app.py:143` | test_legacy_graph_investigation_and_websocket_surfaces |
| P-010 | API: legacy /api | GET /api/evaluation legacy evaluation rows | `backend/venture_intelligence/api/app.py:155` | test_legacy_graph_investigation_and_websocket_surfaces |
| P-011 | API: legacy /api | POST /api/analyst/chat {question<=2000, context_name} grounded analyst; cross-entity refusal when scoped | `backend/venture_intelligence/api/app.py:161` | test_entity_scoped_assistant_refuses_global_list_question; test_legacy_graph_investigation_and_websocket_surfaces |
| P-012 | API: legacy /api | GET /api/founders?limit&offset integrity-gated ranked list | `backend/venture_intelligence/api/app.py:359` | test_complete_lineage_and_typed_api_are_integrity_gated |
| P-013 | API: legacy /api | GET /api/review-queue alias of founders list | `backend/venture_intelligence/api/app.py:391` | test_rejected_candidate_is_json_auditable_and_never_reviewed |
| P-014 | API: legacy /api | GET /api/founders/{entity_id} entity+ranking+signals+evidence+events (404 if not validated) | `backend/venture_intelligence/api/app.py:397` | needs test |
| P-015 | API: legacy /api | GET /api/alerts?limit recent alerts (used by Inbox) | `backend/venture_intelligence/api/app.py:417` | needs test |
| P-016 | API: legacy /api | GET /api/crm CRM board by stage with canonical joins | `backend/venture_intelligence/api/app.py:422` | test_typed_decision_api_is_integrity_gated |
| P-017 | API: legacy /api | POST /api/crm/{entity_id}/advance {to_stage, partner, note} | `backend/venture_intelligence/api/app.py:436` | needs test |
| P-018 | API: legacy /api | POST /api/crm/{entity_id}/note {note, partner} | `backend/venture_intelligence/api/app.py:449` | needs test |
| P-019 | API: legacy /api | GET /api/programs unversioned alias of v1 programs | `backend/venture_intelligence/api/app.py:457` | needs test |
| P-020 | API: legacy /api | GET /api/metrics in-process metrics snapshot | `backend/venture_intelligence/api/app.py:463` | needs test |
| P-021 | API: legacy /api | GET /api/knowledge/validate knowledge page/claim reference validation | `backend/venture_intelligence/api/app.py:470` | test_knowledge_and_email_api_surfaces |
| P-022 | API: legacy /api | GET /api/knowledge/stream/{entity_id} bounded SSE (12 frames, 256KB cap) | `backend/venture_intelligence/api/app.py:476` | needs test (v1 alias covered) |
| P-023 | API: legacy /api | GET /api/events/stream bounded dashboard revision SSE | `backend/venture_intelligence/api/app.py:528` | needs test |
| P-024 | API: legacy /api | GET /api/knowledge/{entity_type}/{entity_id} KnowledgePage JSON | `backend/venture_intelligence/api/app.py:567` | test_knowledge_api_preserves_provenance_as_json |
| P-025 | API: legacy /api | GET /api/settings/email destination, preferences, deliveries, runtime status | `backend/venture_intelligence/api/app.py:575` | test_knowledge_and_email_api_surfaces |
| P-026 | API: legacy /api | PUT /api/settings/email {destination, enabled, preferences, frequency, minimum_confidence} | `backend/venture_intelligence/api/app.py:580` | test_knowledge_and_email_api_surfaces |
| P-027 | API: legacy /api | POST /api/settings/email/test send test email | `backend/venture_intelligence/api/app.py:598` | test_knowledge_and_email_api_surfaces |
| P-028 | API: legacy /api | POST /api/settings/email/digest {send:false} preview / {send:true} send partner digest | `backend/venture_intelligence/api/app.py:641` | needs test (CLI preview: test_email_preview_and_deterministic_analyst_are_truthful) |
| P-029 | API: legacy /api | GET /api/investigations/{entity_id} latest investigation | `backend/venture_intelligence/api/app.py:676` | test_legacy_graph_investigation_and_websocket_surfaces |
| P-030 | API: legacy /api | GET /api/investigations?limit recent investigations | `backend/venture_intelligence/api/app.py:682` | test_legacy_graph_investigation_and_websocket_surfaces |
| P-031 | API: legacy /api | GET /api/graph/{entity_id} first-hop neighbours | `backend/venture_intelligence/api/app.py:695` | test_legacy_graph_investigation_and_websocket_surfaces |
| P-032 | API: legacy /api | GET /api/graph?limit bounded safe graph snapshot | `backend/venture_intelligence/api/app.py:701` | test_graph_api_filters_generic_labels_at_the_boundary |
| P-033 | API: legacy /api | GET /api/companies/evidence?limit company rows with founders, categories, confidence, knowledge revision | `backend/venture_intelligence/api/app.py:727` | test_company_evidence_api_excludes_generic_records_and_returns_json |
| P-034 | API: legacy /api | GET /api/candidates/rejections?limit audit-only rejected candidates | `backend/venture_intelligence/api/app.py:813` | test_rejected_candidate_is_json_auditable_and_never_reviewed |
| P-035 | API: WebSocket | WS /ws connected/ping->pong/stats/ack; 120 msg, 512 char, 300s idle limits | `backend/venture_intelligence/api/app.py:877` | test_legacy_graph_investigation_and_websocket_surfaces |
| P-036 | API: WebSocket | WS /api/ws alias | `backend/venture_intelligence/api/app.py:878` | needs test |
| P-037 | API: /api/v1 | GET /api/v1/knowledge/stream/{entity_id} bounded knowledge SSE (used by Research evidence view) | `backend/venture_intelligence/api/app.py:477` | test_knowledge_sse_and_polling_snapshot; test_sse_headers_and_frame_budget |
| P-038 | API: /api/v1 | GET /api/v1/events/stream bounded dashboard SSE alias | `backend/venture_intelligence/api/app.py:529` | needs test |
| P-039 | API: /api/v1 | GET /api/v1/knowledge/{entity_id} polling snapshot + revision metadata | `backend/venture_intelligence/api/routers/v1.py:261` | test_knowledge_sse_and_polling_snapshot |
| P-040 | API: /api/v1 | GET /api/v1/programs active ProgramRegistry | `backend/venture_intelligence/api/routers/v1.py:270` | test_readiness_api_exposes_f26_without_secrets; test_versioned_api_is_in_openapi |
| P-041 | API: /api/v1 | GET /api/v1/sources providers, connectors, inventory, watchlists, runtime health, Product Hunt config | `backend/venture_intelligence/api/routers/v1.py:275` | test_source_policy_and_temporal_contract_endpoints; test_product_hunt_configured_failure_stays_configured_and_unavailable |
| P-042 | API: /api/v1 | PUT /api/v1/sources/{source_id} {enabled} operator override | `backend/venture_intelligence/api/routers/v1.py:402` | test_default_source_settings_and_fresh_email_are_truthful |
| P-043 | API: /api/v1 | GET /api/v1/source-quality/contract live-validation gate description | `backend/venture_intelligence/api/routers/v1.py:424` | test_source_quality_api_is_read_only_and_typed |
| P-044 | API: /api/v1 | GET /api/v1/source-quality latest persisted live report (never triggers network) | `backend/venture_intelligence/api/routers/v1.py:450` | test_source_quality_api_is_read_only_and_typed; test_source_quality_api_reports_corrupt_state_without_crashing |
| P-045 | API: /api/v1 | GET /api/v1/system/readiness component readiness without secrets | `backend/venture_intelligence/api/routers/v1.py:471` | test_readiness_api_exposes_f26_without_secrets; test_readiness_classifies_optional_integrations_without_secrets |
| P-046 | API: /api/v1 | GET /api/v1/cycles operations state (scheduler + cycles) | `backend/venture_intelligence/api/routers/v1.py:477` | needs test |
| P-047 | API: /api/v1 | GET /api/v1/cycles/{cycle_id} one cycle record (404) | `backend/venture_intelligence/api/routers/v1.py:482` | needs test |
| P-048 | API: /api/v1 | POST /api/v1/cycles/run {program_ids} run one locked cycle (409 if running) | `backend/venture_intelligence/api/routers/v1.py:491` | needs test (service: test_cycle_rejects_unknown_program_before_claiming_success) |
| P-049 | API: /api/v1 | PUT /api/v1/scheduler {enabled} toggle scheduler flag | `backend/venture_intelligence/api/routers/v1.py:502` | needs test (service: test_scheduler_restart_overlap_failure_and_enable_disable) |
| P-050 | API: /api/v1 | GET /api/v1/system/temporal-contract occurred/observed/ingested/validity semantics | `backend/venture_intelligence/api/routers/v1.py:507` | test_source_policy_and_temporal_contract_endpoints |
| P-051 | API: /api/v1 | GET /api/v1/replay/contract replay CLI + suppressed side effects | `backend/venture_intelligence/api/routers/v1.py:543` | test_replay_api_is_read_only_and_openapi_typed |
| P-052 | API: /api/v1 | GET /api/v1/graph?as_of&program_id&node_types&min_confidence&focus_id&depth(0-2)&max_nodes(<=400)&max_edges(<=800) | `backend/venture_intelligence/api/routers/v1.py:554` | test_versioned_graph_api_validates_bounds_and_program; test_live_graph_contract_is_bounded_typed_and_integrity_gated; test_historical_graph_uses_isolated_observed_at_replay |
| P-053 | API: /api/v1 | GET /api/v1/evaluations/contract | `backend/venture_intelligence/api/routers/v1.py:593` | test_evaluation_api_is_typed_read_only_and_reports_provenance |
| P-054 | API: /api/v1 | GET /api/v1/evaluations list persisted evaluation runs | `backend/venture_intelligence/api/routers/v1.py:609` | test_evaluation_api_is_typed_read_only_and_reports_provenance |
| P-055 | API: /api/v1 | GET /api/v1/evaluations/{evaluation_run_id} | `backend/venture_intelligence/api/routers/v1.py:615` | test_evaluation_api_is_typed_read_only_and_reports_provenance |
| P-056 | API: /api/v1 | GET /api/v1/intelligence/rankings?as_of&entity_type&program_id&batch_id&limit(<=200) | `backend/venture_intelligence/api/routers/v1.py:624` | test_complete_lineage_and_typed_api_are_integrity_gated; test_program_ranking_limit_is_applied_after_program_filtering |
| P-057 | API: /api/v1 | GET /api/v1/intelligence/entities/{entity_id}?as_of lineage, evidence, movement, history | `backend/venture_intelligence/api/routers/v1.py:652` | test_complete_lineage_and_typed_api_are_integrity_gated |
| P-058 | API: /api/v1 | GET /api/v1/decisions?as_of&program_id decision board | `backend/venture_intelligence/api/routers/v1.py:673` | test_typed_decision_api_is_integrity_gated |
| P-059 | API: /api/v1 | GET /api/v1/decisions/entities/{entity_id}?as_of | `backend/venture_intelligence/api/routers/v1.py:687` | test_typed_decision_api_is_integrity_gated |
| P-060 | API: /api/v1 | POST /api/v1/decisions/entities/{entity_id}/actions typed append-only CRM action (idempotent action_id) | `backend/venture_intelligence/api/routers/v1.py:705` | test_typed_decision_api_is_integrity_gated; test_typed_action_is_append_only_idempotent_and_fully_lineaged |
| P-061 | API: /api/v1 | POST /api/v1/decisions/recommendations/{id}/lifecycle accepted\|dismissed\|completed | `backend/venture_intelligence/api/routers/v1.py:731` | test_typed_decision_api_is_integrity_gated |
| P-062 | API: /api/v1 | POST /api/v1/decisions/alerts/{id}/lifecycle acknowledged\|dismissed\|resolved | `backend/venture_intelligence/api/routers/v1.py:757` | test_typed_decision_api_is_integrity_gated |
| P-063 | API: /api/v1 | GET /api/v1/search?q&limit(<=30) programs + validated entities | `backend/venture_intelligence/api/routers/v1.py:783` | test_command_search_returns_programs_and_only_validated_entities |
| P-064 | API: /api/v1 | POST /api/v1/research bounded public research run -> ResearchRun, publishes to EventBus | `backend/venture_intelligence/api/routers/v1.py:836` | needs test (OpenAPI only: test_versioned_api_is_in_openapi; agent: test_agent_respects_query_budget_and_json_contract) |
| P-065 | API: /api/v1 | POST /api/v1/intel/gather preview corroboration across eligible connectors (no persistence) | `backend/venture_intelligence/api/routers/v1.py:876` | test_gather_api_reports_attempts_failures_and_candidate_rejection; test_gather_openapi_exposes_a_closed_status_enum |
| P-066 | API: batch discovery | GET /api/v1/batch-discovery/contract stages, taxonomy, source capabilities, tool benchmarks | `backend/venture_intelligence/api/routers/batch_discovery.py:164` | test_m8_api_contract_and_candidate_list |
| P-067 | API: batch discovery | GET /api/v1/batch-discovery?program_id&batch_id&include_rejected candidate list | `backend/venture_intelligence/api/routers/batch_discovery.py:181` | test_m8_api_contract_and_candidate_list; test_unscoped_candidate_listing_and_research_api_preserve_provenance |
| P-068 | API: batch discovery | POST /api/v1/batch-discovery/runs run discovery then drain canonical events | `backend/venture_intelligence/api/routers/batch_discovery.py:201` | test_m8_api_contract_and_candidate_list |
| P-069 | API: batch discovery | POST /api/v1/batch-discovery/manual-observations analyst-supplied observation | `backend/venture_intelligence/api/routers/batch_discovery.py:213` | test_m8_api_contract_and_candidate_list |
| P-070 | API: batch discovery | GET /api/v1/batch-discovery/{hypothesis_id} candidate detail + observations | `backend/venture_intelligence/api/routers/batch_discovery.py:235` | test_attributed_team_survives_persistence_research_api_and_replay |
| P-071 | CLI: venture-intelligence | Console script `venture-intelligence` -> cli:main | `pyproject.toml ([project.scripts])` | test_cli_has_one_cycle_and_one_readiness_job |
| P-072 | CLI: venture-intelligence | Global --json (machine output) and --database PATH | `backend/venture_intelligence/cli.py:175` | test_doctor_human_output_is_concise_but_json_preserves_diagnostics |
| P-073 | CLI: venture-intelligence | Exit codes 0 ok / 1 broken\|failed cycle\|storage / 2 user action\|lookup / 3 already running; storage errors redacted | `backend/venture_intelligence/cli.py:557` | test_cycle_cli_exit_codes_are_scheduler_safe; test_cli_storage_error_is_structured_without_traceback_or_secrets |
| P-074 | CLI: venture-intelligence | doctor (readiness + analyst health + db path) | `backend/venture_intelligence/cli.py:178` | test_doctor_human_output_is_concise_but_json_preserves_diagnostics |
| P-075 | CLI: venture-intelligence | programs list | `backend/venture_intelligence/cli.py:180` | needs test |
| P-076 | CLI: venture-intelligence | batches list --program | `backend/venture_intelligence/cli.py:182` | needs test |
| P-077 | CLI: venture-intelligence | sources status | `backend/venture_intelligence/cli.py:189` | test_cli_storage_error_is_structured_without_traceback_or_secrets |
| P-078 | CLI: venture-intelligence | sources check --program --allow-network --max-queries --max-requests (needs VENTURE_INTELLIGENCE_LIVE_TESTS=1) | `backend/venture_intelligence/cli.py:190` | test_live_gate_and_hard_caps |
| P-079 | CLI: venture-intelligence | search providers (search-discovery policies + enabled) | `backend/venture_intelligence/cli.py:196` | needs test |
| P-080 | CLI: venture-intelligence | feeds list \| feeds status | `backend/venture_intelligence/cli.py:198` | needs test |
| P-081 | CLI: venture-intelligence | watchlist list \| watchlist status | `backend/venture_intelligence/cli.py:201` | needs test |
| P-082 | CLI: venture-intelligence | funding recent (financial claims from hypotheses) | `backend/venture_intelligence/cli.py:204` | needs test |
| P-083 | CLI: venture-intelligence | investors recent (investor signals) | `backend/venture_intelligence/cli.py:206` | needs test |
| P-084 | CLI: venture-intelligence | partner mentions (investor signals with partner) | `backend/venture_intelligence/cli.py:208` | needs test |
| P-085 | CLI: venture-intelligence | discovery candidates --program --batch --include-rejected | `backend/venture_intelligence/cli.py:211` | needs test |
| P-086 | CLI: venture-intelligence | candidate show <id> | `backend/venture_intelligence/cli.py:217` | needs test |
| P-087 | CLI: venture-intelligence | candidate evidence\|investigate <id> (observations, why_now, blockers, next checks, coverage) | `backend/venture_intelligence/cli.py:217` | needs test |
| P-088 | CLI: venture-intelligence | candidate export <id> --format json\|markdown --output PATH | `backend/venture_intelligence/cli.py:221` | needs test |
| P-089 | CLI: venture-intelligence | rankings list --program --batch | `backend/venture_intelligence/cli.py:225` | needs test |
| P-090 | CLI: venture-intelligence | changes list --since Nh | `backend/venture_intelligence/cli.py:230` | needs test |
| P-091 | CLI: venture-intelligence | inbox (unreviewed hypotheses) | `backend/venture_intelligence/cli.py:233` | needs test |
| P-092 | CLI: venture-intelligence | graph show <entity_id> (neighbours + evidence) | `backend/venture_intelligence/cli.py:235` | needs test |
| P-093 | CLI: venture-intelligence | research <query> --program --type | `backend/venture_intelligence/cli.py:239` | needs test |
| P-094 | CLI: venture-intelligence | analyst ask <question> | `backend/venture_intelligence/cli.py:248` | needs test |
| P-095 | CLI: venture-intelligence | email preview --digest | `backend/venture_intelligence/cli.py:252` | test_email_preview_and_deterministic_analyst_are_truthful |
| P-096 | CLI: venture-intelligence | email send --digest (force send if configured) | `backend/venture_intelligence/cli.py:252` | needs test |
| P-097 | CLI: venture-intelligence | cycle run [--program ID ...] | `backend/venture_intelligence/cli.py:257` | test_cycle_cli_exit_codes_are_scheduler_safe |
| P-098 | CLI: venture-intelligence | scheduler start --interval Nh [--once] (foreground loop) | `backend/venture_intelligence/cli.py:263` | test_scheduler_restart_overlap_failure_and_enable_disable |
| P-099 | CLI: venture-intelligence | scheduler status \| scheduler stop | `backend/venture_intelligence/cli.py:269` | test_scheduler_restart_overlap_failure_and_enable_disable |
| P-100 | CLI: venture-intelligence | runs list --program \| runs show <run_id> (batch discovery runs) | `backend/venture_intelligence/cli.py:272` | needs test |
| P-101 | CLI: venture-intelligence | db status (path, exists, table counts) | `backend/venture_intelligence/cli.py:278` | needs test |
| P-102 | CLI: venture-intelligence | db fresh --path --confirm [--confirm-operational] | `backend/venture_intelligence/cli.py:280` | needs test |
| P-103 | CLI: venture-intelligence | reconcile [--apply] dry-run-first polluted-entity reconciliation | `backend/venture_intelligence/cli.py:285` | test_polluted_reconciliation_is_dry_run_idempotent_and_preserves_raw_inputs |
| P-104 | CLI: modules/scripts | python -m venture_intelligence.monitor (API + pipeline + agent + connectors) | `backend/venture_intelligence/monitor.py:158` | needs test (tick: test_validator_failure_blocks_downstream_workers) |
| P-105 | CLI: modules/scripts | uvicorn venture_intelligence.api.app:app / python -m venture_intelligence.api.app (API only) | `backend/venture_intelligence/api/app.py:924` | test_frontend_is_served |
| P-106 | CLI: modules/scripts | python -m venture_intelligence.replay.cli --database --until --from --mode --max-events | `backend/venture_intelligence/replay/cli.py:22` | test_bounded_local_cli_outputs_auditable_json |
| P-107 | CLI: modules/scripts | python -m venture_intelligence.evaluation.cli --database import --file | `backend/venture_intelligence/evaluation/cli.py:25` | test_local_cli_imports_and_runs_auditable_evaluation |
| P-108 | CLI: modules/scripts | python -m venture_intelligence.evaluation.cli --database run --ground-truth --cutoff --mode --max-events --ablate-source | `backend/venture_intelligence/evaluation/cli.py:27` | test_local_cli_imports_and_runs_auditable_evaluation; test_source_ablation_is_bounded_and_uses_isolated_replay |
| P-109 | CLI: modules/scripts | python -m venture_intelligence.operations.cli --program --allow-network --ground-truth --report --max-queries --max-requests | `backend/venture_intelligence/operations/cli.py:12` | test_live_gate_and_hard_caps; test_ground_truth_enables_quality_metrics |
| P-110 | CLI: modules/scripts | scripts/export_openapi.py (pnpm api:types -> openapi.json + schema.d.ts) | `scripts/export_openapi.py:11` | needs test |
| P-111 | CLI: modules/scripts | scripts/crawlee_public_fetcher.mjs (pnpm crawl:public; CRAWLER_ENGINE=crawlee) | `scripts/crawlee_public_fetcher.mjs:1` | test_crawlee_adapter_invocation_is_bounded; test_crawlee_timeout_terminates_and_reaps_child |
| P-112 | CLI: modules/scripts | scripts/git/guards.py commit-msg\|subject\|branch\|pr-base\|range\|pre-push | `scripts/git/guards.py:161` | test_valid_subjects; test_branch_names; test_push_refuses_protected_branches_on_any_remote; test_cli_exit_codes |
| P-113 | CLI: modules/scripts | scripts/git/install_hooks.sh + .githooks/commit-msg, .githooks/pre-push | `scripts/git/install_hooks.sh:1` | needs test |
| P-114 | CLI: modules/scripts | pnpm build \| dev \| preview \| test \| test:watch \| typecheck \| lint | `package.json:15` | frontend vitest suite |
| P-115 | CLI: modules/scripts | CI workflow: git guards + offline python suite | `.github/workflows/ci.yml:1` | needs test |
| P-116 | UI: routes | / -> redirect /inbox | `frontend/src/app/router.tsx:21` | redirects the retired dashboard route to the attention overview |
| P-117 | UI: routes | /inbox InboxPage | `frontend/src/app/router.tsx:22` | includes unscoped opportunities in All programs and opens their Research evidence |
| P-118 | UI: routes | /discover DiscoverPage | `frontend/src/app/router.tsx:23` | shows evidence-backed unknown candidates and runs discovery without a seed identity |
| P-119 | UI: routes | /portfolio PortfolioPage | `frontend/src/app/router.tsx:24` | loads the typed CRM decision board |
| P-120 | UI: routes | /research and /research/:entityId ResearchWorkspace (?candidate=, ?view=) | `frontend/src/app/router.tsx:25` | loads versioned rankings and entity evidence lineage |
| P-121 | UI: routes | /system/sources SourcePolicyPage | `frontend/src/app/router.tsx:27` | shows truthful gated live source quality without enabling direct social access |
| P-122 | UI: routes | /system/runs RunsPage | `frontend/src/app/router.tsx:28` | needs test |
| P-123 | UI: routes | /system/settings SettingsPage | `frontend/src/app/router.tsx:29` | saves and tests notification settings through the native workspace |
| P-124 | UI: routes | /system/evaluate EvaluationPage | `frontend/src/app/router.tsx:30` | loads the typed evaluation workspace with an empty report state |
| P-125 | UI: routes | /system/replay ReplayContractPage | `frontend/src/app/router.tsx:31` | renders the typed replay contract without a remote execution control |
| P-126 | UI: routes | Legacy redirects overview\|dashboard\|discovery\|changes\|alerts -> /inbox | `frontend/src/app/router.tsx:32` | redirects the retired dashboard route to the attention overview |
| P-127 | UI: routes | Legacy redirects companies\|founders -> /discover?type=..., rankings -> /discover, crm -> /portfolio | `frontend/src/app/router.tsx:33` | needs test |
| P-128 | UI: routes | Legacy redirects companies/:id, founders/:id -> /research/:id; crm/:id -> ?view=decisions | `frontend/src/app/router.tsx:36` | needs test |
| P-129 | UI: routes | Legacy redirect graph?focus= -> research ?view=network | `frontend/src/app/router.tsx:38` | needs test |
| P-130 | UI: routes | Legacy redirects chat\|analyst -> /research?view=assistant | `frontend/src/app/router.tsx:39` | needs test |
| P-131 | UI: routes | Legacy redirects sources\|runs\|settings\|replay\|evaluate -> /system/* | `frontend/src/app/router.tsx:40` | needs test |
| P-132 | UI: routes | Not-found page with link to Inbox | `frontend/src/app/router.tsx:41` | needs test |
| P-133 | UI: shell | Sidebar navigation: Workspace (Inbox, Discover, Portfolio, Research) + System (Sources, Runs, Evaluation, Replay, Settings) | `frontend/src/shell/navigation.ts:51` | renders responsive navigation and routes foundational pages |
| P-134 | UI: shell | Backend health indicator (polls /api/health) | `frontend/src/shell/AppShell.tsx:80` | needs test |
| P-135 | UI: shell | Program scope selector (All programs / per program), persisted in localStorage | `frontend/src/shell/AppShell.tsx:103` | applies the registry-selected non-YC program to ranking requests |
| P-136 | UI: shell | Theme selector system/light/dark persisted | `frontend/src/shell/AppShell.tsx:114` | persists an explicit light or dark theme preference |
| P-137 | UI: shell | Command palette Cmd/Ctrl+K: navigation commands + validated entity search | `frontend/src/shell/CommandPalette.tsx:14` | opens with Cmd/Ctrl+K and searches validated backend entities |
| P-138 | UI: Inbox | Tabs: Needs attention / Material changes / Conflicting evidence / Watching | `frontend/src/features/ProductWorkspaces.tsx:101` | needs test |
| P-139 | UI: Inbox | Ranked validated entities with reason, identity/program state, attention score | `frontend/src/features/ProductWorkspaces.tsx:105` | renders ranking movement and navigates to company lineage |
| P-140 | UI: Inbox | Hypothesis leads with change explanation / why_now, evidence state, program confidence, first blocker | `frontend/src/features/ProductWorkspaces.tsx:106` | includes unscoped opportunities in All programs and opens their Research evidence |
| P-141 | UI: Inbox | Watching tab lists tracked CRM entities with last note / recommendation | `frontend/src/features/ProductWorkspaces.tsx:106` | needs test |
| P-142 | UI: Inbox | Alerts feed from /api/alerts | `frontend/src/features/ProductWorkspaces.tsx:107` | needs test |
| P-143 | UI: Discover | Tabs companies / founders / programs (URL ?type=) | `frontend/src/features/ProductWorkspaces.tsx:137` | resets the entity filter when navigating between companies and founders |
| P-144 | UI: Discover | Search, evidence-state filter (all/validated/needs corroboration), sort (priority/name/recent) | `frontend/src/features/ProductWorkspaces.tsx:138` | filters the ranking contract by entity type |
| P-145 | UI: Discover | Opportunity table: validated rankings + unique hypothesis leads (dedup by canonical id/name) | `frontend/src/features/ProductWorkspaces.tsx:141` | renders each canonical company once even if a malformed response repeats it; shows current F26 company and founder results in their respective views |
| P-146 | UI: Discover | Refresh intelligence button -> POST /api/v1/cycles/run (scoped to program) | `frontend/src/features/ProductWorkspaces.tsx:124` | shows evidence-backed unknown candidates and runs discovery without a seed identity |
| P-147 | UI: Discover | Programs tab: select program scope | `frontend/src/features/ProductWorkspaces.tsx:139` | needs test |
| P-148 | UI: Portfolio | Tracked opportunities list with stage filter, last note, next action, link to decisions | `frontend/src/features/ProductWorkspaces.tsx:150` | loads the typed CRM decision board; renders integrity-gated stages and opens the native decision detail |
| P-149 | UI: Research | Header: identity/program status, independent origins, Track/follow up link | `frontend/src/features/research/ResearchWorkspace.tsx:39` | needs test |
| P-150 | UI: Research | View tabs overview/evidence/timeline/network/decisions/investigate/assistant (URL ?view=) | `frontend/src/features/research/ResearchWorkspace.tsx:42` | needs test |
| P-151 | UI: Research | Overview: IntelligenceLineage summary, what changed, signal tabs, evidence->feature->signal columns, ranking history | `frontend/src/features/intelligence/IntelligencePages.tsx:24` | loads versioned rankings and entity evidence lineage; renders ranking movement and navigates to company lineage |
| P-152 | UI: Research | Overview (candidate): why surfaced, uncertainty/blockers, profile (founder, company, website, background, cofounders), funding rounds, investor signals, link to promoted identity | `frontend/src/features/research/ResearchWorkspace.tsx:45` | needs test |
| P-153 | UI: Research | Evidence: knowledge categories via SSE + 15s polling fallback | `frontend/src/features/research/KnowledgeEvidence.tsx:6` | refreshes category confidence on a revision and falls back without reconnect loops; does not manufacture a wiki for a missing canonical page |
| P-154 | UI: Research | Evidence: attached / unbound / contradictory groups with provenance detail | `frontend/src/features/research/ResearchWorkspace.tsx:54` | separates attached evidence from same-name leads |
| P-155 | UI: Research | Timeline: evidence and observations sorted by observed_at with event time | `frontend/src/features/research/ResearchWorkspace.tsx:63` | needs test |
| P-156 | UI: Research | Network: lazy GraphPage focused on entity (blocked for unpromoted candidates) | `frontend/src/features/research/ResearchWorkspace.tsx:67` | loads the native bounded temporal graph workspace |
| P-157 | UI: Research | Decisions: DecisionDetails for validated entity | `frontend/src/features/research/ResearchWorkspace.tsx:68` | renders integrity-gated stages and opens the native decision detail |
| P-158 | UI: Research | Investigate: ResearchCollector bounded research run + recent research (localStorage) | `frontend/src/features/research/ResearchPage.tsx:39` | runs bounded research from the native side-by-side workspace |
| P-159 | UI: Research | Assistant: ContextualAnalyst entity-scoped Q&A with citations and caveats | `frontend/src/features/operations/OperationsPages.tsx:17` | needs test (backend: test_entity_scoped_assistant_refuses_global_list_question) |
| P-160 | UI: Research | Auto-refresh entity and candidate every 15s | `frontend/src/features/research/ResearchWorkspace.tsx:24` | needs test |
| P-161 | UI: Graph | 3D (React Three Fiber, lazy) vs accessible relationship table toggle | `frontend/src/features/graph/GraphPage.tsx:171` | uses the accessible sparse state for one verified node with no relationships |
| P-162 | UI: Graph | Search/focus node, collapse/expand neighbours | `frontend/src/features/graph/GraphPage.tsx:181` | searches, focuses, collapses neighbors, and inspects node provenance |
| P-163 | UI: Graph | As-of historical snapshot apply/reset | `frontend/src/features/graph/GraphPage.tsx:211` | opens edge validity/provenance and applies an isolated as-of request |
| P-164 | UI: Graph | Node inspector (properties, sources) and edge inspector (validity, provenance) | `frontend/src/features/graph/GraphPage.tsx:46` | searches, focuses, collapses neighbors, and inspects node provenance |
| P-165 | UI: Graph | Deterministic layout, edge forces, empty/single graph states | `frontend/src/features/graph/graphModel.ts:1` | uses deterministic positions and preserves one-hop search context; uses canonical edges as forces, is order independent, and handles empty/single graphs |
| P-166 | UI: Decisions | Action composer: action type, partner, note, pass reason taxonomy, allowed stage transitions | `frontend/src/features/decisions/DecisionPages.tsx:63` | records typed partner actions and refreshes decision projections; distinguishes factual false positives from investment passes |
| P-167 | UI: Decisions | Feedback lineage (bounded score influence features) | `frontend/src/features/decisions/DecisionPages.tsx:113` | needs test |
| P-168 | UI: Decisions | Append-only CRM action history | `frontend/src/features/decisions/DecisionPages.tsx:128` | needs test |
| P-169 | UI: Decisions | Current recommendation + lifecycle buttons (accept/dismiss/complete) + superseded history | `frontend/src/features/decisions/DecisionPages.tsx:143` | records recommendation and alert lifecycle decisions; shows one current recommendation and keeps superseded versions in audit history |
| P-170 | UI: Decisions | Alert history + lifecycle buttons (acknowledge/dismiss/resolve) | `frontend/src/features/decisions/DecisionPages.tsx:165` | records recommendation and alert lifecycle decisions |
| P-171 | UI: System | Sources: live coverage report (state, requests, yield, accepted, latency, diagnostic, stop reason) | `frontend/src/features/foundation/FoundationPage.tsx:43` | shows truthful gated live source quality without enabling direct social access |
| P-172 | UI: System | Sources: research provider cards with enable/disable toggle | `frontend/src/features/foundation/FoundationPage.tsx:92` | needs test |
| P-173 | UI: System | Sources: public intelligence watchlist | `frontend/src/features/foundation/FoundationPage.tsx:134` | needs test |
| P-174 | UI: System | Sources: monitor connector policy cards (incl. Product Hunt configuration vs availability) | `frontend/src/features/foundation/FoundationPage.tsx:145` | shows Product Hunt configuration separately from availability |
| P-175 | UI: System | Runs: readiness component cards with action + technical detail | `frontend/src/features/operations/OperationsPages.tsx:89` | needs test |
| P-176 | UI: System | Runs: scheduler state + enable/disable | `frontend/src/features/operations/OperationsPages.tsx:92` | needs test |
| P-177 | UI: System | Runs: run history with per-program counts and raw diagnostics | `frontend/src/features/operations/OperationsPages.tsx:97` | needs test |
| P-178 | UI: System | Settings: SMTP transport/destination/sending/last outcome status | `frontend/src/features/operations/OperationsPages.tsx:62` | shows SMTP transport, destination, and effective sending separately |
| P-179 | UI: System | Settings: recipient, enable switch, frequency, minimum confidence, alert-type toggles, save | `frontend/src/features/operations/OperationsPages.tsx:59` | saves and tests notification settings through the native workspace |
| P-180 | UI: System | Settings: preview partner digest / send partner digest / send test email | `frontend/src/features/operations/OperationsPages.tsx:56` | saves and tests notification settings through the native workspace |
| P-181 | UI: System | Evaluation: run list, metrics, missed/false-positive diagnostics, reproducibility digests, CLI contract | `frontend/src/features/evaluation/EvaluationPage.tsx:23` | loads the typed evaluation workspace with an empty report state; renders persisted metrics and diagnostics from the typed detail contract |
| P-182 | UI: System | Replay: contract explainer (cutoff, isolated store, suppressed side effects, CLI) | `frontend/src/features/replay/ReplayContractPage.tsx:8` | renders the typed replay contract without a remote execution control |
| P-183 | UI: System | Typed API client rejects malformed responses / network errors | `frontend/src/api/client.ts:111` | converts network failures into a stable API error; rejects malformed endpoint shapes; rejects malformed JSON |
| P-184 | UI: System | Loading/empty/partial/error async states | `frontend/src/components/AsyncState.tsx:1` | renders loading before content; renders empty, partial, and error states accessibly |
| P-185 | Connectors (monitor) | Connector factory registers 13 connectors (shared or isolated crawler) | `backend/venture_intelligence/connectors/factory.py:24` | test_factory_registers_social_and_discord; test_every_registered_provider_and_connector_has_explicit_policy |
| P-186 | Connectors (monitor) | DirectoryConnector official program directories (verification) | `backend/venture_intelligence/connectors/directory.py:63` | test_directory_reports_success_empty_drift_parser_failure_and_final_url; test_structured_directory_through_canonical_pipeline |
| P-187 | Connectors (monitor) | TriangulationConnector | `backend/venture_intelligence/connectors/triangulation.py:13` | test_triangulation_never_retypes_a_person_as_a_company |
| P-188 | Connectors (monitor) | NewsletterConnector | `backend/venture_intelligence/connectors/environmental.py:125` | needs test |
| P-189 | Connectors (monitor) | BlogConnector | `backend/venture_intelligence/connectors/environmental.py:161` | needs test |
| P-190 | Connectors (monitor) | WebsiteConnector (COMPANY_WEBSITE_FOUND) | `backend/venture_intelligence/connectors/environmental.py:169` | test_website_investigation_emits_public_canonical_event |
| P-191 | Connectors (monitor) | SocialConnector (X/Instagram, indexed-only by default) | `backend/venture_intelligence/connectors/environmental.py:179` | test_social_scan_uses_x_and_instagram; test_social_profile_investigation_supports_instagram; test_social_handles_are_bounded_and_unique |
| P-192 | Connectors (monitor) | DiscordConnector public widget | `backend/venture_intelligence/connectors/environmental.py:291` | test_discord_scan_never_promotes_widget_usernames; test_discord_investigation_matches_public_member |
| P-193 | Connectors (monitor) | LinkedInConnector (search discovery only) | `backend/venture_intelligence/connectors/public_sources.py:429` | test_default_monitor_connectors_never_contact_restricted_social_sites |
| P-194 | Connectors (monitor) | GitHubConnector (REPO_CREATED; optional token) | `backend/venture_intelligence/connectors/public_sources.py:107` | test_github_connector_uses_optional_token_without_exposing_it; test_github_rejects_non_json_search_text |
| P-195 | Connectors (monitor) | HackerNewsConnector (HN_MENTIONED) | `backend/venture_intelligence/connectors/public_sources.py:209` | needs test |
| P-196 | Connectors (monitor) | ArxivConnector (PAPER_PUBLISHED) | `backend/venture_intelligence/connectors/public_sources.py:217` | needs test |
| P-197 | Connectors (monitor) | ProductHuntConnector official API (credential gated) | `backend/venture_intelligence/connectors/public_sources.py:226` | test_product_hunt_official_api_is_credential_gated; test_product_hunt_official_api_preserves_target_identity_and_provenance |
| P-198 | Connectors (monitor) | WellfoundConnector (JOB_POSTED) | `backend/venture_intelligence/connectors/public_sources.py:421` | needs test |
| P-199 | Connectors (monitor) | FirecrawlConnector (reserved, NOT registered) | `backend/venture_intelligence/connectors/firecrawl.py:12` | needs test |
| P-200 | Connectors (monitor) | ConnectorCoordinator scan/route/investigate, failure isolation, evidence outcome recording | `backend/venture_intelligence/connectors/base.py:120` | test_evidence_metrics_are_recorded_only_after_usable_content |
| P-201 | Connectors (monitor) | Optional Ollama EnvironmentalExtractor with regex fallback | `backend/venture_intelligence/agents/environmental_extractor.py:24` | test_extractor_returns_none_when_ollama_is_disabled; test_extractor_rejects_malformed_ollama_json; test_extractor_uses_explicit_regex_fallback |
| P-202 | Research providers | DuckDuckGo HTML search (202 retry) | `backend/venture_intelligence/research/sources.py:381` | test_duckduckgo_retries_202_without_asserting_evidence |
| P-203 | Research providers | Bing / Yahoo / Brave public HTML search | `backend/venture_intelligence/research/sources.py:593` | test_public_search_parsers_retain_acquisition_provenance; test_search_outage_falls_back_and_same_article_is_one_origin |
| P-204 | Research providers | Public program announcement OSINT provider | `backend/venture_intelligence/research/sources.py:629` | test_public_program_osint_is_bounded_and_provenance_preserving |
| P-205 | Research providers | arXiv official package provider | `backend/venture_intelligence/research/sources.py:809` | test_arxiv_provider_uses_official_package_contract |
| P-206 | Research providers | Google Scholar (opt-in) provider | `backend/venture_intelligence/research/sources.py:963` | test_google_scholar_is_optional_and_structured_when_blocked |
| P-207 | Research providers | Wayback historical provider | `backend/venture_intelligence/research/sources.py:1073` | test_wayback_response_is_structured_historical_evidence |
| P-208 | Research providers | RSS provider (TechCrunch, newsletter watchlist; incremental state; funding extraction) | `backend/venture_intelligence/research/sources.py:1409` | test_incremental_rss_is_successful_empty_and_extracts_funding |
| P-209 | Research providers | Public watchlist page provider (VC pages, content hash) | `backend/venture_intelligence/research/sources.py:1583` | test_public_watchlist_resolves_a16z_as_generic_alias |
| P-210 | Research providers | Composite provider fallback + per-provider budgets | `backend/venture_intelligence/research/sources.py:1684` | test_agent_fanout_keeps_provider_provenance_and_failures |
| P-211 | Research providers | Research agent: query generation, follow-ups, cache, budgets, per-result attribution, knowledge compile | `backend/venture_intelligence/research/agent.py:277` | test_query_generator_builds_quoted_site_and_topic_families; test_agent_respects_query_budget_and_json_contract; test_common_name_evidence_stays_unbound_end_to_end |
| P-212 | Research providers | Provider policies (access method, role, defaults) for every provider/connector | `backend/venture_intelligence/research/provider_policy.py:117` | test_every_registered_provider_and_connector_has_explicit_policy |
| P-213 | Research providers | Crawler: bounded httpx, redirect policy, private-target block, circuit breaker, auth classification | `backend/venture_intelligence/crawler.py:102` | test_crawler_rejects_private_targets_before_network_access; test_crawler_circuit_breaker_bounds_repeated_failures; test_search_redirect_rechecks_restricted_and_private_target_policy |
| P-214 | Research providers | Crawler: optional Playwright rendering | `backend/venture_intelligence/crawler.py:102` | test_rendered_crawl_obeys_budget_auth_classification_and_circuit; test_rendered_redirects_are_blocked_and_subresources_are_disabled |
| P-215 | Research providers | Request budgets per provider/domain/retry/redirect | `backend/venture_intelligence/research/request_budget.py:33` | test_actual_request_budget_reserves_each_redirect_hop; test_actual_request_budget_bounds_retries_and_reports_stop_reason |
| P-216 | Batch discovery | Latest-batch discovery service (queries, observations, clustering, assessment, promotion) | `backend/venture_intelligence/batch_discovery/service.py:82` | test_unknown_candidate_discovery_to_promotion_and_historical_replay |
| P-217 | Batch discovery | Candidate profile (funding, investors, attention, completeness) | `backend/venture_intelligence/batch_discovery/engine.py:722` | test_funding_claim_corpus_preserves_semantics; test_conflicting_round_amounts_are_preserved_not_silently_resolved |
| P-218 | Batch discovery | Local Maigret/Sherlock bounded tools (live-gated) + manual export adapter | `backend/venture_intelligence/batch_discovery/tools.py:465` | test_bounded_tool_normalizes_without_identity_promotion; test_osint_benchmark_and_manual_adapter_never_execute_network |
| P-219 | Batch discovery | Change intelligence between cycles | `backend/venture_intelligence/batch_discovery/service.py:1027` | test_change_intelligence_ignores_tiny_recomputation_but_keeps_semantics |
| P-220 | Jobs / loops | Monitor: per-connector scan loop every CONNECTOR_POLL_INTERVAL; closes crawler on cancel | `backend/venture_intelligence/monitor.py:34` | needs test |
| P-221 | Jobs / loops | Monitor: pipeline_tick every 60s (validate, resolve, graph, evidence, signals, rank, recommend, notify) | `backend/venture_intelligence/monitor.py:51` | test_validator_failure_blocks_downstream_workers; test_generic_candidates_are_rejected_before_eventbus_persistence |
| P-222 | Jobs / loops | AgentLoop every 300s: prioritise/investigate, refresh stale (12h), learn from CRM, resurface passed | `backend/venture_intelligence/agent/loop.py:72` | test_agent_and_reconciliation_reject_orphaned_polluted_derived_state |
| P-223 | Jobs / loops | Intelligence cycle run_once (locked; per-program discovery; changes; drains canonical events) | `backend/venture_intelligence/operations/cycle.py:178` | test_cycle_persists_twelve_hour_schedule_and_reuses_current_batch; test_cycle_rejects_unknown_program_before_claiming_success |
| P-224 | Jobs / loops | Scheduler loop (interval hours, --once, enabled flag, outcome recording) | `backend/venture_intelligence/operations/cycle.py:447` | test_scheduler_restart_overlap_failure_and_enable_disable |
| P-225 | Jobs / loops | Cycle file lock with live-PID protection and stale recovery (Windows + POSIX) | `backend/venture_intelligence/operations/cycle.py:484` | test_cycle_lock_never_reclaims_a_live_process; test_cycle_lock_recovers_dead_and_old_invalid_owners |
| P-226 | Jobs / loops | Gated live source validation run (report persisted) | `backend/venture_intelligence/operations/validation.py:94` | test_live_gate_and_hard_caps; test_isolated_live_report_preserves_policy_and_truthfulness |
| P-227 | Pipeline / domain | Identity/integrity gate before EventBus; rejected candidates audited | `backend/venture_intelligence/pipeline/entity_integrity.py:93` | test_generic_candidates_are_rejected_before_eventbus_persistence; test_rejected_candidate_is_json_auditable_and_never_reviewed |
| P-228 | Pipeline / domain | Event validator (URL, time, confidence, entity-type compatibility) | `backend/venture_intelligence/pipeline/validator.py:81` | test_validator_failure_blocks_downstream_workers |
| P-229 | Pipeline / domain | Entity resolution (directory, YC URL, slug, exact hypothesis name, fuzzy company, exact profile person) | `backend/venture_intelligence/pipeline/entity_resolution.py:109` | test_identity_stress_avoids_false_merges_and_preserves_explicit_rename; test_m8_hypotheses_do_not_fuzzy_merge_promoted_companies |
| P-230 | Pipeline / domain | Graph builder (temporal edges, funding/investor edges) | `backend/venture_intelligence/pipeline/graph.py:54` | test_funding_graph_edges_are_evidence_backed; test_graph_observation_time_does_not_invent_validity |
| P-231 | Pipeline / domain | Evidence builder | `backend/venture_intelligence/pipeline/evidence_builder.py:39` | test_attributed_founders_reach_evidence_research_and_relationships |
| P-232 | Pipeline / domain | Versioned feature worker (as-of, lineage, CRM feedback features) | `backend/venture_intelligence/pipeline/feature_worker.py:27` | test_feature_computation_is_versioned_deterministic_and_lineaged; test_features_and_signals_respect_observed_at_cutoff |
| P-233 | Pipeline / domain | Signal worker + hypotheses | `backend/venture_intelligence/pipeline/signal_worker.py:49` | test_repository_features_are_entity_scoped_not_global |
| P-234 | Pipeline / domain | Ranking worker: 9 dimensions, composite, rank, history | `backend/venture_intelligence/pipeline/ranking_worker.py:49` | test_complete_lineage_and_typed_api_are_integrity_gated |
| P-235 | Pipeline / domain | Recommendation worker (escalate/contact/monitor/ignore; supersede lifecycle; CRM auto-discovery) | `backend/venture_intelligence/pipeline/recommendation_worker.py:57` | test_recommendation_and_alert_lifecycles_are_append_only |
| P-236 | Pipeline / domain | Classification worker (person type) - not wired at runtime | `backend/venture_intelligence/pipeline/classification_worker.py:37` | TestClassificationWorker (tests/test_pipeline.py) |
| P-237 | Pipeline / domain | CRM workflow: stages, allowed transitions, append-only actions, notes, board, history | `backend/venture_intelligence/crm/workflow.py:56` | test_typed_action_is_append_only_idempotent_and_fully_lineaged; test_pre_m5_database_migrates_without_losing_crm_history |
| P-238 | Pipeline / domain | Feedback bounded into features; realized outcome typed separately | `backend/venture_intelligence/pipeline/feature_worker.py:251` | test_feedback_is_bounded_and_realized_outcome_is_not_ground_truth |
| P-239 | Pipeline / domain | Intelligence service: identity/program status, rankings read model, entity lineage, analyst entities | `backend/venture_intelligence/intelligence/service.py:30` | test_program_company_read_model_is_unique_and_current_cohort_only |
| P-240 | Pipeline / domain | Analyst agent: local deterministic + optional Qwen/Ollama, epistemic states, unsupported claim discard | `backend/venture_intelligence/agents/analyst.py:206` | test_analyst_qwen_provider_is_optional_and_json_native; test_analyst_epistemic_states_and_absence_language; test_unsupported_local_model_claim_is_discarded; test_analyst_auto_mode_falls_back_without_hosted_or_local_model |
| P-241 | Pipeline / domain | Program registry (active, get, by_batch, text resolution, prefixed ids, website_urls, discord) | `backend/venture_intelligence/programs.py:123` | test_registry_drives_yc_and_non_yc_domain_projection; test_program_registry_entity_id_is_prefixed; test_registry_exposes_public_website_urls |
| P-242 | Pipeline / domain | Schema migrations additive and idempotent; legacy quarantine | `backend/venture_intelligence/storage/db/schema.py:473` | test_previous_schema_is_migrated_additively; test_m8_migration_is_additive_and_idempotent; test_database_quarantines_pre_gate_legacy_events |
| P-243 | Pipeline / domain | Reconciliation / cleanup (directory links, generic entities, derived state) | `backend/venture_intelligence/storage/cleanup.py:478` | test_directory_company_reconciliation_is_dry_run_first_and_preserves_raw_events; test_generic_derived_cleanup_preserves_immutable_evidence |
| P-244 | Pipeline / domain | Observability metrics collector | `backend/venture_intelligence/observability/metrics.py:28` | test_concurrent_evidence_metrics_remain_operation_attributed |
| P-245 | Notifications | Alert dispatch: two-gate threshold, alert build, console sink (always) | `backend/venture_intelligence/pipeline/notification.py:57` | test_recommendation_and_alert_lifecycles_are_append_only |
| P-246 | Notifications | Slack incoming-webhook sink (SLACK_WEBHOOK_URL) | `backend/venture_intelligence/pipeline/notification.py:185` | needs test |
| P-247 | Notifications | Email sink (category threshold_alerts, confidence filter) | `backend/venture_intelligence/pipeline/notification.py:148` | needs test |
| P-248 | Notifications | Alert lifecycle events created/dispatching/delivered\|partial\|failed; notified_at | `backend/venture_intelligence/pipeline/notification.py:297` | test_recommendation_and_alert_lifecycles_are_append_only |
| P-249 | Notifications | Email config store: destination validation, preferences, frequency, min confidence | `backend/venture_intelligence/pipeline/email.py:91` | test_email_validation_and_disabled_suppression; test_email_category_confidence_and_frequency_filters |
| P-250 | Notifications | Email send: suppression rules, duplicate guard, retries with back-off, refused-recipient failure, delivery log | `backend/venture_intelligence/pipeline/email.py:179` | test_email_success_duplicate_and_test; test_email_refused_recipient_is_never_recorded_as_sent; test_email_retry_exhaustion_is_logged |
| P-251 | Notifications | Email runtime status (transport/destination/sending/last outcome) | `backend/venture_intelligence/pipeline/email.py:121` | test_email_runtime_status_separates_transport_destination_and_enablement; test_readiness_uses_same_email_runtime_semantics |
| P-252 | Notifications | Email preview (no send) | `backend/venture_intelligence/pipeline/email.py:152` | test_email_preview_and_deterministic_analyst_are_truthful |
| P-253 | Notifications | Partner digest composition (API) deduplicating changes | `backend/venture_intelligence/api/app.py:603` | test_digest_deduplicates_one_underlying_change_and_source |
| P-254 | Notifications | Partner digest composition (CLI) | `backend/venture_intelligence/cli.py:151` | needs test |
| P-255 | Exports / artifacts | Candidate export JSON or Markdown (stdout or --output file) | `backend/venture_intelligence/cli.py:440` | needs test |
| P-256 | Exports / artifacts | Knowledge pages: canonical JSON + rendered Markdown wiki under knowledge/<type>/ | `backend/venture_intelligence/knowledge/compiler.py:137` | test_json_to_markdown_is_deterministic_and_incremental; test_research_run_compiles_incremental_knowledge |
| P-257 | Exports / artifacts | Knowledge validation report (historical + broken references) | `backend/venture_intelligence/knowledge/compiler.py:319` | test_historical_and_broken_reference_validation |
| P-258 | Exports / artifacts | OpenAPI export + generated TypeScript schema | `scripts/export_openapi.py:11` | test_versioned_api_is_in_openapi |
| P-259 | Exports / artifacts | --json structured output for every CLI command | `backend/venture_intelligence/cli.py:54` | test_doctor_human_output_is_concise_but_json_preserves_diagnostics |
| P-260 | Exports / artifacts | Source quality report JSON (data/source_quality/latest.json) | `backend/venture_intelligence/operations/store.py:11` | test_isolated_live_report_preserves_policy_and_truthfulness |
| P-261 | Replay / evaluation / graph | Replay service: observed_at cutoff (default), occurred_at mode, isolated disposable store, digests, suppressed side effects | `backend/venture_intelligence/replay/service.py:77` | test_observed_at_is_default_cutoff_and_future_observation_cannot_leak; test_replay_never_mutates_live_database_or_operational_state; test_identical_inputs_produce_identical_relevant_projections |
| P-262 | Replay / evaluation / graph | Replay bounded partial/empty/integrity-rejected states | `backend/venture_intelligence/replay/service.py:77` | test_empty_partial_and_integrity_rejected_states_are_truthful; test_bounded_partial_replay_preserves_valid_projection |
| P-263 | Replay / evaluation / graph | Evaluation: ground-truth import validation (typed, exact, integrity-gated) | `backend/venture_intelligence/evaluation/service.py:51` | test_ground_truth_validation_is_typed_exact_and_integrity_gated |
| P-264 | Replay / evaluation / graph | Evaluation run: precision/recall/lead time/false positives/missed, person vs company, ablation | `backend/venture_intelligence/evaluation/service.py:74` | test_precision_recall_lead_time_false_positive_and_missed_are_reproducible; test_company_and_person_rankings_are_evaluated_separately |
| P-265 | Replay / evaluation / graph | Graph service: live integrity-gated snapshot and historical via replay | `backend/venture_intelligence/graph/service.py:47` | test_live_graph_contract_is_bounded_typed_and_integrity_gated; test_historical_graph_uses_isolated_observed_at_replay |
| P-266 | Replay / evaluation / graph | Decision service: board, entity, apply action, recommendation/alert lifecycle, as_of | `backend/venture_intelligence/decisions/service.py:48` | test_typed_decision_api_is_integrity_gated; test_replay_uses_only_historically_eligible_crm_actions |
| P-267 | Config files / state | config/programs.yaml program registry (yc_current F26, speedrun_current SR007) | `config/programs.yaml:7` | test_registry_drives_yc_and_non_yc_domain_projection |
| P-268 | Config files / state | config/intelligence_watchlists.yaml (publications, newsletters, VC firms) | `config/intelligence_watchlists.yaml:1` | test_public_watchlist_resolves_a16z_as_generic_alias |
| P-269 | Config files / state | .env / .env.example settings source (pydantic-settings) | `.env.example:1` | needs test |
| P-270 | Config files / state | ollama_modelfile/Modelfile custom Qwen extractor | `ollama_modelfile/Modelfile:1` | test_modelfile_uses_required_qwen_parameters |
| P-271 | Config files / state | data/intelligence.db SQLite (30 tables) | `backend/venture_intelligence/storage/db/_base.py:14` | test_fresh_database_has_temporal_foundation |
| P-272 | Config files / state | data/email_notifications.json email config + delivery log | `backend/venture_intelligence/pipeline/email.py:49` | needs test |
| P-273 | Config files / state | data/source_settings.json operator source overrides | `backend/venture_intelligence/research/watchlist.py:148` | test_default_source_settings_and_fresh_email_are_truthful |
| P-274 | Config files / state | data/source_state.json feed/page incremental state | `backend/venture_intelligence/research/watchlist.py:78` | needs test |
| P-275 | Config files / state | data/operations/state.json + cycle.lock | `backend/venture_intelligence/operations/cycle.py:126` | test_cycle_lock_recovers_dead_and_old_invalid_owners |
| P-276 | Config files / state | knowledge/{companies,founders,investigations,programs,sources}/ runtime pages | `knowledge/companies/.gitkeep` | needs test |
| P-277 | Config files / state | frontend/vite.config.ts dev proxy to :8000 + vitest config | `frontend/vite.config.ts:1` | needs test |
| P-278 | Config files / state | Browser localStorage: program scope, theme, recent research | `frontend/src/state/ProgramProvider.tsx:27` | persists an explicit light or dark theme preference |
| P-279 | Env var | DB_PATH (str; default "data/intelligence.db") | `backend/venture_intelligence/config.py:19` | needs test |
| P-280 | Env var | PROGRAMS_CONFIG_PATH (str; default "config/programs.yaml") | `backend/venture_intelligence/config.py:20` | needs test |
| P-281 | Env var | INTELLIGENCE_WATCHLIST_PATH (str; default "config/intelligence_watchlists.yaml") | `backend/venture_intelligence/config.py:21` | needs test |
| P-282 | Env var | KNOWLEDGE_DIR (str; default "knowledge") | `backend/venture_intelligence/config.py:22` | needs test |
| P-283 | Env var | CONNECTOR_POLL_INTERVAL (int; default 1800) | `backend/venture_intelligence/config.py:26` | needs test |
| P-284 | Env var | CONNECTOR_TIMEOUT_SECONDS (float; default 10.0) | `backend/venture_intelligence/config.py:27` | needs test |
| P-285 | Env var | GATHER_TIMEOUT_SECONDS (float; default 12.0) | `backend/venture_intelligence/config.py:28` | needs test |
| P-286 | Env var | MAX_SEARCHES_PER_PROGRAM (int; default 4) | `backend/venture_intelligence/config.py:29` | needs test |
| P-287 | Env var | MAX_RESPONSE_BYTES (int; default 1_000_000) | `backend/venture_intelligence/config.py:30` | needs test |
| P-288 | Env var | USER_AGENT (str; default "venture-intelligence/0.1 (public-web-monitor)") | `backend/venture_intelligence/config.py:31` | needs test |
| P-289 | Env var | CRAWLER_RENDER_ENABLED (bool; default False) | `backend/venture_intelligence/config.py:32` | needs test |
| P-290 | Env var | CRAWLER_ENGINE (str; default "playwright") | `backend/venture_intelligence/config.py:33` | needs test |
| P-291 | Env var | CRAWLER_FAILURE_THRESHOLD (int; default 3) | `backend/venture_intelligence/config.py:34` | needs test |
| P-292 | Env var | CRAWLER_CIRCUIT_COOLDOWN_SECONDS (float; default 60.0) | `backend/venture_intelligence/config.py:35` | needs test |
| P-293 | Env var | RESTRICTED_SOCIAL_ACCESS_MODE (SourceAccessMode; default SourceAccessMode.SEARCH_DISCOVERY) | `backend/venture_intelligence/config.py:36` | needs test |
| P-294 | Env var | RESEARCH_MAX_QUERIES (int; default 20) | `backend/venture_intelligence/config.py:40` | needs test |
| P-295 | Env var | RESEARCH_MAX_DEPTH (int; default 3) | `backend/venture_intelligence/config.py:41` | needs test |
| P-296 | Env var | RESEARCH_MAX_RESULTS_PER_QUERY (int; default 5) | `backend/venture_intelligence/config.py:42` | needs test |
| P-297 | Env var | RESEARCH_MAX_RUNTIME_SECONDS (float; default 30.0) | `backend/venture_intelligence/config.py:43` | needs test |
| P-298 | Env var | RESEARCH_MAX_TOTAL_REQUESTS (int; default 30) | `backend/venture_intelligence/config.py:44` | needs test |
| P-299 | Env var | RESEARCH_MAX_REQUESTS_PER_PROVIDER (int; default 12) | `backend/venture_intelligence/config.py:45` | needs test |
| P-300 | Env var | RESEARCH_MAX_RETRY_REQUESTS (int; default 8) | `backend/venture_intelligence/config.py:46` | needs test |
| P-301 | Env var | RESEARCH_MAX_REQUESTS_PER_DOMAIN (int; default 20) | `backend/venture_intelligence/config.py:47` | needs test |
| P-302 | Env var | RESEARCH_CACHE_TTL_SECONDS (float; default 300.0) | `backend/venture_intelligence/config.py:48` | needs test |
| P-303 | Env var | RESEARCH_ARXIV_ENABLED (bool; default True) | `backend/venture_intelligence/config.py:49` | needs test |
| P-304 | Env var | RESEARCH_SCHOLAR_ENABLED (bool; default False) | `backend/venture_intelligence/config.py:50` | needs test |
| P-305 | Env var | SEARCH_DUCKDUCKGO_ENABLED (bool; default True) | `backend/venture_intelligence/config.py:51` | needs test |
| P-306 | Env var | SEARCH_BING_ENABLED (bool; default True) | `backend/venture_intelligence/config.py:52` | needs test |
| P-307 | Env var | SEARCH_YAHOO_ENABLED (bool; default True) | `backend/venture_intelligence/config.py:53` | needs test |
| P-308 | Env var | SEARCH_BRAVE_ENABLED (bool; default True) | `backend/venture_intelligence/config.py:54` | needs test |
| P-309 | Env var | TECHCRUNCH_ENABLED (bool; default True) | `backend/venture_intelligence/config.py:55` | needs test |
| P-310 | Env var | NEWSLETTER_WATCHLIST_ENABLED (bool; default True) | `backend/venture_intelligence/config.py:56` | needs test |
| P-311 | Env var | VC_WATCHLIST_ENABLED (bool; default True) | `backend/venture_intelligence/config.py:57` | needs test |
| P-312 | Env var | SOURCE_SETTINGS_PATH (str; default "data/source_settings.json") | `backend/venture_intelligence/config.py:58` | needs test |
| P-313 | Env var | SOURCE_STATE_PATH (str; default "data/source_state.json") | `backend/venture_intelligence/config.py:59` | needs test |
| P-314 | Env var | SOURCE_QUALITY_REPORT_PATH (str; default "data/source_quality/latest.json") | `backend/venture_intelligence/config.py:60` | needs test |
| P-315 | Env var | OPERATIONS_STATE_PATH (str; default "data/operations/state.json") | `backend/venture_intelligence/config.py:61` | needs test |
| P-316 | Env var | OPERATIONS_LOCK_PATH (str; default "data/operations/cycle.lock") | `backend/venture_intelligence/config.py:62` | needs test |
| P-317 | Env var | OPERATIONS_LOCK_STALE_SECONDS (float; default 21_600.0) | `backend/venture_intelligence/config.py:63` | needs test |
| P-318 | Env var | SCHEDULER_INTERVAL_HOURS (float; default 12.0) | `backend/venture_intelligence/config.py:64` | needs test |
| P-319 | Env var | SCHEDULER_ENABLED (bool; default True) | `backend/venture_intelligence/config.py:65` | needs test |
| P-320 | Env var | GITHUB_TOKEN (SecretStr \| None; default None) | `backend/venture_intelligence/config.py:68` | needs test |
| P-321 | Env var | PRODUCT_HUNT_API_ENABLED (bool; default False) | `backend/venture_intelligence/config.py:69` | needs test |
| P-322 | Env var | PRODUCT_HUNT_ACCESS_TOKEN (SecretStr \| None; default None) | `backend/venture_intelligence/config.py:70` | needs test |
| P-323 | Env var | PRODUCT_HUNT_KNOWN_POSITIVE_POST_ID (str; default "139993") | `backend/venture_intelligence/config.py:71` | needs test |
| P-324 | Env var | DISCORD_API_ENABLED (bool; default False) | `backend/venture_intelligence/config.py:72` | needs test |
| P-325 | Env var | DISCORD_BOT_TOKEN (SecretStr \| None; default None) | `backend/venture_intelligence/config.py:73` | needs test |
| P-326 | Env var | DISCORD_AUTHORIZED_GUILD_IDS (str; default "") | `backend/venture_intelligence/config.py:74` | needs test |
| P-327 | Env var | DISCORD_AUTHORIZED_CHANNEL_IDS (str; default "") | `backend/venture_intelligence/config.py:75` | needs test |
| P-328 | Env var | FIRECRAWL_API_URL (str \| None; default None) | `backend/venture_intelligence/config.py:76` | needs test |
| P-329 | Env var | FIRECRAWL_API_KEY (str \| None; default None) | `backend/venture_intelligence/config.py:77` | needs test |
| P-330 | Env var | OLLAMA_ENABLED (bool; default False) | `backend/venture_intelligence/config.py:78` | needs test |
| P-331 | Env var | OLLAMA_BASE_URL (str; default "http://127.0.0.1:11434") | `backend/venture_intelligence/config.py:79` | needs test |
| P-332 | Env var | OLLAMA_MODEL (str; default "qwen2.5-coder:3b") | `backend/venture_intelligence/config.py:80` | needs test |
| P-333 | Env var | OLLAMA_TIMEOUT_SECONDS (float; default 20.0) | `backend/venture_intelligence/config.py:81` | needs test |
| P-334 | Env var | OLLAMA_INPUT_CHARS (int; default 4_000) | `backend/venture_intelligence/config.py:82` | needs test |
| P-335 | Env var | OLLAMA_OUTPUT_TOKENS (int; default 400) | `backend/venture_intelligence/config.py:83` | needs test |
| P-336 | Env var | ANALYST_ENABLED (bool; default True) | `backend/venture_intelligence/config.py:87` | needs test |
| P-337 | Env var | ANALYST_PROVIDER (str; default "auto") | `backend/venture_intelligence/config.py:88` | needs test |
| P-338 | Env var | BROWSER_TIMEOUT_MS (int; default 30_000) | `backend/venture_intelligence/config.py:90` | needs test |
| P-339 | Env var | NAME_MATCH_THRESHOLD (float; default 0.82) | `backend/venture_intelligence/config.py:92` | needs test |
| P-340 | Env var | ALERT_MIN_CONFIDENCE (float; default 0.65) | `backend/venture_intelligence/config.py:93` | needs test |
| P-341 | Env var | ALERT_TOP_N_RANK (int; default 100) | `backend/venture_intelligence/config.py:94` | needs test |
| P-342 | Env var | SLACK_WEBHOOK_URL (str \| None; default None) | `backend/venture_intelligence/config.py:95` | needs test |
| P-343 | Env var | EMAIL_CONFIG_PATH (str; default "data/email_notifications.json") | `backend/venture_intelligence/config.py:96` | needs test |
| P-344 | Env var | EMAIL_SMTP_HOST (str \| None; default None) | `backend/venture_intelligence/config.py:97` | needs test |
| P-345 | Env var | EMAIL_SMTP_PORT (int; default 587) | `backend/venture_intelligence/config.py:98` | needs test |
| P-346 | Env var | EMAIL_SMTP_USERNAME (str \| None; default None) | `backend/venture_intelligence/config.py:99` | needs test |
| P-347 | Env var | EMAIL_SMTP_PASSWORD (str \| None; default None) | `backend/venture_intelligence/config.py:100` | needs test |
| P-348 | Env var | EMAIL_SMTP_FROM (str \| None; default None) | `backend/venture_intelligence/config.py:101` | needs test |
| P-349 | Env var | API_HOST (str; default "127.0.0.1") | `backend/venture_intelligence/config.py:102` | needs test |
| P-350 | Env var | API_PORT (int; default 8000) | `backend/venture_intelligence/config.py:103` | needs test |
| P-351 | Env var | EMAIL_SMTP_STARTTLS / SMTP_STARTTLS (os.getenv only; default true) | `backend/venture_intelligence/pipeline/email.py:231` | needs test |
| P-352 | Env var | SMTP_HOST / SMTP_USERNAME / SMTP_PASSWORD / SMTP_FROM aliases (os.getenv only) | `backend/venture_intelligence/pipeline/email.py:226` | needs test |
| P-353 | Env var | VENTURE_INTELLIGENCE_LIVE_TESTS=1 live network gate | `backend/venture_intelligence/operations/validation.py:94` | test_live_gate_and_hard_caps |
