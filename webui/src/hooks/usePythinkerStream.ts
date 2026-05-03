import { useCallback, useEffect, useRef, useState } from "react";

import { useClient } from "@/providers/ClientProvider";
import type { StreamError } from "@/lib/pythinker-client";
import type {
  InboundEvent,
  OutboundMedia,
  UIImage,
  UIMessage,
} from "@/lib/types";

interface StreamBuffer {
  /** ID of the assistant message currently receiving deltas. */
  messageId: string;
  /** Sequence of deltas accumulated in order. */
  parts: string[];
}

interface LatencyTracker {
  /** ID of the placeholder bubble being timed. */
  messageId: string;
  /** Timestamp captured at send() / regenerate() / editMessage(). */
  startedAt: number;
  /** ID returned by ``setInterval`` so cleanup can clear it. */
  intervalId: ReturnType<typeof setInterval>;
}

/**
 * Subscribe to a chat by ID. Returns the in-memory message list for the chat,
 * a streaming flag, and a ``send`` function. Initial history must be seeded
 * separately (e.g. via ``fetchSessionMessages``) since the server only replays
 * live events.
 */
/** Payload passed to ``send`` when the user attaches one or more images.
 *
 * ``media`` is handed to the wire client verbatim; ``preview`` powers the
 * optimistic user bubble (blob URLs so the preview appears before the server
 * acks the frame). Keeping the two separate lets the bubble re-use the local
 * blob URL even after the server persists the file under a different name. */
export interface SendImage {
  media: OutboundMedia;
  preview: UIImage;
}

