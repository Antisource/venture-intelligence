import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { installApiMock, renderApp } from "./testUtils";

describe("M5 decision workflow", () => {
  it("renders integrity-gated stages and opens the native decision detail", async () => {
    installApiMock();
    const { router } = renderApp("/crm");

    await userEvent.click(await screen.findByRole("link", { name: /Acme Labs/ }));

    expect(router.state.location.pathname).toBe("/research/company-acme");
    expect(await screen.findByRole("heading", { name: "Acme Labs" })).toBeVisible();
    await userEvent.click(screen.getByText("Decision score lineage"));
    expect(screen.getByText("human feedback positive count")).toBeVisible();
    expect(screen.getByText("not ground truth")).toBeVisible();
    expect(screen.getByText("Schedule technical diligence.")).toBeVisible();
  });

  it("records typed partner actions and refreshes decision projections", async () => {
    const fetchMock = installApiMock();
    renderApp("/crm/company-acme");
    const user = userEvent.setup();

    await user.type(await screen.findByRole("textbox", { name: "Actor" }), "Review Partner");
    await user.selectOptions(screen.getByRole("combobox", { name: "Action" }), "watchlisted");
    await user.type(screen.getByRole("textbox", { name: "Note" }), "Track verified milestones.");
    await user.click(screen.getByRole("button", { name: "Save decision" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/decisions/entities/company-acme/actions",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("distinguishes factual false positives from investment passes", async () => {
    const fetchMock = installApiMock();
    renderApp("/crm/company-acme");
    const user = userEvent.setup();

    await user.type(await screen.findByRole("textbox", { name: "Actor" }), "Review Partner");
    await user.selectOptions(screen.getByRole("combobox", { name: "Action" }), "passed");
    await user.selectOptions(screen.getByRole("combobox", { name: "Pass reason" }), "wrong_identity");
    await user.click(screen.getByRole("button", { name: "Save decision" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([path]) => (
        path === "/api/v1/decisions/entities/company-acme/actions"
      ));
      const body = JSON.parse(String(call?.[1]?.body));
      expect(body.provenance).toMatchObject({
        review_reason: "wrong_identity",
        factual_false_positive: true,
        authoritative_ground_truth: false,
      });
    });
  });

  it("records recommendation and alert lifecycle decisions", async () => {
    const fetchMock = installApiMock();
    renderApp("/crm/company-acme");

    await userEvent.click(await screen.findByRole("button", { name: "accepted" }));
    await userEvent.click(screen.getByRole("button", { name: "acknowledged" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/decisions/recommendations/recommendation-acme/lifecycle",
        expect.objectContaining({ method: "POST" }),
      );
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/decisions/alerts/alert-acme/lifecycle",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("shows one current recommendation and keeps superseded versions in audit history", async () => {
    installApiMock();
    renderApp("/crm/company-acme");

    expect(await screen.findByText("Validated public evidence warrants partner contact.")).toBeVisible();
    expect(screen.getAllByRole("button", { name: "accepted" })).toHaveLength(1);
    expect(screen.queryByText("Earlier evidence did not clear the review threshold.")).not.toBeVisible();

    await userEvent.click(screen.getByText("Previous versions (1)"));
    expect(screen.getByText("Earlier evidence did not clear the review threshold.")).toBeVisible();
  });
});
