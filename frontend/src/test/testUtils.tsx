import { QueryClient } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { vi } from "vitest";

import { AppProviders } from "../app/AppProviders";
import { appRoutes } from "../app/router";

const program = {
  id: "yc_current",
  name: "Y Combinator",
  batch: "F26",
  canonical_program_id: "yc",
  current_batch: "F26",
  supported_batches: [
    { id: "F26", name: "Fall 2026", directory_url: "https://www.ycombinator.com/companies?batch=Fall%202026", keywords: ["YC F26", "Fall 2026"], historical: false },
    { id: "S26", name: "Summer 2026", directory_url: "https://www.ycombinator.com/companies?batch=Summer%202026", keywords: ["YC S26", "Summer 2026"], historical: true },
  ],
  active: true,
  detection_strategy: "directory",
  directory_url: "https://www.ycombinator.com/companies",
  talent_page_url: null,
  substack_rss_url: null,
  blog_urls: [],
  website_urls: ["https://www.ycombinator.com"],
  social_search_urls: [],
  discord_servers: [],
  demo_day: null,
  base_confidence: 0.95,
  keywords: ["Y Combinator"],
  entity_id_prefix: "yc",
  color: "#f26522",
};

const speedrunProgram = {
  ...program,
  id: "speedrun_current",
  name: "a16z Speedrun",
  batch: "SR007",
  canonical_program_id: "speedrun",
  current_batch: "SR007",
  supported_batches: [
    { id: "SR007", name: "Speedrun 007", directory_url: null, keywords: ["Speedrun 007", "SR007"], historical: false },
  ],
  detection_strategy: "triangulation",
  directory_url: null,
  talent_page_url: "https://speedrun.a16z.com/talent",
  entity_id_prefix: "speedrun",
  keywords: ["a16z Speedrun"],
};

const batchCandidate = {
  hypothesis_id: "hypothesis-alice",
  revision_id: "revision-alice-1",
  revision: 1,
  run_id: "run-m8",
  program_id: "yc_current",
  batch_id: "F26",
  candidate_kind: "person",
  display_name: "Alice Chen",
  candidate_name: "Alice Chen",
  company_name: "Acme Labs",
  aliases: [],
  usernames: ["alicechen"],
  domains: ["acme.example"],
  urls: ["https://acme.example/announcement", "https://founderletter.example/acme"],
  cofounders: [],
  observation_ids: ["observation-alice", "observation-newsletter"],
  state: "high_confidence",
  assessment: {
    confidence_pct: 91,
    identity_confidence_pct: 94,
    independent_supporting_sources: 2,
    supporting_observations: 2,
    contradictory_observations: 0,
    other_batch_observations: 0,
    ambiguous_observations: 0,
    stale_observations: 0,
    derivative_observations: 1,
    source_diversity: ["acme.example", "founderletter.example"],
    components: [
      { component: "independent_support", effect: 0.91, explanation: "Two independent origins.", observation_ids: ["observation-alice", "observation-newsletter"] },
      { component: "identity", effect: 0.94, explanation: "Exact identity and company association.", observation_ids: ["observation-alice"] },
      { component: "contradictions", effect: 0, explanation: "No active contradictions.", observation_ids: [] },
    ],
    promotion_eligible: true,
    promotion_blockers: [],
  },
  profile: {
    founder_name: "Alice Chen",
    company_name: "Acme Labs",
    website: "https://acme.example/announcement",
    linkedin_url: "https://linkedin.com/in/alicechen",
    github_url: "https://github.com/alicechen",
    github_username: "alicechen",
    technical_profiles: ["https://github.com/alicechen"],
    publications: [],
    background_claims: ["Built public developer infrastructure"],
    cofounders: [],
    completeness_pct: 75,
  },
  first_seen_at: "2026-08-01T09:00:00Z",
  last_updated_at: "2026-08-01T09:00:00Z",
  review_state: "unreviewed",
  promoted_person_source_id: "yc__person__alice-chen",
  promoted_company_source_id: "yc__company__acme-labs",
  input_digest: "candidate-digest",
  provenance: { clustering: "exact_normalized_identity_company" },
};

