import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePythinkerStream } from "@/hooks/usePythinkerStream";
import type { PythinkerClient } from "@/lib/pythinker-client";
import { ClientProvider } from "@/providers/ClientProvider";

const fakeClient = {
  sendStop: vi.fn(),
  regenerate: vi.fn(),
  editAndResend: vi.fn(),
  onChat: vi.fn(
    (_chatId: string, _handler: (ev: unknown) => void) => () => {},
  ),
  onError: vi.fn(() => () => {}),
  newChat: vi.fn().mockResolvedValue("abcd"),
  sendMessage: vi.fn(),
};

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
  vi.useFakeTimers();
  // The hook coalesces deltas through requestAnimationFrame. Vitest's default
  // ``useFakeTimers`` does not mock rAF, so make it run the callback inline
  // for deterministic assertions on rendered content.
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    cb(0);
    return 0;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  vi.clearAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("usePythinkerStream latency tracking", () => {
  it("stamps latencyMs=0 on the placeholder on send()", () => {
    const { result } = renderHook(() => usePythinkerStream("abcd", []), {
      wrapper,
    });
    act(() => result.current.send("hi"));
    const placeholder = result.current.messages.find(
      (m) => m.role === "assistant" && m.isStreaming,
    );
    expect(placeholder?.latencyMs).toBe(0);
  });

  it("ticks latencyMs while waiting for the first delta", () => {
    const { result } = renderHook(() => usePythinkerStream("abcd", []), {
      wrapper,
    });
    act(() => result.current.send("hi"));
    act(() => {
      vi.advanceTimersByTime(2_300);
    });
    const placeholder = result.current.messages.find(
      (m) => m.role === "assistant" && m.isStreaming,
    );
    expect(placeholder?.latencyMs).toBeGreaterThanOrEqual(2_000);
  });

  it("clears latencyMs when the first delta lands", () => {
    let capturedHandler: ((ev: unknown) => void) | null = null;
    fakeClient.onChat.mockImplementationOnce((_chatId, handler) => {
      capturedHandler = handler;
      return () => {};
    });
    const { result } = renderHook(() => usePythinkerStream("abcd", []), {
      wrapper,
    });
    act(() => result.current.send("hi"));
    act(() => {
      vi.advanceTimersByTime(1_500);
    });
    act(() =>
      capturedHandler!({ event: "delta", chat_id: "abcd", text: "hello" }),
    );
    const bubble = result.current.messages.find((m) => m.role === "assistant");
    expect(bubble?.content).toBe("hello");
    expect(bubble?.latencyMs).toBeUndefined();
  });

  it("clears latency tracker on stream_end", () => {
    let capturedHandler: ((ev: unknown) => void) | null = null;
    fakeClient.onChat.mockImplementationOnce((_chatId, handler) => {
      capturedHandler = handler;
      return () => {};
    });
    const { result } = renderHook(() => usePythinkerStream("abcd", []), {
      wrapper,
    });
    act(() => result.current.send("hi"));
    act(() =>
      capturedHandler!({ event: "stream_end", chat_id: "abcd" }),
    );
    // Advancing the clock further must NOT mutate state — the interval must
    // already be cleared.
    const before = result.current.messages;
    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(result.current.messages).toBe(before);
  });

  it("clears latency tracker on stop()", () => {
    const { result } = renderHook(() => usePythinkerStream("abcd", []), {
      wrapper,
    });
    act(() => result.current.send("hi"));
    act(() => result.current.stop());
    const before = result.current.messages;
    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(result.current.messages).toBe(before);
  });
});
