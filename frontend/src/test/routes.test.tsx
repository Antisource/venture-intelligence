import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { installApiMock, renderApp } from "./testUtils";

const nativeRoutes = [
  ["/research", "Research"],
  ["/discovery", "Inbox"],
  ["/changes", "Inbox"],
  ["/alerts", "Inbox"],
  ["/analyst", "Research"],
  ["/settings", "Notifications with explicit boundaries."],
] as const;

describe("native product routes", () => {
  it("includes unscoped opportunities in All programs and opens their Research evidence", async () => {
    const fetchMock = installApiMock();
    const original = fetchMock.getMockImplementation()!;
    const sample = await (await original("/api/v1/batch-discovery/sample")).json();
    const candidate = { ...sample.candidate, hypothesis_id: "unscoped-lead", program_id: "generic_venture", batch_id: "unscoped", candidate_name: null, candidate_kind: "company", company_name: "Unscoped Labs", display_name: "Unscoped Labs", canonical_company_id: null, canonical_person_id: null };
    const observation = { ...sample.observations[0], program_id: "generic_venture", batch_id: "unscoped", claim: "Unscoped Labs announced a public launch.", stance: "neutral" };
    fetchMock.mockImplementation(async (input, init) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/batch-discovery" && !url.searchParams.has("program_id")) {
        return new Response(JSON.stringify({ program_id: "generic_venture", batch_id: "unscoped", candidates: [candidate] }));
      }
      if (url.pathname === "/api/v1/batch-discovery/unscoped-lead") {
        return new Response(JSON.stringify({ candidate, observations: [observation] }));
      }
      return original(input, init);
    });
    renderApp("/discover");
    expect(await screen.findByRole("link", { name: "Unscoped Labs" })).toBeVisible();
    const program = screen.getByRole("combobox", { name: "Monitoring program" });
    await userEvent.selectOptions(program, "yc_current");
    expect(screen.queryByRole("link", { name: "Unscoped Labs" })).not.toBeInTheDocument();
    await userEvent.selectOptions(program, "");
    await userEvent.click(await screen.findByRole("link", { name: "Unscoped Labs" }));
    expect(await screen.findByRole("heading", { name: "Unscoped Labs" })).toBeVisible();
    expect(screen.getByText("Insufficient evidence — candidate not promoted")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "evidence" }));
    expect(await screen.findByText("Unscoped Labs announced a public launch.")).toBeVisible();
  });

  it.each(nativeRoutes)("loads %s without the legacy adapter", async (route, heading) => {
    installApiMock();
    renderApp(route);
    expect(await screen.findByRole("heading", { name: heading })).toBeVisible();
    expect(document.querySelector(".legacy-route")).not.toBeInTheDocument();
    expect(screen.queryByText("Something went wrong")).not.toBeInTheDocument();
  });

  it("redirects the retired dashboard route to the attention overview", async () => {
    installApiMock();
    renderApp("/dashboard");
    expect(await screen.findByRole("heading", { name: "Inbox" })).toBeVisible();
  });

  it("runs bounded research from the native side-by-side workspace", async () => {
    const fetchMock = installApiMock();
    renderApp("/research");
    await userEvent.type(await screen.findByRole("textbox", { name: "Research target" }), "Acme Labs");
    await userEvent.click(screen.getByRole("button", { name: "Investigate target" }));
    expect(await screen.findByText("No evidence collected")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/research", expect.objectContaining({ method: "POST" }));
  });

  it("shows evidence-backed unknown candidates and runs discovery without a seed identity", async () => {
    const fetchMock = installApiMock();
    renderApp("/discovery");

    expect(await screen.findByRole("heading", { name: "Inbox" })).toBeVisible();
    await userEvent.click(await screen.findByRole("link", { name: /Alice Chen/ }));
    expect(await screen.findByRole("heading", { name: "Alice Chen" })).toBeVisible();
    expect(screen.getByText("Insufficient evidence — candidate not promoted")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "evidence" }));
    expect(await screen.findByText("Alice Chen says Acme Labs joined Y Combinator F26.")).toBeVisible();
    await userEvent.click(screen.getByRole("link", { name: "Discover" }));
    const refresh = await screen.findByRole("button", { name: "Refresh intelligence" });
    expect(refresh).toBeEnabled();
    await userEvent.click(refresh);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/v1/cycles/run", expect.objectContaining({ method: "POST" })));
    expect(await screen.findByRole("status")).toHaveTextContent("Intelligence updated.");
  });

  it("saves and tests notification settings through the native workspace", async () => {
    const fetchMock = installApiMock();
    renderApp("/settings");

    const destination = await screen.findByRole("textbox", { name: "Destination email" });
    expect(destination).toHaveValue("");
    await userEvent.clear(destination);
    await userEvent.type(destination, "research@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/settings/email",
      expect.objectContaining({ method: "PUT" }),
    ));

    await userEvent.click(screen.getByRole("button", { name: "Send test email" }));
    expect(await screen.findByText("Test email: sent.")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/settings/email/test",
      expect.objectContaining({ method: "POST" }),
    );
  });
});

