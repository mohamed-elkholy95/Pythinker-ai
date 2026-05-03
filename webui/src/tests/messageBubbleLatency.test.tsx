import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { I18nextProvider } from "react-i18next";

import { MessageBubble } from "@/components/MessageBubble";
import i18n from "@/i18n";
import type { UIMessage } from "@/lib/types";

function placeholder(latencyMs: number | undefined): UIMessage {
  return {
    id: "a1",
    role: "assistant",
    content: "",
    isStreaming: true,
    createdAt: 1,
    ...(latencyMs !== undefined ? { latencyMs } : {}),
  };
}

describe("MessageBubble latency subscript", () => {
  it("renders thinking… Ns when latencyMs >= 1000", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <MessageBubble message={placeholder(2_300)} />
      </I18nextProvider>,
    );
    expect(screen.getByText(/thinking…\s*2s/i)).toBeInTheDocument();
  });

  it("does not render the subscript below 1s to avoid flicker", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <MessageBubble message={placeholder(450)} />
      </I18nextProvider>,
    );
    expect(screen.queryByText(/thinking…/i)).toBeNull();
  });

  it("does not render the subscript when latencyMs is undefined", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <MessageBubble message={placeholder(undefined)} />
      </I18nextProvider>,
    );
    expect(screen.queryByText(/thinking…/i)).toBeNull();
  });
});
