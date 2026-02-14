import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { DetailPanel } from "../detail-panel";

describe("DetailPanel", () => {
  it("renders coming soon message", () => {
    render(<DetailPanel />);

    expect(screen.getByText("Coming Soon")).toBeInTheDocument();
    expect(
      screen.getByText("Agent details will appear here in a future update."),
    ).toBeInTheDocument();
  });
});