export function usePythinkerStream(
  chatId: string | null,
  initialMessages: UIMessage[] = [],
): {
  messages: UIMessage[];
  isStreaming: boolean;
  send: (content: string, images?: SendImage[]) => void;
  /** Cancel the in-flight turn for ``chatId`` and drop the typing placeholder. */
  stop: () => void;
  /** Drop the trailing assistant turn and ask the agent to produce a new one. */
  regenerate: () => void;
  /** Rewrite the user bubble with id ``messageId`` and resubmit from there. */
  editMessage: (messageId: string, newContent: string) => void;
  setMessages: React.Dispatch<React.SetStateAction<UIMessage[]>>;
  /** Latest transport-level fault raised since the last ``dismissStreamError``.
   * ``null`` when there is nothing to show. */
  streamError: StreamError | null;
  /** Clear the current ``streamError`` (e.g. after the user dismisses the
   * notification or starts a fresh action). */
  dismissStreamError: () => void;
} {
  const { client } = useClient();
  const [messages, setMessages] = useState<UIMessage[]>(initialMessages);
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamError, setStreamError] = useState<StreamError | null>(null);
  const buffer = useRef<StreamBuffer | null>(null);
  const latency = useRef<LatencyTracker | null>(null);
  // rAF coalescer: bursty WS frames (5-10 small deltas in the same frame) used
  // to trigger that many re-renders. We append to ``buffer.current.parts``
  // synchronously and schedule a single flush per animation frame, so the
  // displayed text grows smoothly at the device's native frame rate even when
  // the wire delivers chunks unevenly.
  const flushHandle = useRef<number | null>(null);

  const cancelFlush = useCallback(() => {
    if (flushHandle.current !== null) {
      cancelAnimationFrame(flushHandle.current);
      flushHandle.current = null;
    }
  }, []);

  const scheduleFlush = useCallback(() => {
    if (flushHandle.current !== null) return;
    flushHandle.current = requestAnimationFrame(() => {
      flushHandle.current = null;
      const buf = buffer.current;
      if (!buf) return;
      const combined = buf.parts.join("");
      const targetId = buf.messageId;
      setMessages((prev) =>
        prev.map((m) => {
          if (m.id !== targetId) return m;
          if (m.latencyMs === undefined) return { ...m, content: combined };
          const { latencyMs: _drop, ...rest } = m;
          void _drop;
          return { ...rest, content: combined };
        }),
      );
    });
  }, []);

  const startLatency = useCallback((placeholderId: string) => {
    if (latency.current) {
      clearInterval(latency.current.intervalId);
    }
    const startedAt = Date.now();
    const intervalId = setInterval(() => {
      const elapsed = Date.now() - startedAt;
      setMessages((prev) =>
        prev.map((m) =>
          m.id === placeholderId && m.isStreaming
            ? { ...m, latencyMs: elapsed }
            : m,
        ),
      );
    }, 1_000);
    latency.current = { messageId: placeholderId, startedAt, intervalId };
  }, []);

  const stopLatency = useCallback((stripFromId?: string) => {
    if (!latency.current) return;
    clearInterval(latency.current.intervalId);
    const trackedId = latency.current.messageId;
    latency.current = null;
    const target = stripFromId ?? trackedId;
    setMessages((prev) =>
      prev.map((m) => {
        if (m.id !== target) return m;
        if (m.latencyMs === undefined) return m;
        const { latencyMs: _drop, ...rest } = m;
        void _drop;
        return rest;
      }),
    );
  }, []);

  useEffect(() => {
    return client.onError((err) => {
      setStreamError(err);
      // A transport fault means no further deltas are coming — drop the
      // typing-dots placeholder so the user isn't left staring at it.
      const stuckId = buffer.current?.messageId;
      cancelFlush();
      buffer.current = null;
      stopLatency(stuckId);
      setIsStreaming(false);
      if (stuckId) {
        setMessages((prev) =>
          prev.filter((m) => !(m.id === stuckId && m.content.trim() === "")),
        );
      }
    });
  }, [client, stopLatency, cancelFlush]);

  const dismissStreamError = useCallback(() => setStreamError(null), []);

  // Reset local state when switching chats. ``streamError`` is scoped to the
  // send that triggered it, so a chat swap should wipe it out: a stale
  // "Message too large" banner on a freshly-opened chat-B would confuse the
  // user about which send actually failed (and in which chat).
  useEffect(() => {
    setMessages(initialMessages);
    setIsStreaming(false);
    setStreamError(null);
    cancelFlush();
    buffer.current = null;
    if (latency.current) {
      clearInterval(latency.current.intervalId);
      latency.current = null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatId]);

  useEffect(() => {
    if (!chatId) return;

    const handle = (ev: InboundEvent) => {
      if (ev.event === "delta") {
        const id = buffer.current?.messageId ?? crypto.randomUUID();
        if (!buffer.current) {
          buffer.current = { messageId: id, parts: [] };
          setMessages((prev) => [
            ...prev,
            {
              id,
              role: "assistant",
              content: "",
              isStreaming: true,
              createdAt: Date.now(),
            },
          ]);
          setIsStreaming(true);
        }
        buffer.current.parts.push(ev.text);
        // First delta — drop the latency tick so the bubble reads cleanly.
        stopLatency(buffer.current.messageId);
        // Defer the actual setMessages to the next animation frame so a
        // burst of small WS chunks collapses into a single React render.
        scheduleFlush();
        return;
      }

      if (ev.event === "stream_end") {
        if (!buffer.current) {
          setIsStreaming(false);
          return;
        }
        const finalId = buffer.current.messageId;
        const finalText = buffer.current.parts.join("");
        cancelFlush();
        buffer.current = null;
        stopLatency(finalId);
        setIsStreaming(false);
        // Flush any deltas that hadn't yet been painted in the trailing
        // frame, then mark the bubble as no longer streaming.
        setMessages((prev) =>
          prev.map((m) =>
            m.id === finalId
              ? { ...m, content: finalText, isStreaming: false }
              : m,
          ),
        );
        return;
      }

      if (ev.event === "message") {
        // Intermediate agent breadcrumbs (tool-call hints, raw progress).
        // Attach them to the last trace row if it was the last emitted item
        // so a sequence of calls collapses into one compact trace group.
        if (ev.kind === "tool_hint" || ev.kind === "progress") {
          const line = ev.text;
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last && last.kind === "trace" && !last.isStreaming) {
              const merged: UIMessage = {
                ...last,
                traces: [...(last.traces ?? [last.content]), line],
                content: line,
              };
              return [...prev.slice(0, -1), merged];
            }
            return [
              ...prev,
              {
                id: crypto.randomUUID(),
                role: "tool",
                kind: "trace",
                content: line,
                traces: [line],
                createdAt: Date.now(),
              },
            ];
          });
          return;
        }

        // A complete (non-streamed) assistant message. If a stream was in
        // flight, drop the placeholder so we don't render the text twice.
        const activeId = buffer.current?.messageId;
        cancelFlush();
        buffer.current = null;
        setIsStreaming(false);
        setMessages((prev) => {
          const filtered = activeId ? prev.filter((m) => m.id !== activeId) : prev;
          return [
            ...filtered,
            {
              id: crypto.randomUUID(),
              role: "assistant",
              content: ev.text,
              createdAt: Date.now(),
            },
          ];
        });
        return;
      }
      if (ev.event === "error") {
        // Server rejected the request — clear the typing-dots placeholder so
        // the user isn't stuck staring at it. The dedicated error UI is driven
        // by ``client.onError`` (set up via the useEffect at the top of this
        // hook); we just need to land the local UI state cleanly.
        const stuckId = buffer.current?.messageId;
        cancelFlush();
        buffer.current = null;
        stopLatency(stuckId);
        setIsStreaming(false);
        if (stuckId) {
          setMessages((prev) =>
            prev.filter((m) => !(m.id === stuckId && m.content.trim() === "")),
          );
        }
        return;
      }
      // ``attached`` frames aren't actionable here; the client shell handles them.
    };

    const unsub = client.onChat(chatId, handle);
    return () => {
      unsub();
      cancelFlush();
      buffer.current = null;
      if (latency.current) {
        clearInterval(latency.current.intervalId);
        latency.current = null;
      }
    };
  }, [chatId, client, stopLatency, scheduleFlush, cancelFlush]);

  const send = useCallback(
    (content: string, images?: SendImage[]) => {
      if (!chatId) return;
      const hasImages = !!images && images.length > 0;
      // Text is optional when images are attached — the agent will still see
      // the image blocks via ``media`` paths.
      if (!hasImages && !content.trim()) return;

      const previews = hasImages ? images!.map((i) => i.preview) : undefined;
      // Pre-allocate the assistant placeholder bubble so the typing indicator
      // shows immediately (between Enter and the first delta) instead of
      // appearing only once tokens start streaming. The delta handler reuses
      // this bubble's id via buffer.current so we don't spawn a duplicate.
      const placeholderId = crypto.randomUUID();
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "user",
          content,
          createdAt: Date.now(),
          ...(previews ? { images: previews } : {}),
        },
        {
          id: placeholderId,
          role: "assistant",
          content: "",
          isStreaming: true,
          latencyMs: 0,
          createdAt: Date.now(),
        },
      ]);
      buffer.current = { messageId: placeholderId, parts: [] };
      setIsStreaming(true);
      startLatency(placeholderId);
      const wireMedia = hasImages ? images!.map((i) => i.media) : undefined;
      client.sendMessage(chatId, content, wireMedia);
    },
    [chatId, client, startLatency],
  );

  const stop = useCallback(() => {
    if (!chatId) return;
    client.sendStop(chatId);
    // Drop the empty typing-dots placeholder; the agent will not produce
    // further deltas for the cancelled turn.
    const stuckId = buffer.current?.messageId;
    cancelFlush();
    buffer.current = null;
    if (latency.current) {
      clearInterval(latency.current.intervalId);
      latency.current = null;
    }
    setIsStreaming(false);
    if (stuckId) {
      setMessages((prev) =>
        prev.filter((m) => !(m.id === stuckId && m.content.trim() === "")),
      );
    }
  }, [chatId, client, cancelFlush]);

  const regenerate = useCallback(() => {
    if (!chatId) return;
    // Cancel any in-flight stream before swapping the buffer; otherwise late
    // delta events for the old turn would leak into the new placeholder bubble.
    if (buffer.current) {
      client.sendStop(chatId);
    }
    cancelFlush();
    // Pre-allocate the typing placeholder so dots show immediately. Merging
    // truncate + placeholder into a single ``setMessages`` avoids a flash
    // of "user message with no assistant" between two state updates.
    const placeholderId = crypto.randomUUID();
    setMessages((prev) => {
      // Drop the trailing assistant message (and any tool-trace rows that
      // followed it) so the user only sees the in-flight regeneration once
      // the new stream begins. Stop at the last user message.
      let cut = prev.length;
      for (let i = prev.length - 1; i >= 0; i--) {
        if (prev[i].role === "user") {
          cut = i + 1;
          break;
        }
      }
      return [
        ...prev.slice(0, cut),
        {
          id: placeholderId,
          role: "assistant",
          content: "",
          isStreaming: true,
          latencyMs: 0,
          createdAt: Date.now(),
        },
      ];
    });
    buffer.current = { messageId: placeholderId, parts: [] };
    setIsStreaming(true);
    startLatency(placeholderId);
    client.regenerate(chatId);
  }, [chatId, client, startLatency, cancelFlush]);

  const editMessage = useCallback(
    (messageId: string, newContent: string) => {
      if (!chatId) return;
      // Cancel any in-flight stream before swapping the buffer; otherwise late
      // delta events for the old turn would leak into the new placeholder bubble.
      if (buffer.current) {
        client.sendStop(chatId);
      }
      cancelFlush();
      const idx = messages.findIndex(
        (m) => m.id === messageId && m.role === "user",
      );
      if (idx < 0) return;
      // Compute the user-only index for the wire envelope.
      const userMsgIndex =
        messages.slice(0, idx + 1).filter((m) => m.role === "user").length - 1;
      const placeholderId = crypto.randomUUID();
      setMessages((prev) => {
        // Defensive findIndex in case messages drifted between callback
        // creation and invocation (e.g. an inbound delta arrived first).
        const targetIdx = prev.findIndex(
          (m) => m.id === messageId && m.role === "user",
        );
        if (targetIdx < 0) return prev;
        return [
          ...prev.slice(0, targetIdx),
          { ...prev[targetIdx], content: newContent },
          {
            id: placeholderId,
            role: "assistant",
            content: "",
            isStreaming: true,
            latencyMs: 0,
            createdAt: Date.now(),
          },
        ];
      });
      buffer.current = { messageId: placeholderId, parts: [] };
      setIsStreaming(true);
      startLatency(placeholderId);
      client.editAndResend(chatId, userMsgIndex, newContent);
    },
    [chatId, client, messages, startLatency, cancelFlush],
  );

  return {
    messages,
    isStreaming,
    send,
    stop,
    regenerate,
    editMessage,
    setMessages,
    streamError,
    dismissStreamError,
  };
}
