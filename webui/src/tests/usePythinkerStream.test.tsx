import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { usePythinkerStream } from "@/hooks/usePythinkerStream";
import type { InboundEvent } from "@/lib/types";
import { ClientProvider } from "@/providers/ClientProvider";

function fakeClient() {
  const handlers = new Map<string, Set<(ev: InboundEvent) => void>>();
  return {
    client: {
      status: "open" as const,
      defaultChatId: null as string | null,
      onStatus: () => () => {},
      onError: () => () => {},
      onChat(chatId: string, h: (ev: InboundEvent) => void) {
        let set = handlers.get(chatId);
        if (!set) {
          set = new Set();
          handlers.set(chatId, set);
        }
        set.add(h);
        return () => set!.delete(h);
      },
      sendMessage: vi.fn(),
      newChat: vi.fn(),
      attach: vi.fn(),
      connect: vi.fn(),
      close: vi.fn(),
      updateUrl: vi.fn(),
    },
    emit(chatId: string, ev: InboundEvent) {
      const set = handlers.get(chatId);
      set?.forEach((h) => h(ev));
    },
  };
}

function wrap(client: ReturnType<typeof fakeClient>["client"]) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <ClientProvider
        client={client as unknown as import("@/lib/pythinker-client").PythinkerClient}
        token="tok"
      >
        {children}
      </ClientProvider>
    );
  };
}

describe("usePythinkerStream", () => {
  it("collapses consecutive tool_hint frames into one trace row", () => {
    const fake = fakeClient();
    const { result } = renderHook(() => usePythinkerStream("chat-t", []), {
      wrapper: wrap(fake.client),
    });

    act(() => {
      fake.emit("chat-t", {
        event: "message",
        chat_id: "chat-t",
        text: 'weather("get")',
        kind: "tool_hint",
      });
      fake.emit("chat-t", {
        event: "message",
        chat_id: "chat-t",
        text: 'search "hk weather"',
        kind: "tool_hint",
      });
    });

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].kind).toBe("trace");
    expect(result.current.messages[0].role).toBe("tool");
    expect(result.current.messages[0].traces).toEqual([
      'weather("get")',
      'search "hk weather"',
    ]);

    act(() => {
      fake.emit("chat-t", {
        event: "message",
        chat_id: "chat-t",
        text: "## Summary",
      });
    });

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[1].role).toBe("assistant");
    expect(result.current.messages[1].kind).toBeUndefined();
  });

  it("drops the placeholder when stream_end arrives with no delta text", () => {
    // Reasoning models with tool calls produce a "tool pivot" stream: the
    // model emits only ``<think>...</think>`` (which the runtime strips) and
    // then a tool call, so the WS stream for that turn ends with zero
    // delta text. Finalizing the placeholder there leaves an empty assistant
    // bubble in the thread for every tool pivot. We drop the placeholder
    // instead and let the actual answer turn (or the tool trace) speak for
    // itself.
    const fake = fakeClient();
    const { result } = renderHook(() => usePythinkerStream("chat-t", []), {
      wrapper: wrap(fake.client),
    });

    act(() => {
      // First delta primes the assistant placeholder bubble.
      fake.emit("chat-t", {
        event: "delta",
        chat_id: "chat-t",
        text: "",
      });
      fake.emit("chat-t", {
        event: "stream_end",
        chat_id: "chat-t",
      });
    });

    expect(result.current.messages).toHaveLength(0);
    expect(result.current.isStreaming).toBe(false);
  });

  it("finalizes the placeholder when stream_end arrives with answer text", () => {
    const fake = fakeClient();
    const { result } = renderHook(() => usePythinkerStream("chat-t", []), {
      wrapper: wrap(fake.client),
    });

    act(() => {
      fake.emit("chat-t", {
        event: "delta",
        chat_id: "chat-t",
        text: "Hello ",
      });
      fake.emit("chat-t", {
        event: "delta",
        chat_id: "chat-t",
        text: "world",
      });
      fake.emit("chat-t", {
        event: "stream_end",
        chat_id: "chat-t",
      });
    });

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].role).toBe("assistant");
    expect(result.current.messages[0].content).toBe("Hello world");
    expect(result.current.messages[0].isStreaming).toBe(false);
    expect(result.current.isStreaming).toBe(false);
  });
});