const batchObservation = {
  observation_id: "observation-alice",
  run_id: "run-m8",
  program_id: "yc_current",
  batch_id: "F26",
  candidate_kind: "person",
  candidate_name: "Alice Chen",
  company_name: "Acme Labs",
  claim: "Alice Chen says Acme Labs joined Y Combinator F26.",
  stance: "supports",
  source_roles: ["DISCOVERY", "IDENTITY", "VERIFICATION"],
  source_url: "https://acme.example/announcement",
  canonical_url: "https://acme.example/announcement",
  source_domain: "acme.example",
  source_provider: "fixture-public-index",
  query: "Y Combinator S26 joining founder",
  origin_key: "origin-alice",
  independence: "independent",
  source_reliability: 0.92,
  identity_confidence: 0.94,
  directness: 0.94,
  temporal_relevance: 0.96,
  published_at: "2026-07-30T10:00:00Z",
  observed_at: "2026-08-01T09:00:00Z",
  retrieved_at: "2026-08-01T09:00:00Z",
  provenance: { access_mode: "direct_public" },
  raw_observation: {},
};

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function installApiMock(
  searchResults: unknown[] = [],
  evaluationRun?: Record<string, unknown>,
  overrides?: { rankingItems?: unknown[]; batchCandidates?: unknown[] },
) {
  const decision = {
    schema_version: "5.0",
    as_of: "2026-02-01T12:00:00Z",
    intelligence: {
      schema_version: "4.0",
      as_of: "2026-02-01T12:00:00Z",
      entity: {
        entity_id: "company-acme",
        entity_type: "company",
        canonical_name: "Acme Labs",
        identity_confidence: 0.96,
        validation_status: "validated",
        founder_ids: [],
      },
      evidence: [],
      features: [],
      signals: [],
      hypotheses: [],
      ranking: {
        ranking_id: "ranking-acme",
        ranking_version: "m5-ranking-1",
        entity_id: "company-acme",
        rank: 1,
        composite_score: 0.81,
        dimensions: [],
        hypothesis_ids: [],
        evidence_ids: ["evidence-acme"],
        signal_ids: ["signal-feedback"],
        feature_ids: ["feature-feedback"],
        crm_action_ids: ["action-interested"],
        input_digest: "ranking-digest",
        reasoning: "Validated public evidence with bounded partner feedback.",
        confidence: 0.91,
        ranked_at: "2026-02-01T12:00:00Z",
      },
      ranking_history: [],
      movement: { current_rank: 1, previous_rank: 2, rank_delta: 1, score_delta: 0.03 },
      recommendation: null,
      crm_actions: [],
      lineage: [],
    },
    crm: { entity_id: "company-acme", stage: "partner_review", notes: [] },
    allowed_transitions: ["investigation", "passed"],
    actions: [{
      crm_event_id: "action-interested",
      schema_version: "5.0",
      action_version: "m5-crm-action-1",
      entity_id: "company-acme",
      action: "interested",
      feedback_kind: "human_judgment",
      from_stage: "partner_review",
      to_stage: "partner_review",
      partner: "A. Partner",
      note: "Schedule technical diligence.",
      provenance: { channel: "partner_review", authoritative_ground_truth: false },
      occurred_at: "2026-02-01T10:00:00Z",
      observed_at: "2026-02-01T10:01:00Z",
      input_digest: "action-digest",
    }],
    feedback_features: [{
      feature_id: "feature-feedback",
      schema_version: "5.0",
      feature_version: "m5-feedback-1",
      entity_id: "company-acme",
      name: "human_feedback_positive_count",
      value: 1,
      unit: "count",
      source: "crm",
      observed_at: "2026-02-01T10:01:00Z",
      as_of: "2026-02-01T10:01:00Z",
      computed_at: "2026-02-01T10:01:00Z",
      evidence_ids: ["evidence-acme"],
      crm_action_ids: ["action-interested"],
      formula: "count(bounded_positive_partner_actions)",
      parameters: { authoritative_ground_truth: false },
      input_digest: "feature-digest",
      confidence: 0.75,
    }],
    feedback_signals: [],
    recommendations: [{
      recommendation: {
        recommendation_id: "recommendation-acme",
        schema_version: "5.0",
        recommendation_version: "m5-recommendation-1",
        entity_id: "company-acme",
        action: "contact",
        reasoning: "Validated public evidence warrants partner contact.",
        confidence: 0.88,
        evidence_ids: ["evidence-acme"],
        feature_ids: ["feature-feedback"],
        signal_ids: ["signal-feedback"],
        crm_action_ids: ["action-interested"],
        input_digest: "recommendation-digest",
      },
      lifecycle: [{
        lifecycle_event_id: "rec-created",
        recommendation_id: "recommendation-acme",
        entity_id: "company-acme",
        status: "created",
        actor: "recommendation_worker",
        occurred_at: "2026-02-01T10:02:00Z",
      }],
    }, {
      recommendation: {
        recommendation_id: "recommendation-acme-previous",
        schema_version: "5.0",
        recommendation_version: "m5-recommendation-0",
        entity_id: "company-acme",
        action: "ignore",
        reasoning: "Earlier evidence did not clear the review threshold.",
        confidence: 0.55,
        evidence_ids: ["evidence-acme"],
        feature_ids: [],
        signal_ids: [],
        crm_action_ids: [],
        input_digest: "recommendation-old-digest",
      },
      lifecycle: [{
        lifecycle_event_id: "rec-superseded",
        recommendation_id: "recommendation-acme-previous",
        entity_id: "company-acme",
        status: "superseded",
        actor: "recommendation_worker",
        occurred_at: "2026-01-31T10:02:00Z",
      }],
    }],
    alerts: [{
      alert: {
        alert_id: "alert-acme",
        schema_version: "5.0",
        alert_version: "m5-alert-1",
        recommendation_id: "recommendation-acme",
        entity_id: "company-acme",
        entity_name: "Acme Labs",
        action: "contact",
        reasoning: "High-confidence validated recommendation.",
        confidence: 0.88,
        evidence_summary: ["Validated accelerator identity"],
        source_urls: ["https://acme.example"],
        crm_action_ids: ["action-interested"],
        fired_at: "2026-02-01T10:03:00Z",
      },
      lifecycle: [{
        lifecycle_event_id: "alert-delivered",
        alert_id: "alert-acme",
        recommendation_id: "recommendation-acme",
        entity_id: "company-acme",
        status: "delivered",
        actor: "notification_pipeline",
        occurred_at: "2026-02-01T10:03:00Z",
      }],
    }],
  };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.startsWith("/api/health")) {
      return response({ status: "ok", project: "venture-intelligence", db: "test.db" });
    }
    if (path.startsWith("/api/v1/programs")) {
      return response({ programs: [program, speedrunProgram] });
    }
    if (path === "/api/v1/system/readiness") {
      return response({
        current_program_id: "yc_current",
        current_batch: "F26",
        components: [
          { component: "Discovery", status: "DEGRADED", summary: "The latest bounded discovery run produced no hypotheses.", action: "Run bounded source checks or retry discovery.", technical_status: "no_new_evidence" },
          { component: "Verification", status: "READY", summary: "Directory evidence remains verification-only." },
          { component: "Email Delivery", status: "OPTIONAL_NOT_CONFIGURED", summary: "SMTP transport is configured; no destination is configured, so sending is disabled.", action: "Add a destination only when delivery is wanted." },
        ],
        social: [
          { platform: "LinkedIn", direct_api_available: false, indexed_discovery_available: true, manual_import_available: true, permission_required: true, live_validation_status: "NOT_RUN", required_configuration: "Approved API or indexed/manual evidence." },
        ],
        provider_policies: [],
        email: { transport_status: "CONFIGURED", destination_status: "NOT_CONFIGURED", sending_status: "DISABLED", last_outcome: null },
      });
    }
    if (path === "/api/v1/cycles") {
      return response({
        scheduler: { enabled: true, running: false, interval_hours: 12, last_cycle_id: null, last_run_at: null, next_run_at: null, updated_at: "2026-08-31T00:00:00Z" },
        cycles: [],
      });
    }
    if (path === "/api/v1/cycles/run") {
      return response({
        schema_version: "1.0", cycle_id: "cycle-ui", status: "completed", trigger: "api", started_at: "2026-08-31T00:00:00Z", finished_at: "2026-08-31T00:00:01Z", next_run_at: "2026-08-31T12:00:00Z", program_results: [], error: null,
      });
    }
    if (path === "/api/v1/scheduler") {
      const enabled = JSON.parse(String(init?.body ?? "{}")).enabled ?? true;
      return response({ enabled, running: false, interval_hours: 12, last_cycle_id: null, last_run_at: null, next_run_at: null, updated_at: "2026-08-31T00:00:00Z" });
    }
    if (path === "/api/v1/batch-discovery/contract") {
      return response({
        schema_version: "8.0",
        stages: ["discovery", "verification", "enrichment", "completed"],
        source_taxonomy: ["DISCOVERY", "IDENTITY", "VERIFICATION", "ENRICHMENT", "HISTORICAL"],
        promotion_min_confidence_pct: 72,
        promotion_min_independent_sources: 2,
        directory_role: "verification_and_fallback_seed_only",
        restricted_social_mode: "permitted_search_index_discovery_only",
        external_tools_execute_network_requests: false,
        tool_benchmarks: [],
      });
    }
    if (path === "/api/v1/batch-discovery/runs") {
      return response({
        schema_version: "8.0",
        engine_version: "m8-discovery-1",
        run_id: "run-m8",
        program_id: "yc_current",
        batch_id: "F26",
        stage: "completed",
        status: "completed",
        queries: [],
        source_outcomes: [],
        observations: [batchObservation],
        hypotheses: [batchCandidate],
        promoted_count: 1,
        started_at: "2026-08-01T09:00:00Z",
        finished_at: "2026-08-01T09:00:01Z",
        stop_reason: "completed",
        budget: {},
        input_digest: "run-digest",
      });
    }
    if (path.startsWith("/api/v1/batch-discovery?")) {
      return response({
        program_id: "yc_current",
        batch_id: "F26",
        candidates: overrides?.batchCandidates ?? [batchCandidate],
        source_contract: "Discovery proposes candidates; verification establishes identity and batch.",
      });
    }
    if (path.startsWith("/api/v1/batch-discovery/")) {
      return response({
        candidate: batchCandidate,
        observations: [batchObservation],
        immutable_evidence: true,
        historical_cutoff_field: "observed_at",
      });
    }
    if (path.startsWith("/api/v1/search")) return response(searchResults);
    if (path.startsWith("/api/v1/research")) {
      return response({
        target: { name: "Acme Labs", entity_type: "company" },
        evidence: [],
        sources: [],
        confidence_label: "insufficient",
        confidence: 0,
        stop_reason: "insufficient_evidence",
        failures: [],
      });
    }
    if (path.startsWith("/api/v1/graph")) {
      return response({
        schema_version: "7.0",
        as_of: "2026-02-01T12:00:00Z",
        historical_cutoff_field: "observed_at",
        projection_source: path.includes("as_of=") ? "isolated_replay" : "live",
        program_id: path.includes("speedrun_current") ? "speedrun_current" : null,
        focus_id: null,
        depth: 1,
        nodes: [
          {
            node_id: "company-acme",
            node_type: "Company",
            label: "Acme Labs",
            confidence: 0.96,
            program_id: "yc_current",
            first_observed_at: "2026-01-15T12:00:00Z",
            properties: { website: "https://acme.example" },
            provenance: {
              evidence_ids: ["event-acme"],
              source_urls: ["https://acme.example/about"],
            },
          },
          {
            node_id: "person-jane",
            node_type: "Person",
            label: "Jane Founder",
            confidence: 0.93,
            program_id: "yc_current",
            first_observed_at: "2026-01-15T12:00:00Z",
            properties: { title: "Founder" },
            provenance: {
              evidence_ids: ["event-jane"],
              source_urls: ["https://acme.example/team"],
            },
          },
        ],
        edges: [
          {
            edge_id: "edge-founded",
            source_id: "person-jane",
            target_id: "company-acme",
            edge_type: "FOUNDED",
            confidence: 0.94,
            observed_at: "2026-01-15T12:00:00Z",
            validity: {
              valid_from: "2025-11-01T00:00:00Z",
              valid_to: null,
              precision: "day",
              status: "current",
            },
            temporal_status: "current",
            validity_precision: "day",
            properties: {},
            provenance: {
              evidence_ids: ["event-jane"],
              source_urls: ["https://acme.example/team"],
            },
          },
        ],
        total_nodes: 2,
        total_edges: 1,
        truncated: false,
        limits: { max_nodes: 220, max_edges: 440, max_depth: 2, max_replay_events: 5000 },
        available_node_types: ["Company", "Person"],
        available_edge_types: ["FOUNDED"],
        replay_projection_digest: path.includes("as_of=") ? "replay-digest" : null,
        semantics: "Historical snapshots use observed_at and isolated replay.",
      });
    }
    if (path.startsWith("/api/v1/intelligence/rankings")) {
      return response({
        schema_version: "4.0",
        as_of: "2026-02-01T12:00:00Z",
        items: overrides?.rankingItems ?? [{
          entity: {
            entity_id: "company-acme",
            entity_type: "company",
            canonical_name: "Acme Labs",
            identity_confidence: 0.96,
            identity_status: "verified",
            program_status: "verified",
            independent_evidence_origins: 2,
            validation_status: "validated",
            program_id: "yc_current",
            batch_id: "F26",
            founder_ids: [],
          },
          ranking: {
            ranking_id: "ranking-acme",
            ranking_version: "m4-ranking-1",
            entity_id: "company-acme",
            rank: 1,
            composite_score: 0.81,
            dimensions: [],
            hypothesis_ids: [],
            evidence_ids: ["evidence-acme"],
            signal_ids: ["signal-acme"],
            feature_ids: ["feature-acme"],
            input_digest: "ranking-digest",
            reasoning: "Program evidence and technical depth.",
            confidence: 0.91,
            ranked_at: "2026-02-01T12:00:00Z",
          },
          movement: { current_rank: 1, previous_rank: 3, rank_delta: 2, score_delta: 0.09 },
          top_signal: {
            signal_id: "signal-acme",
            signal_version: "m4-signal-1",
            entity_id: "company-acme",
            signal_type: "technical_depth",
            value: 0.84,
            evidence_ids: ["evidence-acme"],
            feature_ids: ["feature-acme"],
            input_digest: "signal-digest",
            explanation: "Repository presence and commits",
            confidence: 0.9,
            observed_at: "2026-02-01T12:00:00Z",
          },
        }],
      });
    }
    if (path.startsWith("/api/v1/intelligence/entities/")) {
      return response({
        schema_version: "4.0",
        as_of: "2026-02-01T12:00:00Z",
        entity: {
          entity_id: "company-acme",
          entity_type: "company",
          canonical_name: "Acme Labs",
          identity_confidence: 0.96,
          identity_status: "verified",
          program_status: "verified",
          independent_evidence_origins: 2,
          validation_status: "validated",
          program_id: "yc_current",
          batch_id: "F26",
          founder_ids: [],
        },
        evidence: [{
          evidence_id: "evidence-acme",
          claim: "Has active public repository",
          confidence: 0.9,
          observed_at: "2026-01-30T12:00:00Z",
          observation_ids: ["observation-acme"],
          source_urls: ["https://github.com/acme/core"],
          sources: ["github"],
          attribution_status: "attached",
          attribution_reason: "Declared first-party or verified identity source.",
          evidence_origin_url: "https://github.com/acme/core",
          acquisition_providers: ["github"],
          occurred_at: "2026-01-29T12:00:00Z",
          published_at: "2026-01-29T12:00:00Z",
        }, {
          evidence_id: "evidence-name-collision",
          claim: "A different person with the same name appeared in an entertainment profile",
          confidence: 0.45,
          observed_at: "2026-01-31T12:00:00Z",
          observation_ids: ["observation-name-collision"],
          source_urls: ["https://www.imdb.com/name/nm0000001"],
          sources: ["public_search"],
          attribution_status: "unbound",
          attribution_reason: "Name-only match without company or founder context.",
          evidence_origin_url: "https://www.imdb.com/name/nm0000001",
          acquisition_providers: ["duckduckgo"],
          occurred_at: null,
          published_at: null,
        }],
        features: [{
          feature_id: "feature-acme",
          schema_version: "4.0",
          feature_version: "m4-feature-1",
          entity_id: "company-acme",
          name: "github_stars",
          value: 144,
          unit: "count",
          source: "github",
          observed_at: "2026-02-01T12:00:00Z",
          as_of: "2026-02-01T12:00:00Z",
          computed_at: "2026-02-01T12:00:00Z",
          evidence_ids: ["evidence-acme"],
          observation_ids: ["observation-acme"],
          formula: "sum(repository_star_observation_values)",
          parameters: { input_count: 1 },
          input_digest: "feature-digest",
          confidence: 0.9,
        }],
        signals: [{
          signal_id: "signal-acme",
          schema_version: "4.0",
          signal_version: "m4-signal-1",
          entity_id: "company-acme",
          signal_type: "technical_depth",
          value: 0.84,
          evidence_ids: ["evidence-acme"],
          feature_ids: ["feature-acme"],
          input_digest: "signal-digest",
          explanation: "Repository presence and commits",
          confidence: 0.9,
          observed_at: "2026-02-01T12:00:00Z",
        }],
        hypotheses: [],
        ranking: {
          ranking_id: "ranking-acme",
          ranking_version: "m4-ranking-1",
          entity_id: "company-acme",
          rank: 1,
          composite_score: 0.81,
          dimensions: [],
          hypothesis_ids: [],
          evidence_ids: ["evidence-acme"],
          signal_ids: ["signal-acme"],
          feature_ids: ["feature-acme"],
          input_digest: "ranking-digest",
          reasoning: "Program evidence and technical depth.",
          confidence: 0.91,
          ranked_at: "2026-02-01T12:00:00Z",
        },
        ranking_history: [{
          ranking_id: "ranking-acme",
          ranking_version: "m4-ranking-1",
          entity_id: "company-acme",
          rank: 1,
          composite_score: 0.81,
          dimensions: [],
          hypothesis_ids: [],
          evidence_ids: ["evidence-acme"],
          signal_ids: ["signal-acme"],
          feature_ids: ["feature-acme"],
          input_digest: "ranking-digest",
          reasoning: "Program evidence and technical depth.",
          confidence: 0.91,
          ranked_at: "2026-02-01T12:00:00Z",
        }],
        movement: { current_rank: 1, previous_rank: 3, rank_delta: 2, score_delta: 0.09 },
        recommendation: null,
        lineage: [
          { from_id: "observation-acme", from_type: "observation", to_id: "evidence-acme", to_type: "evidence", relation: "supports" },
          { from_id: "evidence-acme", from_type: "evidence", to_id: "feature-acme", to_type: "feature", relation: "computes" },
          { from_id: "feature-acme", from_type: "feature", to_id: "signal-acme", to_type: "signal", relation: "normalizes" },
        ],
      });
    }
    if (path === "/api/v1/decisions") {
      return response({
        schema_version: "5.0",
        as_of: "2026-02-01T12:00:00Z",
        stages: {
          discovery: [], qualification: [], partner_review: [{
            entity: decision.intelligence.entity,
            crm: decision.crm,
            allowed_transitions: decision.allowed_transitions,
            action_count: 1,
            feedback_features: decision.feedback_features,
            ranking: decision.intelligence.ranking,
            recommendation: decision.recommendations[0]!.recommendation,
            recommendation_status: "created",
            latest_alert: decision.alerts[0]!.alert,
            alert_status: "delivered",
          }], investigation: [], relationship: [], investment: [], portfolio: [], passed: [],
        },
      });
    }
    if (path.endsWith("/actions") && path.startsWith("/api/v1/decisions/entities/")) {
      return response({ action: decision.actions[0], decision });
    }
    if (path.startsWith("/api/v1/decisions/entities/")) return response(decision);
    if (path.startsWith("/api/v1/decisions/recommendations/")) {
      return response({ ...decision.recommendations[0]!.lifecycle[0], status: "accepted" });
    }
    if (path.startsWith("/api/v1/decisions/alerts/")) {
      return response({ ...decision.alerts[0]!.lifecycle[0], status: "acknowledged" });
    }
    if (path === "/api/v1/source-quality/contract") {
      return response({
        schema_version: "9.0",
        execution_boundary: "local_cli_only",
        cli_command: "gated command",
        required_gate: "VENTURE_INTELLIGENCE_LIVE_TESTS=1 plus --allow-network",
        read_only: true,
        maximum_requests: 24,
        direct_social_access_enabled: false,
        metrics_without_ground_truth: ["discovery yield"],
        metrics_requiring_ground_truth: ["precision at K", "recall"],
      });
    }
    if (path === "/api/v1/source-quality") {
      return response({
        available: true,
        message: "Latest gated live validation report.",
        report: {
          schema_version: "9.0",
          run_id: "live-1",
          program_id: "yc_current",
          batch_id: "F26",
          mode: "gated_live_read_only",
          live_network_executed: true,
          operational_store_mutated: false,
          temporary_store_isolated: true,
          direct_social_access_enabled: false,
          started_at: "2026-08-26T00:00:00Z",
          finished_at: "2026-08-26T00:01:00Z",
          budget: {
            max_queries: 8, max_depth: 1, max_results_per_query: 3,
            max_runtime_seconds: 90, max_total_requests: 24,
            max_requests_per_provider: 12, max_retry_requests: 3,
            max_requests_per_domain: 12,
          },
          stop_reason: "completed",
          source_diagnostics: [{
            source_id: "public-program-osint:discovery",
            state: "healthy",
            operation_status: "SUCCESS",
            discovery_role: "discovery",
            requests: 2,
            retries: 0,
            redirects: 0,
            results: 3,
            accepted_observations: 1,
            unique_origins: 1,
            latency_ms: 120,
            indexed_social_hosts: ["linkedin.com"],
            direct_restricted_social_requests: 0,
            final_urls: [],
            error: null,
            drift_reason: null,
            retryable: false,
          }],
          discovery_quality: {
            unknown_hypotheses: 1,
            promotion_eligible: 0,
            canonical_promotions: 0,
            precision_at_k: null,
            recall: null,
            false_positive_rate: null,
            mean_lead_time_days: null,
            source_contribution: { "linkedin.com": 1 },
            source_ablation_promotion_loss: {},
            ground_truth_available: false,
            ground_truth_note: "Metrics require timestamped ground truth.",
          },
          blocked_or_unavailable_sources: [],
          fixture_only_capabilities: [],
          operational_blind_spots: [],
          report_digest: "digest",
        },
      });
    }
    if (path.startsWith("/api/v1/sources")) {
      return response({
        providers: [
          {
            provider_id: "arxiv",
            display_name: "arXiv official API",
            capabilities: ["discovery", "publication"],
            access_method: "public_api",
            access_mode: "approved_api",
            evidence_role: "both",
            default_enabled: true,
            requires_credentials: false,
            live_test_supported: true,
            restricted_hosts: [],
            rate_limit_note: null,
            fallback: null,
          },
        ],
        connectors: [
          {
            provider_id: "linkedin",
            display_name: "LinkedIn direct-site connector",
            capabilities: ["discovery", "identity"],
            access_method: "public_html",
            access_mode: "permission_required",
            evidence_role: "discovery",
            default_enabled: false,
            requires_credentials: false,
            live_test_supported: false,
            restricted_hosts: ["linkedin.com"],
            rate_limit_note: null,
            fallback: "Use permitted search-provider indexed discovery.",
          },
          {
            provider_id: "product_hunt",
            display_name: "Product Hunt official GraphQL connector",
            capabilities: ["discovery", "company_activity"],
            access_method: "public_api",
            access_mode: "permission_required",
            evidence_role: "discovery",
            default_enabled: false,
            requires_credentials: true,
            live_test_supported: true,
            restricted_hosts: [],
            rate_limit_note: null,
            fallback: "Use permitted indexed references.",
          },
        ],
        runtime: [{ source_id: "product_hunt", enabled: true, health: "healthy", configuration_status: "configured", credential_present: true, last_checked_at: "2026-08-26T00:01:00Z", last_successful_fetch_at: "2026-08-26T00:01:00Z", new_item_count: 1, manually_overridden: false, note: null }],
        watchlists: [{
          id: "a16z-news",
          name: "a16z News and Content",
          kind: "vc_firm",
          organization: "Andreessen Horowitz",
          aliases: ["a16z"],
          roles: ["DISCOVERY", "FUNDING", "INVESTOR_SIGNAL", "TEMPORAL_NEWS"],
          public_urls: ["https://a16z.com/news-content/"],
          feed_urls: [],
          social_urls: [],
          sectors: ["venture"],
          active: true,
        }],
        direct_social_access_enabled: false,
        discovery_boundary: "Indexed social evidence is discovery-only.",
      });
    }
    if (path.startsWith("/api/v1/system/temporal-contract")) {
      return response({
        schema_version: "1.1",
        default_historical_cutoff_field: "observed_at",
        fields: [
          { field: "occurred_at", meaning: "World time", required: true },
          { field: "observed_at", meaning: "Observation time", required: true },
          { field: "ingested_at", meaning: "Persistence time", required: true },
          { field: "validity", meaning: "Explicit validity", required: false },
        ],
        invariant: "Observation does not imply validity.",
        replay_available: true,
      });
    }
    if (path.startsWith("/api/v1/replay/contract")) {
      return response({
        schema_version: "2.0",
        engine_version: "m2-replay-1",
        default_mode: "observed_at",
        supported_modes: ["observed_at", "occurred_at"],
        isolated_store: "temporary_sqlite",
        remote_execution_enabled: false,
        cli_command: "uv run python -m venture_intelligence.replay.cli --until <ISO-8601-with-offset>",
        max_events: 25000,
        side_effects_suppressed: ["alerts", "email", "crm", "external_collection"],
      });
    }
    if (path.startsWith("/api/v1/evaluations/contract")) {
      return response({
        schema_version: "3.0",
        engine_version: "m3-evaluation-1",
        default_replay_mode: "observed_at",
        historical_cutoff_field: "observed_at",
        remote_execution_enabled: false,
        import_cli_command: "evaluation import",
        run_cli_command: "evaluation run",
        supported_metrics: ["precision_at_10", "recall"],
        target_levels: ["company", "person", "both"],
        source_ablation_enabled: true,
        side_effects_suppressed: ["alerts", "email", "crm"],
      });
    }
    if (path === "/api/v1/evaluations") {
      return response({
        evaluations: evaluationRun ? [{
          evaluation_run_id: evaluationRun.evaluation_run_id,
          status: evaluationRun.status,
          program_id: evaluationRun.program_id,
          batch_id: evaluationRun.batch_id,
          target_level: evaluationRun.target_level,
          cutoff: evaluationRun.cutoff,
          outcome_timestamp: evaluationRun.outcome_timestamp,
          report_digest: evaluationRun.report_digest,
          created_at: evaluationRun.created_at,
          metrics: evaluationRun.metrics,
        }] : [],
      });
    }
    if (path.startsWith("/api/v1/evaluations/") && evaluationRun) {
      return response(evaluationRun);
    }
    if (path.startsWith("/api/stats")) {
      return response({ total_events: 0, total_companies: 0, total_signals: 0, total_alerts: 0 });
    }
    if (path.startsWith("/api/metrics")) return response({ source_health: {} });
    if (path.startsWith("/api/alerts")) return response({ alerts: [] });
    if (path.startsWith("/api/crm")) return response({ board: {} });
    if (path.startsWith("/api/review-queue")) return response({ total: 0, results: [] });
    if (path.startsWith("/api/companies/evidence")) return response({ total: 0, results: [] });
    if (path.startsWith("/api/graph?")) return response({ nodes: [], edges: [] });
    if (path.startsWith("/api/investigations")) return response({ investigations: [] });
    if (path === "/api/settings/email/test") return response({ status: "sent" });
    if (path === "/api/settings/email/digest") {
      return response({ status: "preview", delivery_configured: false, notice: "DELIVERY NOT CONFIGURED — PREVIEW AVAILABLE", subject: "Venture Intelligence partner digest", body: "No validated candidates or meaningful changes are available.", links: [] });
    }
    if (path.startsWith("/api/settings/email") && init?.method === "PUT") {
      return response({ destination: "test@example.com", preferences: { enabled: true }, runtime: { transport_status: "CONFIGURED", destination_status: "CONFIGURED", sending_status: "ENABLED", last_outcome: null } });
    }
    if (path.startsWith("/api/settings/email")) {
      return response({ destination: null, preferences: { enabled: false }, runtime: { transport_status: "CONFIGURED", destination_status: "NOT_CONFIGURED", sending_status: "DISABLED", last_outcome: null } });
    }
    if (path.startsWith("/api/analyst/chat")) {
      return response({ answer: "Insufficient evidence.", model: "deterministic-fallback", evidence_sufficient: false });
    }
    return response({ detail: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export function renderApp(initialEntry = "/") {
  const router = createMemoryRouter(appRoutes, { initialEntries: [initialEntry] });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return {
    router,
    ...render(
      <AppProviders client={client}>
        <RouterProvider router={router} />
      </AppProviders>,
    ),
  };
}

export function renderWithClient(children: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(<AppProviders client={client}>{children}</AppProviders>);
}
