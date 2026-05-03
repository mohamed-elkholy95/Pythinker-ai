import {
  Activity,
  Bot,
  Bug,
  CalendarClock,
  Database,
  FileText,
  Gauge,
  Globe2,
  LayoutDashboard,
  Moon,
  Palette,
  PanelLeftOpen,
  RefreshCcw,
  ScrollText,
  Settings,
  ShieldAlert,
  Sparkles,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import {
  fetchAdminSurfaces,
  type AdminLiveSession,
  type AdminSubagentStatus,
  type AdminSurfaces,
} from "@/lib/admin-api";
import { adminTabMeta, type AdminTabId } from "@/lib/admin-tabs";
import { UsageView } from "@/components/admin/UsageView";
import { ConfigWorkbench } from "@/components/admin/config/ConfigWorkbench";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

export type { AdminTabId } from "@/lib/admin-tabs";

type AdminState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; surfaces: AdminSurfaces };

const TAB_GROUPS: Array<{
  label: string;
  tabs: Array<{ id: AdminTabId; label: string; icon: ReactNode }>;
}> = [
  {
    label: "Monitor",
    tabs: [
      { id: "overview", label: "Overview", icon: <LayoutDashboard className="h-3.5 w-3.5" /> },
      { id: "usage", label: "Usage", icon: <Gauge className="h-3.5 w-3.5" /> },
      { id: "logs", label: "Logs", icon: <ScrollText className="h-3.5 w-3.5" /> },
    ],
  },
  {
    label: "Workspace",
    tabs: [
      { id: "channels", label: "Channels", icon: <Globe2 className="h-3.5 w-3.5" /> },
      { id: "sessions", label: "Sessions", icon: <Database className="h-3.5 w-3.5" /> },
      { id: "agents", label: "Agents", icon: <Bot className="h-3.5 w-3.5" /> },
      { id: "skills", label: "Skills", icon: <Zap className="h-3.5 w-3.5" /> },
      { id: "dreams", label: "Dreams", icon: <Moon className="h-3.5 w-3.5" /> },
      { id: "cron", label: "Cron", icon: <CalendarClock className="h-3.5 w-3.5" /> },
    ],
  },
  {
    label: "System",
    tabs: [
      { id: "config", label: "Config", icon: <Settings className="h-3.5 w-3.5" /> },
      { id: "appearance", label: "Appearance", icon: <Palette className="h-3.5 w-3.5" /> },
      { id: "infrastructure", label: "Infrastructure", icon: <Activity className="h-3.5 w-3.5" /> },
      { id: "debug", label: "Debug", icon: <Bug className="h-3.5 w-3.5" /> },
    ],
  },
];

function formatNumber(value?: number): string {
  return new Intl.NumberFormat().format(value ?? 0);
}

