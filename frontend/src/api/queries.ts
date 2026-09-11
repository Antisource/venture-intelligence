import { queryOptions } from "@tanstack/react-query";

import { apiClient, type GraphQueryParameters } from "./client.ts";

export const healthQuery = () =>
  queryOptions({ queryKey: ["health"], queryFn: apiClient.health, staleTime: 30_000 });

export const programsQuery = () =>
  queryOptions({
    queryKey: ["programs"],
    queryFn: apiClient.programs,
    staleTime: 5 * 60_000,
  });

export const sourcePoliciesQuery = () =>
  queryOptions({
    queryKey: ["source-policies"],
    queryFn: apiClient.sourcePolicies,
    staleTime: 60_000,
  });

export const sourceQualityQuery = () =>
  queryOptions({
    queryKey: ["source-quality"],
    queryFn: apiClient.sourceQuality,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

export const sourceQualityContractQuery = () =>
  queryOptions({
    queryKey: ["source-quality-contract"],
    queryFn: apiClient.sourceQualityContract,
    staleTime: 5 * 60_000,
  });

export const readinessQuery = () =>
  queryOptions({
    queryKey: ["system-readiness"],
    queryFn: apiClient.readiness,
    staleTime: 15_000,
    refetchInterval: 30_000,
  });

export const cyclesQuery = () =>
  queryOptions({
    queryKey: ["intelligence-cycles"],
    queryFn: apiClient.cycles,
    staleTime: 10_000,
    refetchInterval: 20_000,
  });

export const batchDiscoveryContractQuery = () =>
  queryOptions({
    queryKey: ["batch-discovery-contract"],
    queryFn: apiClient.batchDiscoveryContract,
    staleTime: 5 * 60_000,
  });

export const batchDiscoveryQuery = (programId: string | null, includeRejected = true) =>
  queryOptions({
    queryKey: ["batch-discovery", programId, includeRejected],
    queryFn: () => apiClient.batchDiscovery(programId, includeRejected),
    enabled: programId !== "",
    staleTime: 15_000,
    refetchInterval: 30_000,
  });

export const batchCandidateQuery = (hypothesisId: string) =>
  queryOptions({
    queryKey: ["batch-candidate", hypothesisId],
    queryFn: () => apiClient.batchCandidate(hypothesisId),
    enabled: Boolean(hypothesisId),
    staleTime: 30_000,
  });

export const alertsQuery = () =>
  queryOptions({
    queryKey: ["alerts"],
    queryFn: apiClient.alerts,
    staleTime: 15_000,
  });

export const emailSettingsQuery = () =>
  queryOptions({
    queryKey: ["email-settings"],
    queryFn: apiClient.emailSettings,
    staleTime: 30_000,
  });

export const temporalContractQuery = () =>
  queryOptions({
    queryKey: ["temporal-contract"],
    queryFn: apiClient.temporalContract,
    staleTime: 5 * 60_000,
  });

export const graphQuery = (parameters: GraphQueryParameters) =>
  queryOptions({
    queryKey: [
      "temporal-graph",
      parameters.asOf ?? "live",
      parameters.programId ?? "all-programs",
      parameters.nodeTypes?.join(",") ?? "all-types",
      parameters.minConfidence ?? 0,
      parameters.focusId ?? "all-nodes",
      parameters.depth ?? 1,
      parameters.maxNodes ?? 200,
      parameters.maxEdges ?? 400,
    ],
    queryFn: () => apiClient.graph(parameters),
    staleTime: parameters.asOf ? 5 * 60_000 : 15_000,
    refetchInterval: parameters.asOf ? false : 15_000,
  });

export const replayContractQuery = () =>
  queryOptions({
    queryKey: ["replay-contract"],
    queryFn: apiClient.replayContract,
    staleTime: 5 * 60_000,
  });

export const evaluationContractQuery = () =>
  queryOptions({
    queryKey: ["evaluation-contract"],
    queryFn: apiClient.evaluationContract,
    staleTime: 5 * 60_000,
  });

export const evaluationsQuery = () =>
  queryOptions({
    queryKey: ["evaluations"],
    queryFn: apiClient.evaluations,
    staleTime: 30_000,
  });

export const evaluationQuery = (runId: string) =>
  queryOptions({
    queryKey: ["evaluation", runId],
    queryFn: () => apiClient.evaluation(runId),
    enabled: Boolean(runId),
    staleTime: 5 * 60_000,
  });

export const searchQuery = (query: string) =>
  queryOptions({
    queryKey: ["global-search", query],
    queryFn: () => apiClient.search(query),
    enabled: query.trim().length >= 2,
    staleTime: 20_000,
  });

export const rankingsQuery = (entityType?: "person" | "company", programId?: string) =>
  queryOptions({
    queryKey: ["ranking-intelligence", entityType ?? "all", programId ?? "all-programs"],
    queryFn: () => apiClient.rankings(entityType, programId),
    staleTime: 30_000,
  });

export const entityIntelligenceQuery = (entityId: string) =>
  queryOptions({
    queryKey: ["entity-intelligence", entityId],
    queryFn: () => apiClient.entityIntelligence(entityId),
    enabled: Boolean(entityId),
    staleTime: 30_000,
  });

export const decisionsQuery = (programId?: string) =>
  queryOptions({
    queryKey: ["decisions", programId ?? "all-programs"],
    queryFn: () => apiClient.decisions(programId),
    staleTime: 15_000,
  });

export const decisionQuery = (entityId: string) =>
  queryOptions({
    queryKey: ["decision", entityId],
    queryFn: () => apiClient.decision(entityId),
    enabled: Boolean(entityId),
    staleTime: 15_000,
  });
