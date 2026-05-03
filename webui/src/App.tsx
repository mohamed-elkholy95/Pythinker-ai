import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { DeleteConfirm } from "@/components/DeleteConfirm";
import { HotkeyHelpDialog } from "@/components/HotkeyHelpDialog";
import { Sidebar } from "@/components/Sidebar";
import { AdminDashboard } from "@/components/admin/AdminDashboard";
import { ThreadShell } from "@/components/thread/ThreadShell";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { preloadMarkdownText } from "@/components/MarkdownText";
import { useHotkey } from "@/hooks/useHotkey";
import { useSessions } from "@/hooks/useSessions";
import { useTheme } from "@/hooks/useTheme";
import { cn } from "@/lib/utils";
import type { AdminTabId } from "@/lib/admin-tabs";
import { deriveWsUrl, fetchBootstrap } from "@/lib/bootstrap";
import { cleanChatTitle } from "@/lib/chatTitle";
import { PythinkerClient } from "@/lib/pythinker-client";
import { ClientProvider } from "@/providers/ClientProvider";
import type { ChatSummary } from "@/lib/types";

type BootState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | {
      status: "ready";
      client: PythinkerClient;
      token: string;
      modelName: string | null;
      voiceEnabled: boolean;
    };

const SIDEBAR_STORAGE_KEY = "pythinker-webui.sidebar";
// Desktop sidebar width: 288px on lg, 312px on xl+. Slightly more
// breathing room for nav labels + chat titles without crowding the
// thread. Mobile sheet keeps its own width below.
const SIDEBAR_WIDTH = 288;
const SIDEBAR_WIDTH_XL = 312;

function readSidebarOpen(): boolean {
  if (typeof window === "undefined") return true;
  try {
    const raw = window.localStorage.getItem(SIDEBAR_STORAGE_KEY);
    if (raw === null) return true;
    return raw === "1";
  } catch {
    return true;
  }
}

export default function App() {
  const { t } = useTranslation();
  const [state, setState] = useState<BootState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const boot = await fetchBootstrap();
        if (cancelled) return;
        const url = deriveWsUrl(boot.ws_path, boot.token);
        const client = new PythinkerClient({
          url,
          onReauth: async () => {
            try {
              const refreshed = await fetchBootstrap();
              return deriveWsUrl(refreshed.ws_path, refreshed.token);
            } catch {
              return null;
            }
          },
        });
        client.connect();
        setState({
          status: "ready",
          client,
          token: boot.token,
          modelName: boot.model_name ?? null,
          voiceEnabled: boot.voice_enabled ?? false,
        });
      } catch (e) {
        if (cancelled) return;
        setState({ status: "error", message: (e as Error).message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const warm = () => preloadMarkdownText();
    const win = globalThis as typeof globalThis & {
      requestIdleCallback?: (
        callback: IdleRequestCallback,
        options?: IdleRequestOptions,
      ) => number;
      cancelIdleCallback?: (handle: number) => void;
    };
    if (typeof win.requestIdleCallback === "function") {
      const id = win.requestIdleCallback(warm, { timeout: 1500 });
      return () => win.cancelIdleCallback?.(id);
    }
    const id = globalThis.setTimeout(warm, 250);
    return () => globalThis.clearTimeout(id);
  }, []);

  if (state.status === "loading") {
    return (
      <div className="flex h-full w-full items-center justify-center">
        <div className="flex flex-col items-center gap-3 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-300">
          <img
            src="/brand/icon.svg"
            alt=""
            className="h-10 w-10 motion-safe:animate-pulse select-none"
            aria-hidden
            draggable={false}
          />
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full motion-safe:animate-ping rounded-full bg-foreground/40" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-foreground/60" />
            </span>
            {t("app.loading.connecting")}
          </div>
        </div>
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <div className="flex h-full w-full items-center justify-center px-4 text-center">
        <div className="flex max-w-md flex-col items-center gap-3">
          <img
            src="/brand/icon.svg"
            alt=""
            className="h-10 w-10 opacity-60 grayscale select-none"
            aria-hidden
            draggable={false}
          />
          <p className="text-lg font-semibold">{t("app.error.title")}</p>
          <p className="text-sm text-muted-foreground">{state.message}</p>
          <p className="text-xs text-muted-foreground">
            {t("app.error.gatewayHint")}
          </p>
        </div>
      </div>
    );
  }

  return (
    <ClientProvider
      client={state.client}
      token={state.token}
      modelName={state.modelName}
      voiceEnabled={state.voiceEnabled}
    >
      <Shell />
    </ClientProvider>
  );
}