function stringify(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

export interface AdminDashboardProps {
  /** When set, tab selection is controlled by the shell (e.g. sidebar shortcuts). */
  activeTab?: AdminTabId;
  onActiveTabChange?: (tab: AdminTabId) => void;
  onToggleSidebar?: () => void;
  hideSidebarToggleOnDesktop?: boolean;
}

export function AdminDashboard({
  activeTab: activeTabProp,
  onActiveTabChange,
  onToggleSidebar,
  hideSidebarToggleOnDesktop = false,
}: AdminDashboardProps) {
  const { t } = useTranslation();
  const { token } = useClient();
  const [state, setState] = useState<AdminState>({ status: "loading" });
  // Tab is always controlled by the shell (sidebar). Fall back to "overview"
  // only if a caller forgets to pass the prop.
  const activeTab: AdminTabId = activeTabProp ?? "overview";
  void onActiveTabChange;
  const refresh = useCallback(async () => {
    try {
      const surfaces = await fetchAdminSurfaces(token);
      setState({ status: "ready", surfaces });
    } catch (e) {
      setState({ status: "error", message: (e as Error).message });
    }
  }, [token]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const activeMeta = adminTabMeta(activeTab);
  const activeIcon = TAB_GROUPS.flatMap((g) => g.tabs).find((t) => t.id === activeTab)?.icon;
  return (
    <section className="relative flex min-h-0 flex-1 flex-col overflow-hidden bg-background/40">
      <AdminHeader
        groupLabel={activeMeta.group}
        title={activeMeta.label}
        icon={activeIcon}
        onRefresh={() => void refresh()}
        onToggleSidebar={onToggleSidebar}
        hideSidebarToggleOnDesktop={hideSidebarToggleOnDesktop}
        toggleSidebarLabel={t("thread.header.toggleSidebar")}
      />
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        <div className="flex w-full flex-col gap-5 px-4 py-5 lg:px-6 2xl:px-8">
          {state.status === "loading" ? (
            <div className="rounded-xl border border-border/70 p-6 text-sm text-muted-foreground">
              Loading admin data...
            </div>
          ) : state.status === "error" ? (
            <div className="rounded-xl border border-destructive/40 bg-destructive/10 p-6 text-sm">
              {state.message}
            </div>
          ) : (
            <main className="min-w-0">
              <TabContent
                tab={activeTab}
                data={state.surfaces}
                token={token}
                onRefresh={refresh}
              />
            </main>
          )}
        </div>
      </div>
    </section>
  );
}

function AdminHeader({
  groupLabel,
  title,
  icon,
  onRefresh,
  onToggleSidebar,
  hideSidebarToggleOnDesktop,
  toggleSidebarLabel,
}: {
  groupLabel: string;
  title: string;
  icon?: ReactNode;
  onRefresh: () => void;
  onToggleSidebar?: () => void;
  hideSidebarToggleOnDesktop: boolean;
  toggleSidebarLabel: string;
}) {
  return (
    <header className="control-topbar relative z-10 flex min-h-[58px] items-center justify-between gap-3 px-4 py-2">
      <div className="flex min-w-0 items-center gap-3">
        {onToggleSidebar ? (
          <Button
            variant="ghost"
            size="icon"
            aria-label={toggleSidebarLabel}
            onClick={onToggleSidebar}
            className={cn(
              "h-9 w-9 shrink-0 rounded-full border border-border/70 bg-card/65 text-muted-foreground shadow-sm hover:bg-accent/50 hover:text-foreground",
              hideSidebarToggleOnDesktop && "lg:hidden",
            )}
          >
            <PanelLeftOpen className="h-3.5 w-3.5" />
          </Button>
        ) : null}
        <div className="flex min-w-0 items-center gap-2">
          <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            {groupLabel}
          </span>
          <span className="text-muted-foreground/60">/</span>
          <div className="flex min-w-0 items-center gap-1.5 text-sm font-semibold text-foreground">
            {icon}
            <span className="truncate">{title}</span>
          </div>
        </div>
      </div>
      <Button
        type="button"
        onClick={onRefresh}
        variant="outline"
        size="sm"
        className="shrink-0 gap-2"
      >
        <RefreshCcw className="h-4 w-4" />
        Refresh
      </Button>
    </header>
  );
}

function TabContent({
  tab,
  data,
  token,
  onRefresh,
}: {
  tab: AdminTabId;
  data: AdminSurfaces;
  token: string;
  onRefresh: () => Promise<void>;
}) {
  switch (tab) {
    case "channels":
      return <ChannelsView data={data} />;
    case "sessions":
      return <SessionsView data={data} />;
    case "usage":
      return <UsageView data={data} />;
    case "cron":
      return <CronView data={data} />;
    case "agents":
      return <AgentsView data={data} />;
    case "skills":
      return <SkillsView data={data} />;
    case "dreams":
      return <DreamsView data={data} />;
    case "config":
      return (
        <ConfigView
          data={data}
          token={token}
          onRefresh={onRefresh}
        />
      );
    case "appearance":
      return (
        <Panel title="Appearance" icon={<Palette className="h-4 w-4" />}>
          <KeyValues rows={flattenRecord(data.appearance)} />
        </Panel>
      );
    case "infrastructure":
      return <InfrastructureView data={data} />;
    case "debug":
      return (
        <Panel title="Debug" icon={<Bug className="h-4 w-4" />}>
          <KeyValues rows={flattenRecord(data.debug)} />
        </Panel>
      );
    case "logs":
      return <LogsView data={data} />;
    case "overview":
    default:
      return <OverviewView data={data} />;
  }
}

function OverviewView({ data }: { data: AdminSurfaces }) {
  return (
    <div className="space-y-4">
      <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Workspace" value={data.overview.workspace} />
        <MetricCard label="Gateway" value={`${data.overview.gateway.host}:${data.overview.gateway.port}`} />
        <MetricCard label="Active model" value={data.overview.agent.model} />
        <MetricCard label="Last turn tokens" value={formatNumber(data.usage.last_turn.total_tokens)} />
      </section>
      <section className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <Panel title="Runtime" icon={<Activity className="h-4 w-4" />}>
          <KeyValues
            rows={[
              ["Version", data.overview.version],
              ["Config", data.overview.config_path],
              ["Provider", data.overview.agent.provider],
              ["WebSocket", `${data.overview.websocket.host}:${data.overview.websocket.port}${data.overview.websocket.path}`],
            ]}
          />
        </Panel>
        <Panel title="Attention" icon={<ShieldAlert className="h-4 w-4" />}>
          <p className="text-sm text-muted-foreground">
            This console uses the embedded WebUI token and is intended for trusted
            local operation. Put remote deployments behind real authentication.
          </p>
          <div className="mt-3 grid gap-2 sm:grid-cols-3">
            <MetricCard label="Channels" value={`${data.channels.running}/${data.channels.total} running`} />
            <MetricCard label="Skills" value={`${data.skills.total}`} />
            <MetricCard label="Cron jobs" value={formatNumber(Number(data.cron.status.jobs ?? 0))} />
          </div>
        </Panel>
      </section>
      <section className="grid gap-4 xl:grid-cols-[1fr_1fr]">
        <Panel title="Recent Sessions" icon={<Database className="h-4 w-4" />}>
          <Table
            columns={["Session", "Channel", "Usage"]}
            rows={data.sessions.sessions.slice(0, 5).map((session) => [
              session.key,
              session.channel,
              `${formatNumber(session.usage.used)} / ${formatNumber(session.usage.limit)}`,
            ])}
          />
        </Panel>
        <Panel title="Config Secrets" icon={<Settings className="h-4 w-4" />}>
          <div className="max-h-52 overflow-auto rounded-lg border border-border/60 text-xs">
            {data.config.secret_paths.map((path) => (
              <div key={path} className="border-b border-border/50 p-2 last:border-0">
                {path}
              </div>
            ))}
          </div>
        </Panel>
      </section>
    </div>
  );
}

function ChannelsView({ data }: { data: AdminSurfaces }) {
  return (
    <Panel title="Channel Health" icon={<Globe2 className="h-4 w-4" />}>
      <Table
        columns={["Channel", "Enabled", "Running", "Streaming"]}
        rows={data.channels.rows.map((row) => [
          row.name,
          stringify(row.enabled),
          stringify(row.running),
          stringify(row.streaming),
        ])}
      />
    </Panel>
  );
}

function SessionsView({ data }: { data: AdminSurfaces }) {
  return (
    <Panel title="Sessions" icon={<Database className="h-4 w-4" />}>
      <Table
        columns={["Session", "Channel", "Preview", "Usage"]}
        rows={data.sessions.sessions.map((session) => [
          session.key,
          session.channel,
          session.preview || session.title || "No preview",
          `${formatNumber(session.usage.used)} / ${formatNumber(session.usage.limit)}`,
        ])}
      />
    </Panel>
  );
}

function CronView({ data }: { data: AdminSurfaces }) {
  return (
    <Panel title="Cron Automation" icon={<CalendarClock className="h-4 w-4" />}>
      <KeyValues
        rows={[
          ["Enabled", stringify(data.cron.status.enabled)],
          ["Jobs", stringify(data.cron.status.jobs)],
          ["Next wake", stringify(data.cron.status.next_wake_at_ms)],
        ]}
      />
      <div className="mt-4">
        <Table
          columns={["Job", "ID", "Enabled", "Next run"]}
          rows={data.cron.jobs.map((job) => [
            stringify(job.name),
            stringify(job.id),
            stringify(job.enabled),
            stringify((job.state as Record<string, unknown> | undefined)?.next_run_at_ms),
          ])}
        />
      </div>
    </Panel>
  );
}

function AgentsView({ data }: { data: AdminSurfaces }) {
  const liveSessions = data.agents.live?.sessions ?? [];
  return (
    <div className="space-y-4">
      <Panel title="Agents" icon={<Bot className="h-4 w-4" />}>
        <KeyValues
          rows={[
            ["Default", data.agents.default_agent_id],
            ["Policy", data.agents.policy_enabled ? "enabled" : "disabled"],
            ["Manifests", data.agents.manifests_dir ?? "not configured"],
            ["Total", formatNumber(data.agents.total)],
          ]}
        />
        <div className="mt-4">
          <Table
            columns={["Agent", "ID", "Lifecycle", "Model"]}
            rows={data.agents.agents.map((agent) => [
              stringify(agent.name),
              stringify(agent.id),
              stringify(agent.lifecycle),
              stringify(agent.model),
            ])}
          />
        </div>
      </Panel>
      <LiveAgentsPanel sessions={liveSessions} />
    </div>
  );
}

function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}m ${String(rem).padStart(2, "0")}s`;
}

function formatStartedIso(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString();
}

function summarizePhases(rows: AdminSubagentStatus[]): string {
  if (rows.length === 0) return "—";
  const counts = new Map<string, number>();
  for (const r of rows) {
    counts.set(r.phase, (counts.get(r.phase) ?? 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([phase, n]) => `${n} ${phase}`)
    .join(", ");
}

function LiveAgentsPanel({ sessions }: { sessions: AdminLiveSession[] }) {
  return (
    <Panel title="Live agents" icon={<Activity className="h-4 w-4" />}>
      {sessions.length === 0 ? (
        <p className="text-sm text-muted-foreground">No active agents.</p>
      ) : (
        <div className="space-y-3">
          {sessions.map((session) => (
            <div
              key={session.key}
              className="rounded-lg border border-border/60 bg-background/55 p-3"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate font-mono text-sm font-semibold">
                    {session.key}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {session.in_flight} in flight · {session.subagent_count} subagent
                    {session.subagent_count === 1 ? "" : "s"} ·{" "}
                    {summarizePhases(session.subagents)}
                  </div>
                </div>
                <div className="text-xs text-muted-foreground">
                  Latest:{" "}
                  {formatStartedIso(
                    session.subagents
                      .map((s) => s.started_at_iso)
                      .filter(Boolean)
                      .sort()
                      .at(-1) ?? undefined,
                  )}
                </div>
              </div>
              {session.subagents.length > 0 && (
                <div className="mt-2">
                  <Table
                    columns={["Label", "Phase", "Iter.", "Elapsed", "Last tool"]}
                    rows={session.subagents.map((sub) => [
                      sub.label,
                      sub.phase,
                      String(sub.iteration),
                      formatElapsed(sub.elapsed_s),
                      stringify(sub.tool_events.at(-1)?.name) || "—",
                    ])}
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function SkillsView({ data }: { data: AdminSurfaces }) {
  return (
    <Panel title="Skills" icon={<Zap className="h-4 w-4" />}>
      <Table
        columns={["Skill", "Source", "Always", "Disabled"]}
        rows={data.skills.rows.map((skill) => [
          skill.name,
          stringify(skill.source),
          stringify(skill.always),
          stringify(skill.disabled),
        ])}
      />
    </Panel>
  );
}

function DreamsView({ data }: { data: AdminSurfaces }) {
  return (
    <Panel title="Dreams" icon={<Sparkles className="h-4 w-4" />}>
      <KeyValues rows={flattenRecord(data.dreams)} />
    </Panel>
  );
}

function ConfigView({
  data,
  token,
  onRefresh,
}: {
  data: AdminSurfaces;
  token: string;
  onRefresh: () => Promise<void>;
}) {
  return <ConfigWorkbench token={token} surfaces={data} onRefresh={onRefresh} />;
}

function InfrastructureView({ data }: { data: AdminSurfaces }) {
  return (
    <Panel title="Infrastructure" icon={<Activity className="h-4 w-4" />}>
      <KeyValues rows={flattenRecord(data.infrastructure)} />
    </Panel>
  );
}

function flattenRecord(value: Record<string, unknown>, prefix = ""): Array<[string, string]> {
  const rows: Array<[string, string]> = [];
  for (const [key, raw] of Object.entries(value)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (raw === null || raw === undefined) {
      rows.push([path, "—"]);
    } else if (Array.isArray(raw)) {
      rows.push([path, raw.length === 0 ? "[]" : JSON.stringify(raw)]);
    } else if (typeof raw === "object") {
      rows.push(...flattenRecord(raw as Record<string, unknown>, path));
    } else {
      rows.push([path, stringify(raw)]);
    }
  }
  return rows;
}

function LogsView({ data }: { data: AdminSurfaces }) {
  return (
    <Panel title="Log Feed" icon={<FileText className="h-4 w-4" />}>
      <div className="max-h-[32rem] overflow-auto rounded-lg border border-border/60">
        {data.logs.entries.length === 0 ? (
          <div className="p-4 text-sm text-muted-foreground">No log entries found.</div>
        ) : (
          data.logs.entries.map((entry, index) => (
            <div key={index} className="grid gap-1 border-b border-border/50 p-3 text-xs last:border-0">
              <div className="flex items-center gap-2">
                <span className="rounded-full bg-muted px-2 py-0.5 uppercase text-muted-foreground">
                  {entry.level ?? "log"}
                </span>
                <span className="truncate font-mono">{entry.message ?? ""}</span>
              </div>
            </div>
          ))
        )}
      </div>
    </Panel>
  );
}

function Panel({
  title,
  icon,
  children,
}: {
  title: string;
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="control-glass rounded-2xl p-4">
      <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold uppercase tracking-[0.16em] text-muted-foreground">
        {icon}
        {title}
      </h2>
      {children}
    </section>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-xl border border-border/60 bg-background/55 p-3 shadow-sm">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 truncate text-sm font-semibold">{value}</div>
    </div>
  );
}

function KeyValues({ rows }: { rows: Array<[string, string]> }) {
  return (
    <dl className="grid gap-2 text-sm md:grid-cols-2">
      {rows.map(([label, value]) => (
        <div key={label} className="grid gap-1 rounded-lg border border-border/50 p-2">
          <dt className="text-xs text-muted-foreground">{label}</dt>
          <dd className="break-all font-medium">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Table({ columns, rows }: { columns: string[]; rows: ReactNode[][] }) {
  return (
    <div className="overflow-auto rounded-lg border border-border/60">
      <table className="min-w-full text-left text-xs">
        <thead className="bg-muted/60 text-muted-foreground">
          <tr>
            {columns.map((column) => (
              <th key={column} className="px-3 py-2 font-semibold uppercase tracking-[0.12em]">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="px-3 py-6 text-center text-muted-foreground">
                No rows.
              </td>
            </tr>
          ) : (
            rows.map((row, index) => (
              <tr key={index} className="border-t border-border/50">
                {row.map((cell, cellIndex) => (
                  <td key={cellIndex} className="max-w-[24rem] truncate px-3 py-2">
                    {cell}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
