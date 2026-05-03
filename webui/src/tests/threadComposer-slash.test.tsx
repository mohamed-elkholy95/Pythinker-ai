import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { I18nextProvider } from "react-i18next";

import i18n from "@/i18n";
import { ClientProvider } from "@/providers/ClientProvider";
import { ThreadComposer } from "@/components/thread/ThreadComposer";
import type { PythinkerClient } from "@/lib/pythinker-client";

const fakeClient = { onChat: vi.fn(() => () => {}) };

function r() {
  // Stub global.fetch so useCommands resolves with two rows.
  global.fetch = vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      commands: [
        { name: "/help", summary: "Show available commands", usage: "" },
        { name: "/stop", summary: "Stop the current task", usage: "" },
      ],
    }),
  })) as unknown as typeof fetch;
  return render(
    <I18nextProvider i18n={i18n}>
      <ClientProvider
        client={fakeClient as unknown as PythinkerClient}
        token="t"
      >
        <ThreadComposer onSend={() => {}} variant="thread" />
      </ClientProvider>
    </I18nextProvider>,
  );
}

describe("ThreadComposer slash palette", () => {
  it("opens the palette when the user types '/' as the first character", async () => {
    r();
    const ta = screen.getByLabelText(/message input/i);
    fireEvent.change(ta, { target: { value: "/" } });
    expect(await screen.findByRole("listbox")).toBeInTheDocument();
    expect(await screen.findByText("/help")).toBeInTheDocument();
  });

  it("does not open the palette when '/' appears mid-text", () => {
    r();
    const ta = screen.getByLabelText(/message input/i);
    fireEvent.change(ta, { target: { value: "what is /help" } });
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("Enter accepts the highlighted command and fills the textarea with name + space", async () => {
    r();
    const ta = screen.getByLabelText(/message input/i) as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "/" } });
    await screen.findByRole("listbox");
    await screen.findByText("/help");
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(ta.value).toBe("/help ");
    // Palette closes once the textarea contains a space.
    expect(screen.queryByRole("listbox")).toBeNull();
  });
});
