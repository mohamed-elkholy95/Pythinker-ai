import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MessageTimestamp } from "@/components/MessageTimestamp";

describe("MessageTimestamp", () => {
  it("renders nothing when createdAt is 0 or undefined", () => {
    const { container } = render(<MessageTimestamp createdAt={0} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders a <time> element with a machine-readable datetime attr", () => {
    const t = new Date("2026-04-26T14:32:00Z").getTime();
    render(<MessageTimestamp createdAt={t} />);
    const el = screen.getByRole("time");
    expect(el).toHaveAttribute("datetime", new Date(t).toISOString());
  });
});
