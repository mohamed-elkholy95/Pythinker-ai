import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ThreadViewport } from "@/components/thread/ThreadViewport";
import type { UIMessage } from "@/lib/types";

function msg(id: string, role: "user" | "assistant", content: string): UIMessage {
  return { id, role, content, createdAt: 0 };
}

describe("ThreadViewport scroll-to-message", () => {
  it("calls scrollIntoView on the targeted bubble when scrollTarget is set", () => {
    const scrollIntoView = vi.fn();
    // jsdom doesn't ship a real scrollIntoView; stub it on every element.
    Element.prototype.scrollIntoView = scrollIntoView;

    const messages = [
      msg("u1", "user", "first question"),
      msg("a1", "assistant", "first answer"),
      msg("u2", "user", "second question"),
      msg("a2", "assistant", "second answer with the match"),
    ];

    const { rerender } = render(
      <ThreadViewport
        messages={messages}
        isStreaming={false}
        composer={null}
        scrollTarget={null}
      />,
    );
    expect(scrollIntoView).not.toHaveBeenCalled();

    rerender(
      <ThreadViewport
        messages={messages}
        isStreaming={false}
        composer={null}
        scrollTarget={{ messageIndex: 3, token: 1 }}
      />,
    );
    // The targeted bubble (`data-message-index="3"`) was scrolled into view.
    expect(scrollIntoView).toHaveBeenCalled();
  });

  it("re-fires the effect when token bumps even at the same index", () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    const messages = [
      msg("u1", "user", "q"),
      msg("a1", "assistant", "a"),
    ];

    const { rerender } = render(
      <ThreadViewport
        messages={messages}
        isStreaming={false}
        composer={null}
        scrollTarget={{ messageIndex: 1, token: 1 }}
      />,
    );
    const firstCallCount = scrollIntoView.mock.calls.length;
    expect(firstCallCount).toBeGreaterThan(0);

    rerender(
      <ThreadViewport
        messages={messages}
        isStreaming={false}
        composer={null}
        scrollTarget={{ messageIndex: 1, token: 2 }}
      />,
    );
    expect(scrollIntoView.mock.calls.length).toBeGreaterThan(firstCallCount);
  });
});
