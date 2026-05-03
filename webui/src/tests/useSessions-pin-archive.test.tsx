import { renderHook, act, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useSessions } from "@/hooks/useSessions";
import type { PythinkerClient } from "@/lib/pythinker-client";
import { ClientProvider } from "@/providers/ClientProvider";

const fakeClient = {
  newChat: vi.fn().mockResolvedValue("abcd"),
  onChat: vi.fn(() => () => {}),
};

function wrapper({ children }: { children: ReactNode }) {
  return (
    <ClientProvider client={fakeClient as unknown as PythinkerClient} token="t">
      {children}
    </ClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  global.fetch = vi.fn(async (url: unknown) => {
    if (typeof url !== "string") throw new Error("not a url");
    if (url.endsWith("/pin")) {
      return { ok: true, status: 200, json: async () => ({ pinned: true }) };
    }
    if (url.endsWith("/archive")) {
      return { ok: true, status: 200, json: async () => ({ archived: true }) };
    }
    if (url.endsWith("/api/sessions")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          sessions: [
            {
              key: "websocket:a",
              created_at: null,
              updated_at: null,
              preview: "p",
              title: "A",
              pinned: false,
              archived: false,
            },
            {
              key: "websocket:b",
              created_at: null,
              updated_at: null,
              preview: "p2",
              title: "B",
              pinned: true,
              archived: false,
            },
            {
              key: "websocket:c",
              created_at: null,
              updated_at: null,
              preview: "p3",
              title: "C",
              pinned: false,
              archived: true,
            },
          ],
        }),
      };
    }
    throw new Error(`unexpected url: ${url}`);
  }) as unknown as typeof fetch;
});

describe("useSessions section slices", () => {
  it("partitions rows into pinned / recent / archived slices", async () => {
    const { result } = renderHook(() => useSessions(), { wrapper });
    await waitFor(() =>
      expect(result.current.recentSessions.length).toBeGreaterThan(0),
    );
    expect(result.current.pinnedSessions.map((s) => s.key)).toEqual([
      "websocket:b",
    ]);
    expect(result.current.recentSessions.map((s) => s.key)).toEqual([
      "websocket:a",
    ]);
    expect(result.current.archivedSessions.map((s) => s.key)).toEqual([
      "websocket:c",
    ]);
  });

  it("togglePin moves a session into the pinned slice optimistically", async () => {
    const { result } = renderHook(() => useSessions(), { wrapper });
    await waitFor(() =>
      expect(result.current.recentSessions.length).toBeGreaterThan(0),
    );
    await act(async () => {
      await result.current.togglePin("websocket:a");
    });
    expect(result.current.pinnedSessions.map((s) => s.key)).toContain(
      "websocket:a",
    );
    expect(result.current.recentSessions.map((s) => s.key)).not.toContain(
      "websocket:a",
    );
  });

  it("toggleArchive moves a session into the archived slice optimistically", async () => {
    const { result } = renderHook(() => useSessions(), { wrapper });
    await waitFor(() =>
      expect(result.current.recentSessions.length).toBeGreaterThan(0),
    );
    await act(async () => {
      await result.current.toggleArchive("websocket:a");
    });
    expect(result.current.archivedSessions.map((s) => s.key)).toContain(
      "websocket:a",
    );
  });
});
