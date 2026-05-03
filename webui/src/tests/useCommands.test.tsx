import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { ClientProvider } from "@/providers/ClientProvider";
import { useCommands } from "@/hooks/useCommands";
import type { PythinkerClient } from "@/lib/pythinker-client";

const fakeClient = { onChat: vi.fn(() => () => {}) };

function wrapper({ children }: { children: ReactNode }) {
  return (
    <ClientProvider
      client={fakeClient as unknown as PythinkerClient}
      token="t"
    >
      {children}
    </ClientProvider>
  );
}

beforeEach(() => {
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
});

describe("useCommands", () => {
  it("fetches /api/commands once on mount", async () => {
    const { result } = renderHook(() => useCommands(), { wrapper });
    await waitFor(() => expect(result.current.commands.length).toBe(2));
    expect(result.current.commands[0].name).toBe("/help");
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  it("returns loading=false after the fetch resolves", async () => {
    const { result } = renderHook(() => useCommands(), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
  });
});
