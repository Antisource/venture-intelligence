import type { components } from "./schema";

export type HealthResponse = components["schemas"]["HealthResponse"];
export type Program = components["schemas"]["ProgramConfig"];
export type ProgramListResponse = components["schemas"]["ProgramListResponse"];
export type ReplayContractResponse = components["schemas"]["ReplayContractResponse"];
export type EvaluationContractResponse = components["schemas"]["EvaluationContractResponse"];
export type EvaluationListResponse = components["schemas"]["EvaluationListResponse"];
export type EvaluationRun = components["schemas"]["EvaluationRun"];
export type SearchSuggestion = components["schemas"]["SearchSuggestion"];
export type SourcePolicyResponse = components["schemas"]["SourcePolicyResponse"];
export type SourceRuntimeStatus = components["schemas"]["SourceRuntimeStatus"];
export type SourceQualityResponse = components["schemas"]["SourceQualityResponse"];
export type OperationalValidationContract =
  components["schemas"]["OperationalValidationContract"];
export type TemporalContractResponse = components["schemas"]["TemporalContractResponse"];
export type RankingIntelligenceResponse = components["schemas"]["RankingIntelligenceResponse"];
export type EntityIntelligenceResponse = components["schemas"]["EntityIntelligenceResponse"];
export type DecisionBoardResponse = components["schemas"]["DecisionBoardResponse"];
export type DecisionEntityResponse = components["schemas"]["DecisionEntityResponse"];
export type CRMActionRequest = components["schemas"]["CRMActionRequest"];
export type CRMActionResult = components["schemas"]["CRMActionResult"];
export type RecommendationLifecycleRequest = components["schemas"]["RecommendationLifecycleRequest"];
export type RecommendationLifecycleEvent = components["schemas"]["RecommendationLifecycleEvent"];
export type AlertLifecycleRequest = components["schemas"]["AlertLifecycleRequest"];
export type AlertLifecycleEvent = components["schemas"]["AlertLifecycleEvent"];
export type CRMActionType = components["schemas"]["CRMActionType"];
export type CRMStage = components["schemas"]["CRMStage"];
export type GraphSnapshotResponse = components["schemas"]["GraphSnapshotResponse"];
export type TemporalGraphNode = components["schemas"]["TemporalGraphNode"];
export type TemporalGraphEdge = components["schemas"]["TemporalGraphEdge"];
export type BatchDiscoveryContractResponse = components["schemas"]["BatchDiscoveryContractResponse"];
export type BatchDiscoveryListResponse = components["schemas"]["BatchDiscoveryListResponse"];
export type BatchDiscoveryRequest = components["schemas"]["BatchDiscoveryRequest"];
export type BatchDiscoveryRun = components["schemas"]["BatchDiscoveryRun"];
export type CandidateHypothesis = components["schemas"]["CandidateHypothesis"];
export type BatchDiscoveryObservation = components["schemas"]["BatchDiscoveryObservation"];
export type BatchCandidateDetailResponse = components["schemas"]["BatchCandidateDetailResponse"];
export type SystemReadiness = components["schemas"]["SystemReadiness"];
export type OperationsState = components["schemas"]["OperationsState"];
export type IntelligenceCycleRecord = components["schemas"]["IntelligenceCycleRecord"];
export type SchedulerState = components["schemas"]["SchedulerState"];
export type EmailSettingsResponse = components["schemas"]["EmailSettingsResponse"];
export type EmailSettingsRequest = components["schemas"]["EmailSettingsRequest"];

export interface ResearchEvidenceItem {
  url: string;
  title?: string | null;
  source_domain?: string | null;
  confidence_label?: string | null;
  confidence?: number | null;
  category?: string | null;
  claim?: string | null;
  attribution_status: "attached" | "unbound" | "contradictory";
  attribution_reason: string;
  evidence_origin_url?: string | null;
  acquisition_providers: string[];
}

