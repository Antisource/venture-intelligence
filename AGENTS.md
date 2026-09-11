# Venture Intelligence — Agent Guide

## Purpose

This project gathers public, environmental venture signals and ranks evidence
for accelerator-associated founders and companies. It does not prove admission
from weak social or community signals.

## Architecture

- `config/programs.yaml`: single source of truth for accelerator programs.
- `venture_intelligence/programs.py`: typed `ProgramRegistry` (`active`, `get`,
  `by_batch`, case-insensitive text resolution, and program-prefixed IDs).
- `venture_intelligence/connectors/`: generic public-source connectors and a
  central factory. Public website templates use `website_urls`; do not add
  hardcoded accelerator-specific connector classes.
- `venture_intelligence/crawler.py`: shared bounded public-web lifecycle; never
  use it to bypass authentication, CAPTCHAs, rate limits, robots rules, or
  private access controls. Optional rendered public pages use Playwright; the
  host circuit breaker prevents repeated failures from hammering one source.
  `crawler_engine=crawlee` uses the Node `PlaywrightCrawler` adapter in
  `scripts/crawlee_public_fetcher.mjs`; the root package installs Crawlee.
- `venture_intelligence/shared`, `pipeline`, and `storage`: canonical event,
  provenance/evidence, ranking, CRM, and SQLite pipeline.
- `venture_intelligence/api`: backend API. The built React/Vite application is
  served at `/` and must stay free of backend logic. Canonical API, evaluation,
  analyst, graph, and WebSocket surfaces remain available; retired legacy UI
  code must not be reintroduced as a second product boundary.
- `venture_intelligence/agents/environmental_extractor.py`: optional local
  Ollama structured extraction with deterministic fallback. It is separate from
  the conversational analyst boundary.

## Running

Use `uv sync --all-groups`, `uv run pytest -q`, and
`uv run python -m venture_intelligence.monitor`. Copy `.env.example` to `.env`
for local settings. Tests must not require credentials, browser sessions,
external APIs, private Discord, a running Ollama server, or GPU hardware.

The default extractor model is `qwen2.5-coder:3b`. An optional custom model
can be created with `ollama create venture-intelligence-extractor -f
ollama_modelfile/Modelfile`; it is based on Qwen. Keep the Python default,
Modelfile, and README consistent.

## Important endpoints

`GET /api/v1/programs` exposes registry data. `POST /api/v1/intel/gather`
performs a bounded, best-effort on-demand pass. Keep legacy `/api/*` dashboard
routes working while evolving versioned APIs.

## Operational rules

Preserve event provenance and calibrated confidence. Close connector resources;
one connector failure must not stop monitoring. Do not include `.env`, database
files, browser profiles, virtual environments, or caches in release archives.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
