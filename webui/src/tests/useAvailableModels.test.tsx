import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { ClientProvider } from "@/providers/ClientProvider";
import { useAvailableModels } from "@/hooks/useAvailableModels";
import type { PythinkerClient } from "@/lib/pythinker-client";

const fakeClient = { onChat: vi.fn(() => () => {}) };

function wrapper({ children }: { children: ReactNode }) {
  return (
    <ClientProvider
      client={fakeClient as unknown as PythinkerClient}
      token="t"
      modelName="anthropic/claude-3-5-sonnet-20241022"
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
      models: [
        { name: "anthropic/claude-3-5-sonnet-20241022", is_default: true },
        { name: "anthropic/claude-3-5-haiku-20241022", is_default: false },
      ],
    }),
  })) as unknown as typeof fetch;
});

describe("useAvailableModels", () => {
  it("fetches /api/models and exposes the rows", async () => {
    const { result } = renderHook(() => useAvailableModels(), { wrapper });
    await waitFor(() => expect(result.current.models.length).toBe(2));
    expect(result.current.defaultModel).toBe("anthropic/claude-3-5-sonnet-20241022");
  });
});
