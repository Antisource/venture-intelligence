import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { installApiMock, renderApp } from "./testUtils";

describe("configuration truthfulness", () => {
  it("shows Product Hunt configuration separately from availability", async () => {
    installApiMock();
    renderApp("/system/sources");

    const card = (await screen.findByRole("heading", {
      name: "Product Hunt official GraphQL connector",
    })).closest("article");
    expect(card).toHaveTextContent("Configured");
    expect(card).toHaveTextContent("healthy");
  });

  it("shows SMTP transport, destination, and effective sending separately", async () => {
    installApiMock();
    renderApp("/system/settings");

    expect(await screen.findByText("SMTP transport")).toBeVisible();
    expect(screen.getByText("Destination", { selector: "dt" })).toBeVisible();
    expect(screen.getByText("Sending", { selector: "dt" })).toBeVisible();
    expect(screen.getByText("CONFIGURED", { selector: "dd" })).toBeVisible();
    expect(screen.getByText("NOT CONFIGURED", { selector: "dd" })).toBeVisible();
    expect(screen.getByText("DISABLED", { selector: "dd" })).toBeVisible();
  });
});