describe("native milestone routes", () => {
  it("shows current F26 company and founder results in their respective views", async () => {
    const fetchMock = installApiMock();
    const original = fetchMock.getMockImplementation()!;
    const sample = await (await original("/api/v1/batch-discovery/sample")).json();
    const founder = {
      ...sample.candidate,
      hypothesis_id: "f26-founder",
      program_id: "yc_current",
      batch_id: "F26",
      candidate_name: "Jules Walter",
      company_name: "Forward",
      display_name: "Jules Walter",
    };
    const company = {
      ...founder,
      hypothesis_id: "f26-company",
      candidate_kind: "company",
      display_name: "Forward",
    };
    fetchMock.mockImplementation(async (input, init) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/batch-discovery") {
        return new Response(JSON.stringify({
          program_id: "yc_current",
          batch_id: "F26",
          candidates: [company, founder],
        }));
      }
      return original(input, init);
    });

    renderApp("/discover?type=companies");
    expect(await screen.findByRole("link", { name: "Forward" })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "founders" }));
    expect(await screen.findByRole("link", { name: "Jules Walter" })).toBeVisible();
  });

  it("loads the typed CRM decision board", async () => {
    installApiMock();
    renderApp("/crm");

    expect(await screen.findByRole("heading", {
      name: "Portfolio",
    })).toBeVisible();
    expect(await screen.findByRole("link", { name: /Acme Labs/ })).toBeVisible();
  });
  it("loads the native bounded temporal graph workspace", async () => {
    installApiMock();
    renderApp("/graph");

    expect(await screen.findByRole("heading", {
      name: "Network / relationships",
    })).toBeVisible();
    expect(await screen.findByLabelText("Relationship table")).toBeVisible();
    expect(screen.queryByText("Graph workspace moved")).not.toBeInTheDocument();
  });
  it("loads versioned rankings and entity evidence lineage", async () => {
    installApiMock();
    renderApp("/companies");

    expect(await screen.findByRole("heading", {
      name: "Discover",
    })).toBeVisible();
    expect((await screen.findAllByRole("link", { name: "Acme Labs" }))[0]).toBeVisible();
  });

  it("loads the typed evaluation workspace with an empty report state", async () => {
    installApiMock();
    renderApp("/evaluate");

    expect(await screen.findByRole("heading", {
      name: "Understand what the system knew—and what it missed.",
    })).toBeVisible();
    expect(await screen.findByText("Nothing to show yet")).toBeVisible();
    expect(screen.getByText("Local only")).toBeVisible();
  });

  it("shows truthful gated live source quality without enabling direct social access", async () => {
    installApiMock();
    renderApp("/sources");

    expect(await screen.findByRole("heading", { name: "Operational source quality" })).toBeVisible();
    expect(await screen.findByText("public-program-osint:discovery")).toBeVisible();
    expect(screen.getByText("Indexed: linkedin.com")).toBeVisible();
    expect(
      screen.getByText(/Direct LinkedIn\/X\/Instagram\/Threads crawling remains disabled/),
    ).toBeVisible();
    expect(screen.getByText("Canonical writes").closest("div")).toHaveTextContent("0");
    expect(screen.getByRole("heading", { name: "Andreessen Horowitz" })).toBeVisible();
    expect(screen.getByText("Also known as a16z")).toBeVisible();
  });

  it("renders persisted metrics and diagnostics from the typed detail contract", async () => {
    installApiMock([], {
      evaluation_run_id: "eval-1",
      status: "completed",
      program_id: "yc_current",
      batch_id: "S26",
      target_level: "company",
      cutoff: "2026-01-15T12:00:00Z",
      outcome_timestamp: "2026-02-01T12:00:00Z",
      created_at: "2026-02-02T12:00:00Z",
      report_digest: "report-digest",
      ground_truth_digest: "truth-digest",
      metrics: [{
        name: "precision_at_10",
        value: 0.7,
        numerator: 7,
        denominator: 10,
        sample_size: 10,
        unit: "ratio",
        definition: "Positive identities in the first 10 rankings / 10.",
      }],
      warnings: [],
      missed_entities: [{
        canonical_name: "Missing Labs",
        entity_type: "company",
        category: "not_discovered",
        evidence_count: 0,
        signal_count: 0,
      }],
      false_positives: [{
        rank: 2,
        entity_id: "company-beta",
        canonical_name: "Beta Systems",
        entity_type: "company",
        composite_score: 0.72,
        confidence: 0.8,
        signals: ["hiring_velocity"],
        evidence: ["Hiring page"],
      }],
      snapshot: { replay_projection_digest: "replay-digest" },
    });
    renderApp("/evaluate");

    expect(await screen.findByText("70.0%")).toBeVisible();
    expect(screen.getByText("Missing Labs")).toBeVisible();
    expect(screen.getByText("Beta Systems")).toBeVisible();
    expect(screen.getByText("report-digest")).toBeVisible();
  });
});
