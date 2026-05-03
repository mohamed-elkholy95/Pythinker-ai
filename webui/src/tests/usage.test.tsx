import { renderHook, act, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { ClientProvider } from "@/providers/ClientProvider";
import { useSessionUsage } from "@/hooks/useSessionUsage";

const fakeClient = {
  onChat: vi.fn((_: string, _h: (ev: unknown) => void) => () => {}),
};

function wrapper({ children }: { children: ReactNode }) {
  return (
    <ClientProvider client={fakeClient as any} token="t">
      {children}
    </ClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  global.fetch = vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => ({ used: 5_000, limit: 128_000 }),
  })) as unknown as typeof fetch;
});

describe("useSessionUsage", () => {
  it("fetches usage on mount when chatId is provided", async () => {
    const { result } = renderHook(
      () => useSessionUsage("abcd"),
      { wrapper },
    );
    await waitFor(() => expect(result.current.used).toBe(5_000));
    expect(result.current.limit).toBe(128_000);
  });

  it("returns zero state when chatId is null", () => {
    const { result } = renderHook(() => useSessionUsage(null), { wrapper });
    expect(result.current.used).toBe(0);
    expect(result.current.limit).toBe(0);
  });

  it("refetches on stream_end events", async () => {
    let capturedHandler: ((ev: any) => void) | null = null;
    fakeClient.onChat.mockImplementationOnce((_, handler) => {
      capturedHandler = handler;
      return () => {};
    });
    renderHook(() => useSessionUsage("abcd"), { wrapper });
    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));
    act(() => capturedHandler!({ event: "stream_end" }));
    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(2));
  });

  it("ignores non-stream_end events from the chat channel", async () => {
    let capturedHandler: ((ev: any) => void) | null = null;
    fakeClient.onChat.mockImplementationOnce((_, handler) => {
      capturedHandler = handler;
      return () => {};
    });
    renderHook(() => useSessionUsage("abcd"), { wrapper });
    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));
    act(() => capturedHandler!({ event: "delta", text: "x" }));
    // Still 1 — delta should not trigger a refetch.
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  it("returns zero state on fetch error (does not throw)", async () => {
    global.fetch = vi.fn(async () => ({
      ok: false,
      status: 404,
      json: async () => ({}),
    })) as unknown as typeof fetch;
    const { result } = renderHook(() => useSessionUsage("abcd"), { wrapper });
    // Wait for fetch to actually have run, so the assertion below tests the
    // post-error state rather than the (also-zero) initial state.
    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));
    expect(result.current.used).toBe(0);
    expect(result.current.limit).toBe(0);
  });
});
