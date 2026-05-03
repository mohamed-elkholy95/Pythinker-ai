import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { I18nextProvider } from "react-i18next";

import i18n from "@/i18n";
import { ClientProvider } from "@/providers/ClientProvider";
import { ThreadComposer } from "@/components/thread/ThreadComposer";
import type { PythinkerClient } from "@/lib/pythinker-client";

const fakeClient = { onChat: vi.fn(() => () => {}) };

const MODELS = [
  { name: "anthropic/claude-3-5-sonnet-20241022", is_default: true },
  { name: "anthropic/claude-3-5-haiku-20241022", is_default: false },
];

function wrap(children: React.ReactNode) {
  return (
    <I18nextProvider i18n={i18n}>
      <ClientProvider
        client={fakeClient as unknown as PythinkerClient}
        token="t"
      >
        {children}
      </ClientProvider>
    </I18nextProvider>
  );
}

describe("ThreadComposer × ModelSwitcher", () => {
  it("renders the ModelSwitcher pill when models + currentModel are provided", () => {
    render(
      wrap(
        <ThreadComposer
          onSend={() => {}}
          variant="thread"
          models={MODELS}
          currentModel="anthropic/claude-3-5-sonnet-20241022"
          override={null}
          onModelChange={() => {}}
        />,
      ),
    );
    // The switcher renders the model leaf as the trigger label.
    expect(
      screen.getByRole("button", { name: /claude-3-5-sonnet/i }),
    ).toBeInTheDocument();
  });

  it("forwards selection from the switcher up to onModelChange", () => {
    const onModelChange = vi.fn();
    render(
      wrap(
        <ThreadComposer
          onSend={() => {}}
          variant="thread"
          models={MODELS}
          currentModel="anthropic/claude-3-5-sonnet-20241022"
          override={null}
          onModelChange={onModelChange}
        />,
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: /claude-3-5-sonnet/i }));
    fireEvent.click(screen.getByText("anthropic/claude-3-5-haiku-20241022"));
    expect(onModelChange).toHaveBeenCalledWith(
      "anthropic/claude-3-5-haiku-20241022",
    );
  });

  it("falls back to the read-only modelLabel when no models are available", () => {
    render(
      wrap(
        <ThreadComposer
          onSend={() => {}}
          variant="thread"
          modelLabel="claude-opus-4-5"
        />,
      ),
    );
    // Static span fallback (not a button — the switcher is not rendered).
    expect(screen.getByText("claude-opus-4-5")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /claude-opus-4-5/i }),
    ).toBeNull();
  });
});
