import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MessageBubble } from "@/components/MessageBubble";
import type { UIMessage } from "@/lib/types";

describe("MessageBubble", () => {
  it("renders user messages as right-aligned pills", () => {
    const message: UIMessage = {
      id: "u1",
      role: "user",
      content: "hello",
      createdAt: Date.now(),
    };

    const { container } = render(<MessageBubble message={message} />);
    const row = container.firstElementChild;
    const pill = screen.getByText("hello");

    expect(row).toHaveClass("ml-auto", "flex");
    expect(pill).toHaveClass("ml-auto", "w-fit", "rounded-[18px]");
  });

  it("renders trace messages as compact chips that expand to full lines", () => {
    const message: UIMessage = {
      id: "t1",
      role: "tool",
      kind: "trace",
      content: 'search "hk weather"',
      traces: ['weather("get")', 'search "hk weather"'],
      createdAt: Date.now(),
    };

    render(<MessageBubble message={message} />);

    // Chips render the tool verbs by default; the full lines stay hidden
    // until the user expands the group.
    expect(screen.getByText("weather")).toBeInTheDocument();
    expect(screen.getByText("search")).toBeInTheDocument();
    expect(screen.queryByText('weather("get")')).toBeNull();

    fireEvent.click(
      screen.getByRole("button", { name: /show tool details/i }),
    );
    expect(screen.getByText('weather("get")')).toBeInTheDocument();
    expect(screen.getByText('search "hk weather"')).toBeInTheDocument();
  });
});