function Shell() {
  const { t, i18n } = useTranslation();
  const { theme, toggle } = useTheme();
  const {
    sessions,
    loading,
    refresh,
    createChat,
    deleteChat,
    togglePin,
    toggleArchive,
  } = useSessions();
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<"chat" | "admin">("chat");
  const [adminTab, setAdminTab] = useState<AdminTabId>("overview");
  // One-shot scroll target carried from cross-chat search hits. The
  // monotonically-bumped ``token`` lets MessageList re-fire its effect even
  // when the user clicks the same hit twice. ThreadShell forwards both down.
  const [scrollTarget, setScrollTarget] = useState<{
    chatKey: string;
    messageIndex: number;
    token: number;
  } | null>(null);
  const [desktopSidebarOpen, setDesktopSidebarOpen] =
    useState<boolean>(readSidebarOpen);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<{
    key: string;
    label: string;
  } | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const lastSessionsLen = useRef(0);

  useEffect(() => {
    try {
      window.localStorage.setItem(
        SIDEBAR_STORAGE_KEY,
        desktopSidebarOpen ? "1" : "0",
      );
    } catch {
      // ignore storage errors (private mode, etc.)
    }
  }, [desktopSidebarOpen]);

  useEffect(() => {
    if (activeKey) return;
    if (sessions.length > 0 && lastSessionsLen.current === 0) {
      setActiveKey(sessions[0].key);
    }
    lastSessionsLen.current = sessions.length;
  }, [sessions, activeKey]);

  const activeSession = useMemo<ChatSummary | null>(() => {
    if (!activeKey) return null;
    return sessions.find((s) => s.key === activeKey) ?? null;
  }, [sessions, activeKey]);

  const closeDesktopSidebar = useCallback(() => {
    setDesktopSidebarOpen(false);
  }, []);

  const closeMobileSidebar = useCallback(() => {
    setMobileSidebarOpen(false);
  }, []);

  const toggleSidebar = useCallback(() => {
    const isDesktop =
      typeof window !== "undefined" &&
      window.matchMedia("(min-width: 1024px)").matches;
    if (isDesktop) {
      setDesktopSidebarOpen((v) => !v);
    } else {
      setMobileSidebarOpen((v) => !v);
    }
  }, []);

  const onNewChat = useCallback(async () => {
    try {
      const chatId = await createChat();
      setActiveView("chat");
      setActiveKey(`websocket:${chatId}`);
      setMobileSidebarOpen(false);
      return chatId;
    } catch (e) {
      console.error("Failed to create chat", e);
      return null;
    }
  }, [createChat]);

  const onSelectChat = useCallback(
    (key: string, messageIndex?: number) => {
      setActiveView("chat");
      setActiveKey(key);
      setMobileSidebarOpen(false);
      if (typeof messageIndex === "number") {
        setScrollTarget((prev) => ({
          chatKey: key,
          messageIndex,
          token: (prev?.token ?? 0) + 1,
        }));
      }
    },
    [],
  );

  const onConfirmDelete = useCallback(async () => {
    if (!pendingDelete) return;
    const key = pendingDelete.key;
    const deletingActive = activeKey === key;
    const currentIndex = sessions.findIndex((s) => s.key === key);
    const fallbackKey = deletingActive
      ? (sessions[currentIndex + 1]?.key ?? sessions[currentIndex - 1]?.key ?? null)
      : activeKey;
    setPendingDelete(null);
    if (deletingActive) setActiveKey(fallbackKey);
    try {
      await deleteChat(key);
    } catch (e) {
      if (deletingActive) setActiveKey(key);
      console.error("Failed to delete session", e);
    }
  }, [pendingDelete, deleteChat, activeKey, sessions]);

  // Global keyboard shortcuts. The `?` and `mod+/` bindings dispatch
  // CustomEvents so future overlays can subscribe without coupling App.tsx
  // to ThreadShell's stream controls or InChatSearch state.
  useHotkey("mod+k", (e) => {
    e.preventDefault();
    void onNewChat();
  });

  useHotkey("mod+/", (e) => {
    e.preventDefault();
    window.dispatchEvent(new CustomEvent("pythinker:toggle-search"));
  });

  useHotkey("mod+up", (e) => {
    e.preventDefault();
    if (sessions.length === 0) return;
    const idx = activeKey
      ? sessions.findIndex((s) => s.key === activeKey)
      : 0;
    const safeIdx = idx < 0 ? 0 : idx;
    const next = sessions[(safeIdx - 1 + sessions.length) % sessions.length];
    setActiveKey(next.key);
  });

  useHotkey("mod+down", (e) => {
    e.preventDefault();
    if (sessions.length === 0) return;
    const idx = activeKey
      ? sessions.findIndex((s) => s.key === activeKey)
      : -1;
    const safeIdx = idx < 0 ? -1 : idx;
    const next = sessions[(safeIdx + 1) % sessions.length];
    setActiveKey(next.key);
  });

  useHotkey("?", (e) => {
    e.preventDefault();
    setHelpOpen((v) => !v);
  });

  useHotkey("esc", () => {
    window.dispatchEvent(new CustomEvent("pythinker:stop"));
  });

  const headerTitle = activeSession
    ? cleanChatTitle(activeSession.title) ||
      cleanChatTitle(activeSession.preview) ||
      t("chat.fallbackTitle")
    : t("app.brand");

  useEffect(() => {
    document.title = activeSession
      ? t("app.documentTitle.chat", { title: headerTitle })
      : t("app.documentTitle.base");
  }, [activeSession, headerTitle, i18n.resolvedLanguage, t]);

  const sidebarProps = {
    sessions,
    activeKey,
    loading,
    theme,
    onToggleTheme: toggle,
    onNewChat: () => {
      void onNewChat();
    },
    onSelect: onSelectChat,
    onRefresh: () => void refresh(),
    onRequestDelete: (key: string, label: string) =>
      setPendingDelete({ key, label }),
    onTogglePin: (key: string) => void togglePin(key),
    onToggleArchive: (key: string) => void toggleArchive(key),
    activeView,
    adminActiveTab: adminTab,
    onOpenChat: () => setActiveView("chat"),
    onNavigateAdmin: (tab: AdminTabId) => {
      setActiveView("admin");
      setAdminTab(tab);
      setMobileSidebarOpen(false);
    },
  };

  return (
    <div className="control-grid-bg relative flex h-full w-full overflow-hidden bg-background">
      {/* Desktop sidebar: in normal flow, so the thread area width stays honest. */}
      <aside
        className={cn(
          "relative z-20 hidden shrink-0 overflow-hidden lg:block",
          "transition-[width] duration-300 ease-out",
        )}
        style={{
          width: desktopSidebarOpen
            ? `clamp(${SIDEBAR_WIDTH}px, 22vw, ${SIDEBAR_WIDTH_XL}px)`
            : 0,
        }}
      >
        <div
          className={cn(
            "absolute inset-y-0 left-0 h-full w-full overflow-hidden bg-sidebar shadow-inner-right",
            "transition-transform duration-300 ease-out",
            desktopSidebarOpen ? "translate-x-0" : "-translate-x-full",
          )}
        >
          <Sidebar {...sidebarProps} onCollapse={closeDesktopSidebar} />
        </div>
      </aside>

      <Sheet
        open={mobileSidebarOpen}
        onOpenChange={(open) => setMobileSidebarOpen(open)}
      >
        <SheetContent
          side="left"
          showCloseButton={false}
          className="w-[88vw] max-w-[320px] p-0 sm:max-w-[320px] lg:hidden"
        >
          <Sidebar {...sidebarProps} onCollapse={closeMobileSidebar} />
        </SheetContent>
      </Sheet>

      <main className="flex h-full min-w-0 flex-1 flex-col">
        {activeView === "admin" ? (
          <AdminDashboard
            activeTab={adminTab}
            onActiveTabChange={setAdminTab}
            onToggleSidebar={toggleSidebar}
            hideSidebarToggleOnDesktop={desktopSidebarOpen}
          />
        ) : (
          <ThreadShell
            session={activeSession}
            title={headerTitle}
            onToggleSidebar={toggleSidebar}
            onGoHome={() => setActiveKey(null)}
            onNewChat={onNewChat}
            hideSidebarToggleOnDesktop={desktopSidebarOpen}
            scrollTarget={
              scrollTarget && scrollTarget.chatKey === activeKey
                ? scrollTarget
                : null
            }
          />
        )}
      </main>

      <DeleteConfirm
        open={!!pendingDelete}
        title={pendingDelete?.label ?? ""}
        onCancel={() => setPendingDelete(null)}
        onConfirm={onConfirmDelete}
      />

      <HotkeyHelpDialog open={helpOpen} onOpenChange={setHelpOpen} />
    </div>
  );
}
