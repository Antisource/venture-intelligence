import { act, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { KnowledgeEvidence } from "../features/research/KnowledgeEvidence";
import { renderWithClient } from "./testUtils";

describe("canonical knowledge in Research", () => {
  it("refreshes category confidence on a revision and falls back without reconnect loops", async () => {
    let confidence = 71;
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      entity_id: "acme", entity_type: "company", overall_confidence_pct: confidence,
      claims: [{ claim_id: "claim", statement: "Independent corroboration", status: "supported", confidence: confidence / 100 }],
      evidence_by_category: { first_party_identity: [{ claim: "Official identity", confidence_pct: confidence, canonical_url: "https://acme.example/about", temporal_status: "current", provenance: { observation_id: "observation" } }] },
      category_confidence: { first_party_identity: { confidence_pct: confidence } },
    }), { headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const listeners = new Map<string, () => void>();
    const close = vi.fn();
    let error: (() => void) | undefined;
    class Stream {
      onopen?: () => void;
      set onerror(callback: () => void) { error = callback; }
      addEventListener(name: string, callback: () => void) { listeners.set(name, callback); }
      close = close;
    }
    vi.stubGlobal("EventSource", Stream);
    const rendered = renderWithClient(<KnowledgeEvidence entityId="acme" entityType="company" />);
    expect(await screen.findByText(/71% overall confidence/)).toBeVisible();
    confidence = 89;
    act(() => listeners.get("knowledge.updated")?.());
    expect(await screen.findByText(/89% overall confidence/)).toBeVisible();
    act(() => error?.());
    expect(screen.getByRole("status")).toHaveTextContent("Polling every 15 seconds");
    expect(close).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledWith("/api/knowledge/company/acme", expect.anything());
    rendered.unmount();
    expect(close).toHaveBeenCalledTimes(2);
  });

  it("does not manufacture a wiki for a missing canonical page", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 404 })));
    renderWithClient(<KnowledgeEvidence entityId="missing" entityType="company" />);
    await waitFor(() => expect(screen.getByText("No compiled knowledge page yet")).toBeVisible());
  });

  it("rejects malformed categorized evidence instead of crashing the workspace", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({claims: [], evidence_by_category: {first_party_identity: {claim: "not an array"}}}))));
    renderWithClient(<KnowledgeEvidence entityId="malformed" entityType="company" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Malformed response");
  });
});
