import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StackedUsageBar } from "./StackedUsageBar";

describe("StackedUsageBar", () => {
  it("renders three segments with proportional widths", () => {
    const { container } = render(
      <StackedUsageBar floor={10_000} used={50_000} limit={100_000} />,
    );
    const segments = container.querySelectorAll("[data-segment]");
    expect(segments).toHaveLength(3);
    expect(segments[0].getAttribute("data-segment")).toBe("floor");
    expect(segments[1].getAttribute("data-segment")).toBe("history");
    expect(segments[2].getAttribute("data-segment")).toBe("headroom");
  });

  it("shows percentage of used vs limit", () => {
    render(<StackedUsageBar floor={0} used={50_000} limit={100_000} />);
    expect(screen.getByText(/50%/)).toBeTruthy();
  });

  it("clamps overflow to 100%", () => {
    render(<StackedUsageBar floor={0} used={150_000} limit={100_000} />);
    expect(screen.getByText(/100%/)).toBeTruthy();
  });

  it("hides floor segment when floor=0", () => {
    const { container } = render(
      <StackedUsageBar floor={0} used={50_000} limit={100_000} />,
    );
    const segments = container.querySelectorAll("[data-segment]");
    expect(segments).toHaveLength(2);
  });
});