export interface ResearchResponse {
  target: { name: string; entity_type?: string };
  evidence: ResearchEvidenceItem[];
  sources: string[];
  confidence_label: string;
  confidence?: number | null;
  stop_reason: string;
  failures?: Array<{ source?: string; status?: string; error?: string }>;
  identity_status: "verified" | "corroborated" | "ambiguous" | "provisional" | "unresolved";
  program_status: "verified" | "supported" | "hypothesis" | "unknown" | "contradicted";
  independent_evidence_origins: number;
  attached_evidence_count: number;
  unbound_evidence_count: number;
  contradictory_evidence_count: number;
}

export type AlertSummary = components["schemas"]["Alert"];

export interface AlertListResponse { alerts: AlertSummary[] }

export interface AnalystResponse {
  answer: string;
  model: string;
  evidence_sufficient: boolean;
  caveats?: string[];
  citations?: Array<{ label: string; url: string }>;
}

export interface EmailDigestResponse {
  status: string;
  delivery_configured?: boolean;
  notice?: string;
  destination?: string | null;
  subject?: string;
  body?: string;
  links?: string[];
  error?: string | null;
}

export interface GraphQueryParameters {
  asOf?: string;
  programId?: string;
  nodeTypes?: string[];
  minConfidence?: number;
  focusId?: string;
  depth?: number;
  maxNodes?: number;
  maxEdges?: number;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
    readonly cause?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: {
        Accept: "application/json",
        ...init?.headers,
      },
    });
  } catch (error) {
    throw new ApiError("The API is unavailable. Check that the backend is running.", null, error);
  }
  if (!response.ok) {
    throw new ApiError(
      `Request failed with status ${response.status}.`,
      response.status,
    );
  }
  try {
    return (await response.json()) as T;
  } catch (error) {
    throw new ApiError("The API returned malformed JSON.", response.status, error);
  }
}

function requireRecord(value: unknown, endpoint: string): asserts value is object {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new ApiError(`Malformed response from ${endpoint}.`, 200);
  }
}

function requireArray(value: unknown, endpoint: string): asserts value is unknown[] {
  if (!Array.isArray(value)) {
    throw new ApiError(`Malformed response from ${endpoint}.`, 200);
  }
}

