import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { installApiMock, renderApp } from "./testUtils";

describe("M4 intelligence views", () => {
  it("resets the entity filter when navigating between companies and founders", async () => {
    installApiMock();
    renderApp("/companies");

    expect(await screen.findByRole("heading", {
      name: "Discover",
    })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "founders" }));

    expect(await screen.findByRole("heading", {
      name: "Discover",
    })).toBeVisible();
  });

  it("renders ranking movement and navigates to company lineage", async () => {
    installApiMock();
    const { router } = renderApp("/discover");

    expect(await screen.findByText("+2 places")).toBeVisible();
    await userEvent.click(screen.getAllByRole("link", { name: "Acme Labs" })[0]!);

    expect(router.state.location.pathname).toBe("/research/company-acme");
    expect(await screen.findByRole("heading", { name: "Acme Labs" })).toBeVisible();
    expect(screen.getByText("Evidence → feature → signal → ranking")).toBeVisible();
    expect(screen.getByText("Has active public repository")).toBeVisible();
    expect(screen.getByText("github stars = 144")).toBeVisible();
    expect(screen.getByRole("link", { name: "Open source ↗" })).toHaveAttribute(
      "href",
      "https://github.com/acme/core",
    );
    expect(screen.getByText(/Identity verified · Program verified · 2 independent evidence origins/)).toBeVisible();
    expect(screen.queryByText(/Validated identity · 96%/)).not.toBeInTheDocument();
  });

  it("separates attached evidence from same-name leads", async () => {
    const user = userEvent.setup();
    const fetchMock = installApiMock();
    renderApp("/research/company-acme?view=evidence");

    expect(await screen.findByRole("heading", { name: "Canonical / attached evidence" })).toBeVisible();
    expect(screen.getByText("Has active public repository")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Ambiguous / unbound leads" })).toBeVisible();
    expect(screen.getByRole("heading", { name: /different person with the same name/ })).toBeVisible();
    expect(screen.getAllByText(/Name-only match without company/).length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "assistant" }));
    expect(screen.getByText(/Scoped to/)).toBeVisible();
    const composer = screen.getByPlaceholderText(/Ask about Acme Labs/);
    await user.type(composer, "Compare every company");
    await user.click(screen.getByRole("button", { name: "Ask analyst" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([path]) => String(path) === "/api/analyst/chat");
      expect(call).toBeDefined();
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({
        question: "Compare every company",
        context_name: "Acme Labs",
      });
    });
  });

  it("filters the ranking contract by entity type", async () => {
    const fetchMock = installApiMock();
    renderApp("/discover");
    await userEvent.click(await screen.findByRole("button", { name: "founders" }));
    expect(screen.queryByText("+2 places")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/intelligence/rankings", expect.anything());
  });

  it("applies the registry-selected non-YC program to ranking requests", async () => {
    const fetchMock = installApiMock();
    renderApp("/discover");
    const program = await screen.findByRole("combobox", { name: "Monitoring program" });
    await screen.findByRole("option", { name: "a16z Speedrun · SR007" });

    await userEvent.selectOptions(program, "speedrun_current");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/intelligence/rankings?program_id=speedrun_current&limit=200",
      expect.anything(),
    );
  });

  it("renders each canonical company once even if a malformed response repeats it", async () => {
    const duplicate = {
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
        founder_ids: ["founder-one", "founder-two"],
      },
      ranking: {
        ranking_id: "ranking-acme",
        ranking_version: "m4-ranking-1",
        entity_id: "company-acme",
        rank: 1,
        composite_score: 0.81,
        dimensions: [],
        hypothesis_ids: [],
        evidence_ids: ["evidence-one", "evidence-two"],
        signal_ids: [],
        feature_ids: [],
        input_digest: "ranking-digest",
        reasoning: "Multiple founders and evidence records.",
        confidence: 0.91,
        ranked_at: "2026-02-01T12:00:00Z",
      },
      movement: { current_rank: 1, previous_rank: 1, rank_delta: 0, score_delta: 0 },
    };
    installApiMock([], undefined, { rankingItems: [duplicate, { ...duplicate, ranking: { ...duplicate.ranking, ranking_id: "ranking-acme-tie" } }] });
    renderApp("/discover");

    expect(await screen.findAllByRole("link", { name: "Acme Labs" })).toHaveLength(1);
  });
});
