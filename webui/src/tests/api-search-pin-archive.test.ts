import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  searchSessions,
  togglePinSession,
  toggleArchiveSession,
} from "@/lib/api";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("searchSessions", () => {
  it("hits /api/search with q/offset/limit", async () => {
    const fetchSpy = vi.fn(async (_url: string, _init?: RequestInit) => ({
      ok: true,
      status: 200,
      json: async () => ({
        results: [
          {
            session_key: "websocket:abcd",
            message_index: 3,
            role: "user",
            snippet: "…hello world…",
            match_offsets: [[1, 6]],
            title: "Hi",
            archived: false,
          },
        ],
        offset: 0,
        limit: 50,
        has_more: false,
      }),
    }));
    global.fetch = fetchSpy as unknown as typeof fetch;
    const out = await searchSessions("t", "hello", { offset: 0, limit: 50 });
    const url = fetchSpy.mock.calls[0][0];
    expect(url).toContain("/api/search");
    expect(url).toContain("q=hello");
    expect(url).toContain("offset=0");
    expect(url).toContain("limit=50");
    expect(out.results).toHaveLength(1);
    expect(out.results[0].sessionKey).toBe("websocket:abcd");
    expect(out.results[0].matchOffsets).toEqual([[1, 6]]);
  });
});

describe("togglePinSession / toggleArchiveSession", () => {
  it("togglePinSession returns the new pinned state", async () => {
    global.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ pinned: true }),
    })) as unknown as typeof fetch;
    expect(await togglePinSession("t", "websocket:abcd")).toBe(true);
  });

  it("toggleArchiveSession returns the new archived state", async () => {
    global.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ archived: true }),
    })) as unknown as typeof fetch;
    expect(await toggleArchiveSession("t", "websocket:abcd")).toBe(true);
  });
});
