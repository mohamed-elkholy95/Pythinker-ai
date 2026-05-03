import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { InChatSearch } from "@/components/InChatSearch";
import { ThreadSearchProvider } from "@/components/ThreadSearchProvider";
import { ThreadComposer } from "@/components/thread/ThreadComposer";
import { ThreadHeader } from "@/components/thread/ThreadHeader";
import { StreamErrorNotice } from "@/components/thread/StreamErrorNotice";
import { ThreadViewport } from "@/components/thread/ThreadViewport";
import { useAttachedImages } from "@/hooks/useAttachedImages";
import { useAvailableModels } from "@/hooks/useAvailableModels";
import { useClipboardAndDrop } from "@/hooks/useClipboardAndDrop";
import { useModelOverride } from "@/hooks/useModelOverride";
import { usePythinkerStream } from "@/hooks/usePythinkerStream";
import { useSessionHistory } from "@/hooks/useSessions";
import type { ChatSummary, UIMessage } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

interface ThreadShellProps {
  session: ChatSummary | null;
  title: string;
  onToggleSidebar: () => void;
  onGoHome: () => void;
  onNewChat: () => Promise<string | null>;
  hideSidebarToggleOnDesktop?: boolean;
  /** Test hook: override the streaming flag instead of the live hook value. */
  isStreaming?: boolean;
  /** Test hook: override the stop callback wired into the active-thread composer. */
  onStop?: () => void;
  /** One-shot scroll target (cross-chat search hit jump-to-message). */
  scrollTarget?: {
    chatKey: string;
    messageIndex: number;
    /** Bumped each time the same target is re-requested so the effect refires. */
    token: number;
  } | null;
}

function toModelBadgeLabel(modelName: string | null): string | null {
  if (!modelName) return null;
  const trimmed = modelName.trim();
  if (!trimmed) return null;
  const leaf = trimmed.split("/").pop() ?? trimmed;
  return leaf || trimmed;
}

