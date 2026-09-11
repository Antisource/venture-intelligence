import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { GraphSnapshotResponse } from "../api/client";
import { graphPositions, neighborIds, visibleGraph } from "../features/graph/graphModel";
import { installApiMock, renderApp } from "./testUtils";

describe("temporal graph interactions", () => {
  it("searches, focuses, collapses neighbors, and inspects node provenance", async () => {
    const user = userEvent.setup();
    installApiMock();
    renderApp("/graph");

    const search = await screen.findByLabelText("Search");
    await user.type(search, "Acme");
    await user.click(screen.getByRole("button", { name: "Focus" }));

    expect(await screen.findByRole("heading", { name: "Acme Labs" })).toBeVisible();
    expect(screen.getByRole("link", { name: "https://acme.example/about" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Collapse neighbors" }));
    expect(screen.getByRole("button", { name: "Expand neighbors" })).toBeVisible();
  });

  it("opens edge validity/provenance and applies an isolated as-of request", async () => {
    const user = userEvent.setup();
    const fetchMock = installApiMock();
    renderApp("/graph");

    await user.click(await screen.findByRole("button", { name: "Acme Labs" }));
    expect(await screen.findByRole("heading", { name: "Acme Labs" })).toBeVisible();
    await user.click(await screen.findByRole("button", { name: "founded" }));
    expect(await screen.findByRole("heading", { name: "founded" })).toBeVisible();
    await user.click(screen.getByText("Technical details"));
    expect(screen.getByText(/day precision/)).toBeVisible();
    expect(screen.getByRole("link", { name: "https://acme.example/team" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Open source" })).toHaveAttribute(
      "href",
      "https://acme.example/team",
    );

    const input = screen.getByLabelText("As of");
    await user.type(input, "2026-01-10T12:00");
    await user.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([path]) => String(path).includes("as_of="))).toBe(true);
    });
    expect(await screen.findByText("Isolated replay")).toBeVisible();
  });

  it("rejects a malformed graph response at the API boundary", async () => {
    const fetchMock = installApiMock();
    const fallback = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input).startsWith("/api/v1/graph")) {
        return new Response(JSON.stringify({ as_of: "2026-02-01T12:00:00Z", nodes: [{}], edges: [], limits: { max_nodes: 200, max_edges: 400 } }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (!fallback) throw new Error("API mock is unavailable");
      return fallback(input);
    });
    renderApp("/graph");

    expect(await screen.findByText("Malformed response from /api/v1/graph.")).toBeVisible();
  });

  it("uses the accessible sparse state for one verified node with no relationships", async () => {
    const fetchMock = installApiMock();
    const fallback = fetchMock.getMockImplementation();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).startsWith("/api/v1/graph")) {
        return new Response(JSON.stringify({
          schema_version: "7.0",
          as_of: "2026-02-01T12:00:00Z",
          historical_cutoff_field: "observed_at",
          projection_source: "live",
          program_id: null,
          focus_id: "company-acme",
          depth: 1,
          nodes: [{
            node_id: "company-acme",
            node_type: "Company",
            label: "Acme Labs",
            confidence: 0.96,
            properties: {},
            provenance: { evidence_ids: ["event-acme"], source_urls: ["https://acme.example"] },
          }],
          edges: [],
          truncated: false,
          limits: { max_nodes: 220, max_edges: 440, max_depth: 2, max_replay_events: 5000 },
          available_node_types: ["Company"],
          available_edge_types: [],
          semantics: "Evidence-backed canonical relationships only.",
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (!fallback) throw new Error("API mock is unavailable");
      return fallback(input, init);
    });
    renderApp("/graph?focus=company-acme");

    expect(await screen.findByText("No verified relationships yet.")).toBeVisible();
    expect(screen.queryByText("3D relationship workspace")).not.toBeInTheDocument();
  });
});

describe("bounded graph view model", () => {
  const snapshot = {
    nodes: [
      { node_id: "a", node_type: "Company", label: "Alpha", properties: {} },
      { node_id: "b", node_type: "Person", label: "Beta Founder", properties: {} },
      { node_id: "c", node_type: "Repository", label: "Gamma Core", properties: {} },
    ],
    edges: [
      { edge_id: "ab", source_id: "a", target_id: "b", edge_type: "FOUNDED", temporal_status: "current" },
      { edge_id: "bc", source_id: "b", target_id: "c", edge_type: "CREATED", temporal_status: "historical" },
    ],
  } as GraphSnapshotResponse;

  it("uses deterministic positions and preserves one-hop search context", () => {
    expect(graphPositions(snapshot.nodes)).toEqual(graphPositions(snapshot.nodes));
    expect(neighborIds("b", snapshot.edges)).toEqual(new Set(["a", "c"]));
    const graph = visibleGraph(snapshot, {
      search: "Alpha",
      nodeType: "",
      edgeType: "",
      temporalStatus: "",
      hiddenNodeIds: new Set(),
    });
    expect(graph.nodes.map((node) => node.node_id)).toEqual(["a", "b"]);
    expect(graph.edges.map((edge) => edge.edge_id)).toEqual(["ab"]);
  });

  it("uses canonical edges as forces, is order independent, and handles empty/single graphs", () => {
    const linked = graphPositions(snapshot.nodes, snapshot.edges);
    const unlinked = graphPositions(snapshot.nodes, []);
    const distance = (positions: ReturnType<typeof graphPositions>, a: string, b: string) =>
      Math.hypot(...positions.get(a)!.map((v, i) => v - positions.get(b)![i]!));
    expect(distance(linked, "a", "b")).toBeLessThan(distance(unlinked, "a", "b"));
    expect(linked).toEqual(graphPositions([...snapshot.nodes].reverse(), [...snapshot.edges].reverse()));
    expect(graphPositions([]).size).toBe(0);
    expect(graphPositions(snapshot.nodes.slice(0, 1)).get("a")).toEqual([0, 0, 0]);
    for (const point of linked.values()) expect(point.every(Number.isFinite)).toBe(true);
  });
});
