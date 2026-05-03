import {
  Activity,
  Bot,
  Bug,
  CalendarClock,
  Database,
  Gauge,
  Globe2,
  LayoutDashboard,
  MessageSquare,
  Moon,
  Palette,
  PanelLeftClose,
  Plus,
  RefreshCcw,
  ScrollText,
  Settings,
  Sparkles,
  Sun,
  Zap,
} from "lucide-react";
import { useCallback, useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { ChatList, type ChatSection } from "@/components/ChatList";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { SidebarSearch } from "@/components/SidebarSearch";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  togglePinSession,
  toggleArchiveSession,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";
import type { AdminTabId } from "@/lib/admin-tabs";
import type { ChatSummary } from "@/lib/types";

interface SidebarProps {
  sessions: ChatSummary[];
  activeKey: string | null;
  loading: boolean;
  theme: "light" | "dark";
  onToggleTheme: () => void;
  onNewChat: () => void;
  /** Open chat ``key``. When ``messageIndex`` is supplied (e.g. cross-chat
   * search hit), the thread should scroll to that message after load. */
  onSelect: (key: string, messageIndex?: number) => void;
  onRefresh: () => void;
  onRequestDelete: (key: string, label: string) => void;
  onCollapse: () => void;
  /** Optional override for the pin toggle — when omitted, Sidebar issues
   * the API call itself and triggers ``onRefresh`` to repopulate. Lets
   * future callers inject an optimistic implementation without breaking
   * the existing call site in ``App.tsx``. */
  onTogglePin?: (key: string) => void | Promise<void>;
  /** Same fallback contract as ``onTogglePin``. */
  onToggleArchive?: (key: string) => void | Promise<void>;
  activeView?: "chat" | "admin";
  adminActiveTab?: AdminTabId;
  onOpenChat?: () => void;
  /** Open the admin console and select a tab (mirrors in-page navigation). */
  onNavigateAdmin?: (tab: AdminTabId) => void;
}

export function Sidebar(props: SidebarProps) {
  const { t } = useTranslation();
  const { token } = useClient();
  const handleSelectHit = ({
    sessionKey,
    messageIndex,
  }: {
    sessionKey: string;
    messageIndex: number;
  }) => {
    props.onSelect(sessionKey, messageIndex);
  };

  // Fall back to a direct API call + refresh when the parent hasn't wired
  // a richer (optimistic) toggle. Keeps the four-file scope honest while
  // still delivering working pin/archive end to end.
  const onTogglePinProp = props.onTogglePin;
  const onToggleArchiveProp = props.onToggleArchive;
  const onRefresh = props.onRefresh;
  const togglePin = useCallback(
    async (key: string) => {
      if (onTogglePinProp) {
        await onTogglePinProp(key);
        return;
      }
      try {
        await togglePinSession(token, key);
      } finally {
        onRefresh();
      }
    },
    [onTogglePinProp, onRefresh, token],
  );
  const toggleArchive = useCallback(
    async (key: string) => {
      if (onToggleArchiveProp) {
        await onToggleArchiveProp(key);
        return;
      }
      try {
        await toggleArchiveSession(token, key);
      } finally {
        onRefresh();
      }
    },
    [onToggleArchiveProp, onRefresh, token],
  );

  const { pinned, recent, archived } = useMemo(() => {
    const p: ChatSummary[] = [];
    const r: ChatSummary[] = [];
    const a: ChatSummary[] = [];
    for (const s of props.sessions) {
      if (s.archived) a.push(s);
      else if (s.pinned) p.push(s);
      else r.push(s);
    }
    return { pinned: p, recent: r, archived: a };
  }, [props.sessions]);

  const sections: ChatSection[] = useMemo(
    () => [
      { id: "pinned", label: t("sidebar.pinned"), items: pinned },
      { id: "recent", label: t("sidebar.recent"), items: recent },
      {
        id: "archived",
        label: t("sidebar.archived"),
        items: archived,
        collapsible: true,
        defaultOpen: false,
      },
    ],
    [pinned, recent, archived, t],
  );

  return (
    <aside className="flex h-full w-full flex-col border-r border-sidebar-border/70 bg-sidebar text-sidebar-foreground">
      <div className="flex items-center justify-between px-3 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <img
            src="/brand/icon.svg"
            alt=""
            className="h-7 w-7 rounded-lg"
            aria-hidden
            draggable={false}
          />
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold tracking-tight">
              Pythinker
            </div>
            <div className="truncate text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
              Agent & control
            </div>
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon"
          aria-label={t("sidebar.collapse")}
          onClick={props.onCollapse}
          className="h-11 w-11 sm:h-7 sm:w-7 rounded-lg text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
        >
          <PanelLeftClose className="h-3.5 w-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          aria-label={t("sidebar.toggleTheme")}
          onClick={props.onToggleTheme}
          className="h-11 w-11 sm:h-7 sm:w-7 rounded-lg text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
        >
          {props.theme === "dark" ? (
            <Sun className="h-3.5 w-3.5" />
          ) : (
            <Moon className="h-3.5 w-3.5" />
          )}
        </Button>
      </div>
      <div className="px-3 pb-3">
        <Button
          onClick={props.onNewChat}
          className="h-9 w-full justify-start gap-2 rounded-xl border border-sidebar-border/80 bg-card/45 px-3 text-[13px] font-medium text-sidebar-foreground shadow-sm hover:bg-sidebar-accent/80"
          variant="outline"
        >
          <Plus className="h-3.5 w-3.5" />
          {t("sidebar.newChat")}
        </Button>
      </div>
      <div
        className={cn(
          "flex min-h-0 flex-1 flex-col overflow-y-auto",
          // Thin sidebar scrollbar: only paints when content overflows;
          // tracks the sidebar background and tints with the muted color so
          // it never fights the chat content for attention.
          "scrollbar-thin",
          "[&::-webkit-scrollbar]:w-1.5",
          "[&::-webkit-scrollbar-thumb]:rounded-full",
          "[&::-webkit-scrollbar-thumb]:bg-muted-foreground/25",
          "[&::-webkit-scrollbar-thumb]:hover:bg-muted-foreground/45",
          "[&::-webkit-scrollbar-track]:bg-transparent",
        )}
      >
      {props.onNavigateAdmin ? (
        <div className="space-y-3 px-3 pb-3">
          <NavGroup label="Chat">
            <NavButton
              active={props.activeView !== "admin"}
              icon={<MessageSquare className="h-3.5 w-3.5" />}
              label="Conversations"
              onClick={props.onOpenChat}
            />
          </NavGroup>
          <NavGroup label="Monitor">
            <AdminNavButton
              tab="overview"
              icon={<LayoutDashboard className="h-3.5 w-3.5" />}
              label="Overview"
              {...props}
            />
            <AdminNavButton
              tab="usage"
              icon={<Gauge className="h-3.5 w-3.5" />}
              label="Usage"
              {...props}
            />
            <AdminNavButton
              tab="logs"
              icon={<ScrollText className="h-3.5 w-3.5" />}
              label="Logs"
              {...props}
            />
          </NavGroup>
          <NavGroup label="Workspace">
            <AdminNavButton
              tab="channels"
              icon={<Globe2 className="h-3.5 w-3.5" />}
              label="Channels"
              {...props}
            />
            <AdminNavButton
              tab="sessions"
              icon={<Database className="h-3.5 w-3.5" />}
              label="Sessions"
              {...props}
            />
            <AdminNavButton
              tab="agents"
              icon={<Bot className="h-3.5 w-3.5" />}
              label="Agents"
              {...props}
            />
            <AdminNavButton
              tab="skills"
              icon={<Zap className="h-3.5 w-3.5" />}
              label="Skills"
              {...props}
            />
            <AdminNavButton
              tab="dreams"
              icon={<Sparkles className="h-3.5 w-3.5" />}
              label="Dreams"
              {...props}
            />
            <AdminNavButton
              tab="cron"
              icon={<CalendarClock className="h-3.5 w-3.5" />}
              label="Cron"
              {...props}
            />
          </NavGroup>
          <NavGroup label="System">
            <AdminNavButton
              tab="config"
              icon={<Settings className="h-3.5 w-3.5" />}
              label="Config"
              {...props}
            />
            <AdminNavButton
              tab="appearance"
              icon={<Palette className="h-3.5 w-3.5" />}
              label="Appearance"
              {...props}
            />
            <AdminNavButton
              tab="infrastructure"
              icon={<Activity className="h-3.5 w-3.5" />}
              label="Infrastructure"
              {...props}
            />
            <AdminNavButton
              tab="debug"
              icon={<Bug className="h-3.5 w-3.5" />}
              label="Debug"
              {...props}
            />
          </NavGroup>
        </div>
      ) : null}
      <SidebarSearch onSelectHit={handleSelectHit}>
        <Separator className="bg-sidebar-border/70" />
        <div className="flex items-center justify-between px-3 py-2">
          <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            Recent
          </span>
          <Button
            variant="ghost"
            size="icon"
            className="h-11 w-11 sm:h-6 sm:w-6 rounded-md text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
            onClick={props.onRefresh}
            aria-label={t("sidebar.refreshSessions")}
          >
            <RefreshCcw className="h-3.5 w-3.5" />
          </Button>
        </div>
        <div className="flex-1">
          <ChatList
            sections={sections}
            activeKey={props.activeKey}
            loading={props.loading}
            onSelect={props.onSelect}
            onRequestDelete={props.onRequestDelete}
            onTogglePin={(key) => {
              void togglePin(key);
            }}
            onToggleArchive={(key) => {
              void toggleArchive(key);
            }}
          />
        </div>
      </SidebarSearch>
      </div>
      <Separator className="bg-sidebar-border/70" />
      <div className="flex items-center justify-between gap-2 px-2.5 py-2 text-xs">
        <ConnectionBadge />
        <LanguageSwitcher />
      </div>
    </aside>
  );
}

function NavGroup({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1">
      <div className="px-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
        {label}
      </div>
      {children}
    </div>
  );
}

function NavButton({
  active,
  icon,
  label,
  onClick,
}: {
  active: boolean;
  icon: ReactNode;
  label: string;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex h-8 w-full items-center gap-2 rounded-xl px-2.5 text-left text-xs transition-all",
        "text-muted-foreground hover:bg-sidebar-accent/80 hover:text-sidebar-foreground",
        active
          && "bg-primary/[0.12] text-sidebar-foreground shadow-[inset_0_0_0_1px_hsl(var(--primary)/0.28)]",
      )}
    >
      {icon}
      <span className="truncate">{label}</span>
    </button>
  );
}

function AdminNavButton({
  tab,
  icon,
  label,
  activeView,
  adminActiveTab,
  onNavigateAdmin,
}: {
  tab: AdminTabId;
  icon: ReactNode;
  label: string;
} & Pick<SidebarProps, "activeView" | "adminActiveTab" | "onNavigateAdmin">) {
  return (
    <NavButton
      active={activeView === "admin" && adminActiveTab === tab}
      icon={icon}
      label={label}
      onClick={() => onNavigateAdmin?.(tab)}
    />
  );
}
