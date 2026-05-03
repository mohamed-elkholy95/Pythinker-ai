import { render } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { describe, expect, it } from "vitest";

import { MessageBubble } from "@/components/MessageBubble";
import i18n from "@/i18n";

function renderTypingPlaceholder() {
  return render(
    <I18nextProvider i18n={i18n}>
      <MessageBubble
        message={{
          id: "a1",
          role: "assistant",
          content: "",
          isStreaming: true,
          createdAt: 1,
        }}
        onRegenerate={() => {}}
        onEdit={() => {}}
      />
    </I18nextProvider>,
  );
}

describe("reduced-motion gating", () => {
  it("gates the typing-dots bounce animation behind motion-safe:", () => {
    const { container } = renderTypingPlaceholder();
    // The Dot spans use animate-bounce — must now be motion-safe-prefixed.
    const dots = container.querySelectorAll("span.rounded-full");
    expect(dots.length).toBeGreaterThan(0);
    for (const dot of Array.from(dots)) {
      // Either there's no animate- class at all, or it's gated.
      expect(dot.className).not.toMatch(/(?<!motion-safe:)animate-bounce/);
    }
  });

  it("gates the stream-cursor pulse behind motion-safe:", () => {
    const { container } = render(
      <I18nextProvider i18n={i18n}>
        <MessageBubble
          message={{
            id: "a1",
            role: "assistant",
            content: "partial",
            isStreaming: true,
            createdAt: 1,
          }}
          onRegenerate={() => {}}
          onEdit={() => {}}
        />
      </I18nextProvider>,
    );
    const cursor = container.querySelector("span[aria-label]");
    expect(cursor?.className ?? "").not.toMatch(/(?<!motion-safe:)animate-pulse/);
  });

  it("gates the bubble entrance animation behind motion-safe:", () => {
    const { container } = renderTypingPlaceholder();
    // The outermost div carries baseAnim.
    const root = container.firstElementChild!;
    expect(root.className).not.toMatch(/(?<!motion-safe:)animate-in/);
  });
});
