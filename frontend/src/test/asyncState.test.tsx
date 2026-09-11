import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  AsyncState,
  EmptyState,
  ErrorState,
  PartialState,
} from "../components/AsyncState";

describe("data-state primitives", () => {
  it("renders loading before content", () => {
    render(<AsyncState loading error={null}><p>Content</p></AsyncState>);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    expect(screen.queryByText("Content")).not.toBeInTheDocument();
  });

  it("renders empty, partial, and error states accessibly", () => {
    const view = render(<EmptyState title="No evidence" />);
    expect(screen.getByText("No evidence")).toBeVisible();
    view.rerender(<PartialState>One provider was unavailable.</PartialState>);
    expect(screen.getByRole("status")).toHaveTextContent("Partial data");
    view.rerender(<ErrorState error={new Error("Backend offline")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Backend offline");
  });
});