export const apiClient = {
  async knowledge(entityType: string, entityId: string): Promise<components["schemas"]["KnowledgePage"] | null> {
    const path = `/api/knowledge/${encodeURIComponent(entityType)}/${encodeURIComponent(entityId)}`;
    try {
      const value: unknown = await requestJson(path);
      requireRecord(value, path);
      requireArray(Reflect.get(value, "claims"), path);
      requireRecord(Reflect.get(value, "evidence_by_category"), path);
      for (const items of Object.values(Reflect.get(value, "evidence_by_category") as Record<string, unknown>)) {
        requireArray(items, path);
        for (const item of items as unknown[]) requireRecord(item, path);
      }
      for (const claim of Reflect.get(value, "claims") as unknown[]) {
        requireRecord(claim, path);
        if (typeof Reflect.get(claim, "statement") !== "string") throw new ApiError(`Malformed response from ${path}.`, 200);
      }
      return value as components["schemas"]["KnowledgePage"];
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return null;
      throw error;
    }
  },
  async health(): Promise<HealthResponse> {
    const value: unknown = await requestJson("/api/health");
    requireRecord(value, "/api/health");
    return value as HealthResponse;
  },

  async programs(): Promise<ProgramListResponse> {
    const value: unknown = await requestJson("/api/v1/programs");
    requireRecord(value, "/api/v1/programs");
    requireArray(Reflect.get(value, "programs"), "/api/v1/programs");
    return value as ProgramListResponse;
  },

  async search(query: string, limit = 12): Promise<SearchSuggestion[]> {
    const parameters = new URLSearchParams({ q: query, limit: String(limit) });
    const value: unknown = await requestJson(`/api/v1/search?${parameters}`);
    requireArray(value, "/api/v1/search");
    return value as SearchSuggestion[];
  },

  async sourcePolicies(): Promise<SourcePolicyResponse> {
    const value: unknown = await requestJson("/api/v1/sources");
    requireRecord(value, "/api/v1/sources");
    requireArray(Reflect.get(value, "providers"), "/api/v1/sources");
    requireArray(Reflect.get(value, "connectors"), "/api/v1/sources");
    return value as SourcePolicyResponse;
  },

  async updateSourceSetting(
    sourceId: string,
    enabled: boolean,
  ): Promise<SourceRuntimeStatus> {
    const value: unknown = await requestJson(
      `/api/v1/sources/${encodeURIComponent(sourceId)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      },
    );
    requireRecord(value, `/api/v1/sources/${sourceId}`);
    return value as SourceRuntimeStatus;
  },

  async sourceQuality(): Promise<SourceQualityResponse> {
    const value: unknown = await requestJson("/api/v1/source-quality");
    requireRecord(value, "/api/v1/source-quality");
    const report = Reflect.get(value, "report");
    if (report !== null && report !== undefined) {
      requireRecord(report, "/api/v1/source-quality.report");
      requireArray(
        Reflect.get(report, "source_diagnostics"),
        "/api/v1/source-quality.report.source_diagnostics",
      );
    }
    return value as SourceQualityResponse;
  },

  async sourceQualityContract(): Promise<OperationalValidationContract> {
    const value: unknown = await requestJson("/api/v1/source-quality/contract");
    requireRecord(value, "/api/v1/source-quality/contract");
    requireArray(
      Reflect.get(value, "metrics_without_ground_truth"),
      "/api/v1/source-quality/contract.metrics_without_ground_truth",
    );
    requireArray(
      Reflect.get(value, "metrics_requiring_ground_truth"),
      "/api/v1/source-quality/contract.metrics_requiring_ground_truth",
    );
    return value as OperationalValidationContract;
  },

  async readiness(): Promise<SystemReadiness> {
    const value: unknown = await requestJson("/api/v1/system/readiness");
    requireRecord(value, "/api/v1/system/readiness");
    requireArray(Reflect.get(value, "components"), "/api/v1/system/readiness");
    requireArray(Reflect.get(value, "social"), "/api/v1/system/readiness");
    return value as SystemReadiness;
  },

  async cycles(): Promise<OperationsState> {
    const value: unknown = await requestJson("/api/v1/cycles");
    requireRecord(value, "/api/v1/cycles");
    requireArray(Reflect.get(value, "cycles"), "/api/v1/cycles");
    requireRecord(Reflect.get(value, "scheduler"), "/api/v1/cycles");
    return value as OperationsState;
  },

  async runCycle(programIds?: string[]): Promise<IntelligenceCycleRecord> {
    const value: unknown = await requestJson("/api/v1/cycles/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ program_ids: programIds?.length ? programIds : null }),
    });
    requireRecord(value, "/api/v1/cycles/run");
    requireArray(Reflect.get(value, "program_results"), "/api/v1/cycles/run");
    return value as IntelligenceCycleRecord;
  },

  async configureScheduler(enabled: boolean): Promise<SchedulerState> {
    const value: unknown = await requestJson("/api/v1/scheduler", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    requireRecord(value, "/api/v1/scheduler");
    return value as SchedulerState;
  },

  async batchDiscoveryContract(): Promise<BatchDiscoveryContractResponse> {
    const value: unknown = await requestJson("/api/v1/batch-discovery/contract");
    requireRecord(value, "/api/v1/batch-discovery/contract");
    requireArray(Reflect.get(value, "stages"), "/api/v1/batch-discovery/contract");
    requireArray(Reflect.get(value, "source_taxonomy"), "/api/v1/batch-discovery/contract");
    requireArray(Reflect.get(value, "tool_benchmarks"), "/api/v1/batch-discovery/contract");
    return value as BatchDiscoveryContractResponse;
  },

  async batchDiscovery(
    programId: string | null,
    includeRejected = true,
  ): Promise<BatchDiscoveryListResponse> {
    const parameters = new URLSearchParams({
      include_rejected: String(includeRejected),
    });
    if (programId !== null) parameters.set("program_id", programId);
    const value: unknown = await requestJson(`/api/v1/batch-discovery?${parameters}`);
    requireRecord(value, "/api/v1/batch-discovery");
    requireArray(Reflect.get(value, "candidates"), "/api/v1/batch-discovery");
    return value as BatchDiscoveryListResponse;
  },

  async batchCandidate(hypothesisId: string): Promise<BatchCandidateDetailResponse> {
    const value: unknown = await requestJson(
      `/api/v1/batch-discovery/${encodeURIComponent(hypothesisId)}`,
    );
    requireRecord(value, "/api/v1/batch-discovery/{hypothesis_id}");
    requireRecord(Reflect.get(value, "candidate"), "/api/v1/batch-discovery/{hypothesis_id}");
    requireArray(
      Reflect.get(value, "observations"),
      "/api/v1/batch-discovery/{hypothesis_id}",
    );
    return value as BatchCandidateDetailResponse;
  },

  async runBatchDiscovery(body: BatchDiscoveryRequest): Promise<BatchDiscoveryRun> {
    const value: unknown = await requestJson("/api/v1/batch-discovery/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    requireRecord(value, "/api/v1/batch-discovery/runs");
    requireArray(Reflect.get(value, "hypotheses"), "/api/v1/batch-discovery/runs");
    requireArray(Reflect.get(value, "source_outcomes"), "/api/v1/batch-discovery/runs");
    return value as BatchDiscoveryRun;
  },

  async research(body: {
    entity_name: string;
    entity_type: string;
    program_id: string | null;
    budget: {
      max_queries: number;
      max_depth: number;
      max_results_per_query: number;
      max_runtime_seconds: number;
    };
  }): Promise<ResearchResponse> {
    const value: unknown = await requestJson("/api/v1/research", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    requireRecord(value, "/api/v1/research");
    requireRecord(Reflect.get(value, "target"), "/api/v1/research");
    const rawEvidence = Reflect.get(value, "evidence");
    requireArray(rawEvidence, "/api/v1/research");
    requireArray(Reflect.get(value, "sources"), "/api/v1/research");
    const evidence = rawEvidence.map((item) => {
      requireRecord(item, "/api/v1/research");
      if (typeof Reflect.get(item, "url") !== "string") {
        throw new ApiError("Malformed response from /api/v1/research.", 200);
      }
      const attribution = Reflect.get(item, "attribution_status");
      const attributionStatus = ["attached", "unbound", "contradictory"].includes(
        String(attribution),
      ) ? attribution : "unbound";
      const providers = Reflect.get(item, "acquisition_providers");
      return {
        ...item,
        attribution_status: attributionStatus,
        attribution_reason: typeof Reflect.get(item, "attribution_reason") === "string"
          ? Reflect.get(item, "attribution_reason")
          : "legacy_response_without_identity_attribution",
        acquisition_providers: Array.isArray(providers)
          ? providers.filter((provider): provider is string => typeof provider === "string")
          : [],
      } as ResearchEvidenceItem;
    });
    const attached = evidence.filter((item) => item.attribution_status === "attached").length;
    const unbound = evidence.filter((item) => item.attribution_status === "unbound").length;
    const contradictory = evidence.length - attached - unbound;
    return {
      ...(value as Record<string, unknown>),
      evidence,
      identity_status: Reflect.get(value, "identity_status")
        ?? (evidence.length ? "ambiguous" : "unresolved"),
      program_status: Reflect.get(value, "program_status") ?? "unknown",
      independent_evidence_origins: Reflect.get(value, "independent_evidence_origins") ?? 0,
      attached_evidence_count: Reflect.get(value, "attached_evidence_count") ?? attached,
      unbound_evidence_count: Reflect.get(value, "unbound_evidence_count") ?? unbound,
      contradictory_evidence_count:
        Reflect.get(value, "contradictory_evidence_count") ?? contradictory,
    } as ResearchResponse;
  },

  async alerts(): Promise<AlertListResponse> {
    const value: unknown = await requestJson("/api/alerts?limit=50");
    requireRecord(value, "/api/alerts");
    requireArray(Reflect.get(value, "alerts"), "/api/alerts");
    return value as AlertListResponse;
  },

  async askAnalyst(question: string, contextName?: string): Promise<AnalystResponse> {
    const value: unknown = await requestJson("/api/analyst/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, context_name: contextName ?? null }),
    });
    requireRecord(value, "/api/analyst/chat");
    if (typeof Reflect.get(value, "answer") !== "string") {
      throw new ApiError("Malformed response from /api/analyst/chat.", 200);
    }
    return value as AnalystResponse;
  },

  async emailSettings(): Promise<EmailSettingsResponse> {
    const value: unknown = await requestJson("/api/settings/email");
    requireRecord(value, "/api/settings/email");
    requireRecord(Reflect.get(value, "preferences"), "/api/settings/email");
    return value as EmailSettingsResponse;
  },

  async updateEmailSettings(body: EmailSettingsRequest): Promise<EmailSettingsResponse> {
    const value: unknown = await requestJson("/api/settings/email", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    requireRecord(value, "/api/settings/email");
    return value as EmailSettingsResponse;
  },

  async testEmail(): Promise<{ status: string }> {
    const value: unknown = await requestJson("/api/settings/email/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    requireRecord(value, "/api/settings/email/test");
    return value as { status: string };
  },

  async emailDigest(send = false): Promise<EmailDigestResponse> {
    const value: unknown = await requestJson("/api/settings/email/digest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ send }),
    });
    requireRecord(value, "/api/settings/email/digest");
    return value as EmailDigestResponse;
  },

  async temporalContract(): Promise<TemporalContractResponse> {
    const value: unknown = await requestJson("/api/v1/system/temporal-contract");
    requireRecord(value, "/api/v1/system/temporal-contract");
    requireArray(Reflect.get(value, "fields"), "/api/v1/system/temporal-contract");
    return value as TemporalContractResponse;
  },

  async graph(parameters: GraphQueryParameters = {}): Promise<GraphSnapshotResponse> {
    const query = new URLSearchParams();
    if (parameters.asOf) query.set("as_of", parameters.asOf);
    if (parameters.programId) query.set("program_id", parameters.programId);
    if (parameters.nodeTypes?.length) query.set("node_types", parameters.nodeTypes.join(","));
    if (parameters.minConfidence !== undefined) {
      query.set("min_confidence", String(parameters.minConfidence));
    }
    if (parameters.focusId) query.set("focus_id", parameters.focusId);
    if (parameters.depth !== undefined) query.set("depth", String(parameters.depth));
    if (parameters.maxNodes !== undefined) query.set("max_nodes", String(parameters.maxNodes));
    if (parameters.maxEdges !== undefined) query.set("max_edges", String(parameters.maxEdges));
    const suffix = query.size ? `?${query}` : "";
    const value: unknown = await requestJson(`/api/v1/graph${suffix}`);
    requireRecord(value, "/api/v1/graph");
    const nodes = Reflect.get(value, "nodes");
    const edges = Reflect.get(value, "edges");
    const limits = Reflect.get(value, "limits");
    requireArray(nodes, "/api/v1/graph");
    requireArray(edges, "/api/v1/graph");
    requireRecord(limits, "/api/v1/graph");
    const nodeLimit = Reflect.get(limits, "max_nodes");
    const edgeLimit = Reflect.get(limits, "max_edges");
    if (
      typeof nodeLimit !== "number"
      || typeof edgeLimit !== "number"
      || nodeLimit < 1
      || nodeLimit > 400
      || edgeLimit < 1
      || edgeLimit > 800
      || nodes.length > nodeLimit
      || edges.length > edgeLimit
      || typeof Reflect.get(value, "as_of") !== "string"
    ) {
      throw new ApiError("Malformed response from /api/v1/graph.", 200);
    }
    for (const node of nodes) {
      requireRecord(node, "/api/v1/graph");
      requireRecord(Reflect.get(node, "provenance"), "/api/v1/graph");
      if (
        typeof Reflect.get(node, "node_id") !== "string"
        || typeof Reflect.get(node, "node_type") !== "string"
        || typeof Reflect.get(node, "label") !== "string"
      ) {
        throw new ApiError("Malformed response from /api/v1/graph.", 200);
      }
    }
    for (const edge of edges) {
      requireRecord(edge, "/api/v1/graph");
      requireRecord(Reflect.get(edge, "validity"), "/api/v1/graph");
      requireRecord(Reflect.get(edge, "provenance"), "/api/v1/graph");
      if (
        typeof Reflect.get(edge, "edge_id") !== "string"
        || typeof Reflect.get(edge, "source_id") !== "string"
        || typeof Reflect.get(edge, "target_id") !== "string"
        || typeof Reflect.get(edge, "edge_type") !== "string"
        || typeof Reflect.get(edge, "confidence") !== "number"
        || typeof Reflect.get(edge, "observed_at") !== "string"
      ) {
        throw new ApiError("Malformed response from /api/v1/graph.", 200);
      }
    }
    return value as GraphSnapshotResponse;
  },

  async replayContract(): Promise<ReplayContractResponse> {
    const value: unknown = await requestJson("/api/v1/replay/contract");
    requireRecord(value, "/api/v1/replay/contract");
    requireArray(Reflect.get(value, "supported_modes"), "/api/v1/replay/contract");
    requireArray(
      Reflect.get(value, "side_effects_suppressed"),
      "/api/v1/replay/contract",
    );
    return value as ReplayContractResponse;
  },

  async evaluationContract(): Promise<EvaluationContractResponse> {
    const value: unknown = await requestJson("/api/v1/evaluations/contract");
    requireRecord(value, "/api/v1/evaluations/contract");
    requireArray(Reflect.get(value, "supported_metrics"), "/api/v1/evaluations/contract");
    requireArray(Reflect.get(value, "target_levels"), "/api/v1/evaluations/contract");
    return value as EvaluationContractResponse;
  },

  async evaluations(): Promise<EvaluationListResponse> {
    const value: unknown = await requestJson("/api/v1/evaluations");
    requireRecord(value, "/api/v1/evaluations");
    requireArray(Reflect.get(value, "evaluations"), "/api/v1/evaluations");
    return value as EvaluationListResponse;
  },

  async evaluation(runId: string): Promise<EvaluationRun> {
    const value: unknown = await requestJson(
      `/api/v1/evaluations/${encodeURIComponent(runId)}`,
    );
    requireRecord(value, "/api/v1/evaluations/{evaluation_run_id}");
    requireArray(Reflect.get(value, "metrics"), "/api/v1/evaluations/{evaluation_run_id}");
    requireArray(
      Reflect.get(value, "missed_entities"),
      "/api/v1/evaluations/{evaluation_run_id}",
    );
    return value as EvaluationRun;
  },

  async rankings(
    entityType?: "person" | "company",
    programId?: string,
  ): Promise<RankingIntelligenceResponse> {
    const parameters = new URLSearchParams();
    if (entityType) parameters.set("entity_type", entityType);
    if (programId) {
      parameters.set("program_id", programId);
      parameters.set("limit", "200");
    }
    const suffix = parameters.size ? `?${parameters}` : "";
    const value: unknown = await requestJson(`/api/v1/intelligence/rankings${suffix}`);
    requireRecord(value, "/api/v1/intelligence/rankings");
    requireArray(Reflect.get(value, "items"), "/api/v1/intelligence/rankings");
    return value as RankingIntelligenceResponse;
  },

  async entityIntelligence(entityId: string): Promise<EntityIntelligenceResponse> {
    const value: unknown = await requestJson(
      `/api/v1/intelligence/entities/${encodeURIComponent(entityId)}`,
    );
    requireRecord(value, "/api/v1/intelligence/entities/{entity_id}");
    requireArray(Reflect.get(value, "evidence"), "/api/v1/intelligence/entities/{entity_id}");
    requireArray(Reflect.get(value, "features"), "/api/v1/intelligence/entities/{entity_id}");
    requireArray(Reflect.get(value, "signals"), "/api/v1/intelligence/entities/{entity_id}");
    requireArray(Reflect.get(value, "lineage"), "/api/v1/intelligence/entities/{entity_id}");
    return value as EntityIntelligenceResponse;
  },

  async decisions(programId?: string): Promise<DecisionBoardResponse> {
    const parameters = new URLSearchParams();
    if (programId) parameters.set("program_id", programId);
    const suffix = parameters.size ? `?${parameters}` : "";
    const value: unknown = await requestJson(`/api/v1/decisions${suffix}`);
    requireRecord(value, "/api/v1/decisions");
    const stages = Reflect.get(value, "stages");
    requireRecord(stages, "/api/v1/decisions");
    for (const items of Object.values(stages)) {
      requireArray(items, "/api/v1/decisions");
    }
    return value as DecisionBoardResponse;
  },

  async decision(entityId: string): Promise<DecisionEntityResponse> {
    const value: unknown = await requestJson(
      `/api/v1/decisions/entities/${encodeURIComponent(entityId)}`,
    );
    requireRecord(value, "/api/v1/decisions/entities/{entity_id}");
    requireArray(Reflect.get(value, "actions"), "/api/v1/decisions/entities/{entity_id}");
    requireArray(
      Reflect.get(value, "feedback_features"),
      "/api/v1/decisions/entities/{entity_id}",
    );
    requireArray(
      Reflect.get(value, "recommendations"),
      "/api/v1/decisions/entities/{entity_id}",
    );
    requireArray(Reflect.get(value, "alerts"), "/api/v1/decisions/entities/{entity_id}");
    return value as DecisionEntityResponse;
  },

  async recordCrmAction(entityId: string, body: CRMActionRequest): Promise<CRMActionResult> {
    const value: unknown = await requestJson(
      `/api/v1/decisions/entities/${encodeURIComponent(entityId)}/actions`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    requireRecord(value, "/api/v1/decisions/entities/{entity_id}/actions");
    requireRecord(Reflect.get(value, "action"), "/api/v1/decisions/entities/{entity_id}/actions");
    requireRecord(Reflect.get(value, "decision"), "/api/v1/decisions/entities/{entity_id}/actions");
    return value as CRMActionResult;
  },

  async updateRecommendation(
    recommendationId: string,
    body: RecommendationLifecycleRequest,
  ): Promise<RecommendationLifecycleEvent> {
    const value: unknown = await requestJson(
      `/api/v1/decisions/recommendations/${encodeURIComponent(recommendationId)}/lifecycle`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    requireRecord(value, "/api/v1/decisions/recommendations/{recommendation_id}/lifecycle");
    return value as RecommendationLifecycleEvent;
  },

  async updateAlert(
    alertId: string,
    body: AlertLifecycleRequest,
  ): Promise<AlertLifecycleEvent> {
    const value: unknown = await requestJson(
      `/api/v1/decisions/alerts/${encodeURIComponent(alertId)}/lifecycle`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    requireRecord(value, "/api/v1/decisions/alerts/{alert_id}/lifecycle");
    return value as AlertLifecycleEvent;
  },
};

// Stable function exports keep mutation boundaries independent of object-method
// binding and make them straightforward to exercise in isolation.
export const recordCrmAction = apiClient.recordCrmAction;
export const updateRecommendationLifecycle = apiClient.updateRecommendation;
export const updateAlertLifecycle = apiClient.updateAlert;
