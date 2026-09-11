import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { installApiMock, renderApp } from "./testUtils";

describe("application shell", () => {
  it("renders responsive navigation and routes foundational pages", async () => {
    installApiMock();
    renderApp();

    expect(await screen.findByRole("heading", { name: "Inbox" })).toBeVisible();
    expect(screen.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Research" })).toBeVisible();
    await userEvent.click(screen.getByText("System"));
    expect(screen.getByRole("link", { name: "Replay" })).toBeVisible();

    await userEvent.click(screen.getByRole("link", { name: "Sources" }));
    expect(await screen.findByRole("heading", { name: "Public-source boundaries" })).toBeVisible();
  });

  it("renders the typed replay contract without a remote execution control", async () => {
    installApiMock();
    renderApp("/replay");

    expect(
      await screen.findByRole("heading", {
        name: "Reconstruct what the system knew then.",
      }),
    ).toBeVisible();
    expect(await screen.findByText("observed_at")).toBeVisible();
    expect(screen.getByText("Disabled")).toBeVisible();
    expect(screen.queryByRole("button", { name: /run replay/i })).not.toBeInTheDocument();
  });

  it("persists an explicit light or dark theme preference", async () => {
    installApiMock();
    renderApp();
    const selector = await screen.findByRole("combobox", { name: "Theme preference" });

    await userEvent.selectOptions(selector, "light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(window.localStorage.getItem("venture:theme")).toBe("light");

    await userEvent.selectOptions(selector, "dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("venture:theme")).toBe("dark");
  });

  it("opens with Cmd/Ctrl+K and searches validated backend entities", async () => {
    installApiMock([
      {
        id: "person__ada",
        kind: "person",
        label: "Ada Lovelace",
        context: "Acme Labs",
        confidence: 0.94,
        route: "/founders?entity=person__ada",
      },
    ]);
    renderApp();
    await screen.findByRole("heading", { name: "Inbox" });

    await userEvent.keyboard("{Control>}k{/Control}");
    const input = screen.getByRole("textbox", {
      name: "Search commands and validated entities",
    });
    await userEvent.type(input, "Ada");

    expect(await screen.findByRole("button", { name: /Ada Lovelace/ })).toBeVisible();
    expect(screen.getByText(/person · Acme Labs/)).toBeVisible();
  });
});
