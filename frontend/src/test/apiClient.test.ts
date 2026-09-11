import { describe, expect, it, vi } from "vitest";

import { ApiError, apiClient } from "../api/client.ts";

describe("typed API boundary", () => {
  it("converts network failures into a stable API error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    await expect(apiClient.health()).rejects.toMatchObject({
      name: "ApiError",
      status: null,
    });
  });

  it("rejects malformed endpoint shapes", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("[]", {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    await expect(apiClient.health()).rejects.toBeInstanceOf(ApiError);
  });

  it("rejects malformed JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("not-json", {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    await expect(apiClient.sourcePolicies()).rejects.toThrow("malformed JSON");
  });

  it("rejects malformed replay contract collections", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ supported_modes: "observed_at" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    await expect(apiClient.replayContract()).rejects.toBeInstanceOf(ApiError);
  });

  it("rejects malformed evaluation collections", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ evaluations: {} }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    await expect(apiClient.evaluations()).rejects.toBeInstanceOf(ApiError);
  });

  it("rejects malformed intelligence lineage collections", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ evidence: [], features: {}, signals: [], lineage: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    await expect(apiClient.entityIntelligence("company-acme")).rejects.toBeInstanceOf(
      ApiError,
    );
  });

  it("fails closed when a legacy research response lacks identity attribution", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({
          target: { name: "Alex Kim", entity_type: "person" },
          evidence: [{ url: "https://example.com/alex-kim", title: "Alex Kim" }],
          sources: [],
          confidence_label: "low",
          confidence: 0.4,
          stop_reason: "completed",
        }), { status: 200, headers: { "Content-Type": "application/json" } }),
      ),
    );

    const result = await apiClient.research({
      entity_name: "Alex Kim",
      entity_type: "person",
      program_id: null,
      budget: {
        max_queries: 1,
        max_depth: 0,
        max_results_per_query: 1,
        max_runtime_seconds: 1,
      },
    });
    expect(result.identity_status).toBe("ambiguous");
    expect(result.program_status).toBe("unknown");
    expect(result.independent_evidence_origins).toBe(0);
    expect(result.evidence[0]).toMatchObject({
      attribution_status: "unbound",
      acquisition_providers: [],
    });
  });
});
