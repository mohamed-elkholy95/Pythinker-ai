import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { usePythinkerStream } from "@/hooks/usePythinkerStream";
import type { PythinkerClient } from "@/lib/pythinker-client";
import { ClientProvider } from "@/providers/ClientProvider";

const fakeClient = {
  sendStop: vi.fn(),
  regenerate: vi.fn(),
  editAndResend: vi.fn(),
  onChat: vi.fn((_chatId: string, _handler: (ev: unknown) => void) => () => {}),
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
  vi.clearAllMocks();
});

describe("usePythinkerStream actions", () => {
  it("stop() forwards chatId to client.sendStop and clears placeholder", () => {
    const { result } = renderHook(() => usePythinkerStream("abcd", []), {
      wrapper,
    });
    act(() => result.current.stop());
    expect(fakeClient.sendStop).toHaveBeenCalledWith("abcd");
    expect(result.current.isStreaming).toBe(false);
  });

  it("regenerate() drops the last assistant message and forwards", () => {
    const { result } = renderHook(
      () =>
        usePythinkerStream("abcd", [
          { id: "u1", role: "user", content: "hi", createdAt: 1 },
          { id: "a1", role: "assistant", content: "yo", createdAt: 2 },
        ]),
      { wrapper },
    );
    act(() => result.current.regenerate());
    // Old assistant gone, new typing-dots placeholder added (empty content + isStreaming).
    const ids = result.current.messages.map((m) => m.id);
    const placeholder = result.current.messages.find(
      (m) => m.role === "assistant" && m.isStreaming && m.content === "",
    );
    expect(ids[0]).toBe("u1");
    expect(placeholder).toBeDefined();
    expect(fakeClient.regenerate).toHaveBeenCalledWith("abcd");
    expect(result.current.isStreaming).toBe(true);
  });

  it("editMessage() rewrites the user bubble and emits 'edit'", () => {
    const { result } = renderHook(
      () =>
        usePythinkerStream("abcd", [
          { id: "u1", role: "user", content: "old", createdAt: 1 },
          { id: "a1", role: "assistant", content: "stale", createdAt: 2 },
        ]),
      { wrapper },
    );
    act(() => result.current.editMessage("u1", "new"));
    const remaining = result.current.messages;
    const u1 = remaining.find((m) => m.id === "u1");
    expect(u1?.content).toBe("new");
    // Original assistant gone; placeholder added.
    expect(remaining.some((m) => m.id === "a1")).toBe(false);
    expect(
      remaining.some(
        (m) => m.role === "assistant" && m.isStreaming && m.content === "",
      ),
    ).toBe(true);
    expect(fakeClient.editAndResend).toHaveBeenCalledWith("abcd", 0, "new");
    expect(result.current.isStreaming).toBe(true);
  });

  it("stop() drops the in-flight placeholder added by regenerate()", () => {
    const { result } = renderHook(
      () =>
        usePythinkerStream("abcd", [
          { id: "u1", role: "user", content: "hi", createdAt: 1 },
          { id: "a1", role: "assistant", content: "yo", createdAt: 2 },
        ]),
      { wrapper },
    );
    act(() => result.current.regenerate());
    // Sanity: a placeholder exists.
    expect(
      result.current.messages.some(
        (m) => m.role === "assistant" && m.isStreaming && m.content === "",
      ),
    ).toBe(true);
    act(() => result.current.stop());
    // Placeholder is now gone, only the user message remains.
    expect(result.current.messages).toEqual([
      { id: "u1", role: "user", content: "hi", createdAt: 1 },
    ]);
    expect(result.current.isStreaming).toBe(false);
  });

  it("regenerate() sends stop first when a stream is in flight", () => {
    const { result } = renderHook(
      () =>
        usePythinkerStream("abcd", [
          { id: "u1", role: "user", content: "hi", createdAt: 1 },
          { id: "a1", role: "assistant", content: "yo", createdAt: 2 },
        ]),
      { wrapper },
    );
    // First regenerate seeds buffer.current; second regenerate should cancel.
    act(() => result.current.regenerate());
    expect(fakeClient.regenerate).toHaveBeenCalledTimes(1);
    expect(fakeClient.sendStop).not.toHaveBeenCalled();
    act(() => result.current.regenerate());
    expect(fakeClient.sendStop).toHaveBeenCalledWith("abcd");
    expect(fakeClient.regenerate).toHaveBeenCalledTimes(2);
  });

  it("handles server error event by clearing the in-flight placeholder", () => {
    // Capture the onChat handler so the test can drive events.
    let capturedHandler: ((ev: any) => void) | null = null;
    fakeClient.onChat.mockImplementationOnce((_chatId, handler) => {
      capturedHandler = handler;
      return () => {};
    });

    const { result } = renderHook(
      () =>
        usePythinkerStream("abcd", [
          { id: "u1", role: "user", content: "hi", createdAt: 1 },
          { id: "a1", role: "assistant", content: "yo", createdAt: 2 },
        ]),
      { wrapper },
    );
    // Seed an in-flight stream by regenerating from a stub history.
    act(() => result.current.regenerate());
    // Confirm placeholder was added.
    expect(
      result.current.messages.some(
        (m) => m.role === "assistant" && m.isStreaming && m.content === "",
      ),
    ).toBe(true);
    // Server rejects.
    act(() => capturedHandler!({ event: "error", detail: "bad" }));
    // Placeholder gone, isStreaming cleared.
    expect(
      result.current.messages.some(
        (m) => m.role === "assistant" && m.isStreaming && m.content === "",
      ),
    ).toBe(false);
    expect(result.current.isStreaming).toBe(false);
  });
});