export function ThreadShell({
  session,
  title,
  onToggleSidebar,
  onGoHome,
  onNewChat,
  hideSidebarToggleOnDesktop = false,
  isStreaming: isStreamingOverride,
  onStop: onStopOverride,
  scrollTarget,
}: ThreadShellProps) {
  const { t } = useTranslation();
  const chatId = session?.chatId ?? null;
  const historyKey = session?.key ?? null;
  const { messages: historical, loading } = useSessionHistory(historyKey);
  const { client, modelName } = useClient();
  const { models } = useAvailableModels();
  const { override, setOverride } = useModelOverride(
    chatId,
    session?.modelOverride ?? null,
  );
  // Prefer the configured default reported by ``/api/models`` (the source the
  // switcher binds against); fall back to the bootstrap-injected ``modelName``
  // when ``/api/models`` is unavailable so the switcher's "active" indicator
  // still tracks something sensible.
  const defaultModel = models.find((m) => m.is_default)?.name ?? null;
  const currentModel = defaultModel ?? modelName ?? null;
  const [booting, setBooting] = useState(false);
  const pendingFirstRef = useRef<string | null>(null);
  const messageCacheRef = useRef<Map<string, UIMessage[]>>(new Map());

  const initial = useMemo(() => {
    if (!chatId) return historical;
    return messageCacheRef.current.get(chatId) ?? historical;
  }, [chatId, historical]);
  const {
    messages,
    isStreaming: liveIsStreaming,
    send,
    stop: liveStop,
    regenerate,
    editMessage,
    setMessages,
    streamError,
    dismissStreamError,
  } = usePythinkerStream(chatId, initial);
  const isStreaming = isStreamingOverride ?? liveIsStreaming;
  const onStop = onStopOverride ?? liveStop;
  const showHeroComposer = messages.length === 0 && !loading;
  const [searchOpen, setSearchOpen] = useState(false);

  // Staged image state lives here so users can drop files anywhere on the
  // chat surface (including the message viewport, header gutter, etc.) and
  // still feed the same validator → encoder → chip pipeline that the
  // composer's paperclip / paste paths use. The composer renders the chips
  // from the ``attached`` prop rather than owning the hook itself.
  const attached = useAttachedImages();
  const onImageFiles = useCallback(
    (files: File[]) => {
      attached.enqueue(files);
    },
    [attached],
  );
  const drop = useClipboardAndDrop(onImageFiles);

  // Global ⌘F / Ctrl+F: toggle the in-chat search overlay and override the
  // browser's native find-in-page (which would otherwise search the chrome
  // rather than the message stream the user actually cares about).
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      const mod = ev.metaKey || ev.ctrlKey;
      if (mod && ev.key.toLowerCase() === "f") {
        ev.preventDefault();
        setSearchOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!chatId || loading) return;
    const cached = messageCacheRef.current.get(chatId);
    // When the user switches away and back, keep the local in-memory thread
    // state (including not-yet-persisted messages) instead of replacing it with
    // whatever the history endpoint currently knows about.
    setMessages(cached && cached.length > 0 ? cached : historical);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, chatId, historical]);

  useEffect(() => {
    if (chatId) return;
    setMessages(historical);
  }, [chatId, historical, setMessages]);

  useEffect(() => {
    if (!chatId) return;
    messageCacheRef.current.set(chatId, messages);
  }, [chatId, messages]);

  useEffect(() => {
    if (!chatId) return;
    const pending = pendingFirstRef.current;
    if (!pending) return;
    pendingFirstRef.current = null;
    client.sendMessage(chatId, pending);
    setMessages((prev) => [
      ...prev,
      {
        id: crypto.randomUUID(),
        role: "user",
        content: pending,
        createdAt: Date.now(),
      },
    ]);
    setBooting(false);
  }, [chatId, client, setMessages]);

  const handleWelcomeSend = useCallback(
    async (content: string) => {
      if (booting) return;
      setBooting(true);
      pendingFirstRef.current = content;
      const newId = await onNewChat();
      if (!newId) {
        pendingFirstRef.current = null;
        setBooting(false);
      }
    },
    [booting, onNewChat],
  );

  const emptyState = loading ? (
    <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
      {t("thread.loadingConversation")}
    </div>
  ) : (
    <div className="flex w-full max-w-[40rem] flex-col gap-2 text-left animate-in fade-in-0 slide-in-from-bottom-2 duration-500">
      <div className="inline-flex items-center gap-2 text-[11px] font-medium text-muted-foreground">
        <img
          src="/brand/icon.svg"
          alt=""
          aria-hidden
          draggable={false}
          className="h-4 w-4 rounded-sm opacity-90"
        />
        <span className="text-foreground/82">pythinker</span>
      </div>
      <p className="max-w-[28rem] text-[13px] leading-6 text-muted-foreground">
        {t("thread.empty.description")}
      </p>
    </div>
  );

  return (
    <ThreadSearchProvider>
      <section
        className={cn(
          "relative flex min-h-0 flex-1 flex-col overflow-hidden bg-background/40",
          drop.isDragging
            && "ring-2 ring-primary/40 motion-reduce:ring-0 motion-reduce:border-primary",
        )}
        onDragEnter={drop.onDragEnter}
        onDragOver={drop.onDragOver}
        onDragLeave={drop.onDragLeave}
        onDrop={drop.onDrop}
      >
        <ThreadHeader
          title={title}
          onToggleSidebar={onToggleSidebar}
          onGoHome={onGoHome}
          hideSidebarToggleOnDesktop={hideSidebarToggleOnDesktop}
          chatId={chatId}
        />
        <InChatSearch
          open={searchOpen}
          onClose={() => setSearchOpen(false)}
        />
        <ThreadViewport
          messages={messages}
          isStreaming={isStreaming}
          emptyState={emptyState}
          onRegenerate={regenerate}
          onEdit={editMessage}
          scrollTarget={scrollTarget ?? null}
          composer={
            <>
              {streamError ? (
                <StreamErrorNotice
                  error={streamError}
                  onDismiss={dismissStreamError}
                />
              ) : null}
              {session ? (
                <ThreadComposer
                  onSend={send}
                  disabled={!chatId}
                  placeholder={
                    showHeroComposer
                      ? t("thread.composer.placeholderHero")
                      : t("thread.composer.placeholderThread")
                  }
                  modelLabel={toModelBadgeLabel(modelName)}
                  variant={showHeroComposer ? "hero" : "thread"}
                  isStreaming={isStreaming}
                  onStop={onStop}
                  models={models}
                  currentModel={currentModel}
                  override={override}
                  onModelChange={setOverride}
                  attachedImages={attached}
                />
              ) : (
                <ThreadComposer
                  onSend={handleWelcomeSend}
                  disabled={booting}
                  placeholder={
                    booting
                      ? t("thread.composer.placeholderOpening")
                      : t("thread.composer.placeholderHero")
                  }
                  modelLabel={toModelBadgeLabel(modelName)}
                  variant="hero"
                  attachedImages={attached}
                />
              )}
            </>
          }
        />
      </section>
    </ThreadSearchProvider>
  );
}
