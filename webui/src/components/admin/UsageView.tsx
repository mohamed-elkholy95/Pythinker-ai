import {
  Activity,
  Coins,
  CreditCard,
  Database,
  Gauge,
  Layers,
  Sparkles,
} from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

import type { AdminSurfaces } from "@/lib/admin-api";
import { cn } from "@/lib/utils";

type SubView = "summary" | "sessions" | "ledger";

const SUB_VIEWS: Array<{ id: SubView; label: string; icon: ReactNode }> = [
  { id: "summary", label: "Summary", icon: <Gauge className="h-3.5 w-3.5" /> },
  {
    id: "sessions",
    label: "Sessions",
    icon: <Database className="h-3.5 w-3.5" />,
  },
  {
    id: "ledger",
    label: "Ledger",
    icon: <Layers className="h-3.5 w-3.5" />,
  },
];

/** Compact human-readable token count: 1234 → "1.23k", 1_500_000 → "1.50M". */
function formatTokens(value: number | null | undefined): string {
  const n = value ?? 0;
  if (n === 0) return "0";
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(2)}k`;
  return new Intl.NumberFormat().format(n);
}

function formatNumber(value?: number): string {
  return new Intl.NumberFormat().format(value ?? 0);
}

function formatCost(cost: number | null, currency: string | null): string {
  if (cost === null || cost === undefined) return "—";
  const c = currency ?? "USD";
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: c,
      maximumFractionDigits: 4,
    }).format(cost);
  } catch {
    return `${cost.toFixed(4)} ${c}`;
  }
}

/**
 * Refreshed Usage tab — replaces the prior single MetricCard row + JSON dump.
 *
 * Three sub-views:
 *   • Summary  — KPI cards, last-turn donut + breakdown, top sessions, channel rollup
 *   • Sessions — full session usage table with per-row context-window progress bars
 *   • Ledger   — raw ledger JSON for power users
 *
 * Charts are pure SVG/CSS (no chart library) so the bundle size stays flat
 * and theming follows the existing CSS variables.
 */
export function UsageView({ data }: { data: AdminSurfaces }) {
  const [view, setView] = useState<SubView>("summary");
  return (
    <div className="space-y-4">
      <SubViewSwitch active={view} onChange={setView} />
      {view === "summary" && <SummaryView data={data} />}
      {view === "sessions" && <SessionsView data={data} />}
      {view === "ledger" && <LedgerView data={data} />}
    </div>
  );
}

function SubViewSwitch({
  active,
  onChange,
}: {
  active: SubView;
  onChange: (v: SubView) => void;
}) {
  return (
    <div
      role="tablist"
      aria-label="Usage views"
      className="inline-flex items-center gap-1 rounded-xl border border-border/60 bg-background/55 p-1"
    >
      {SUB_VIEWS.map((v) => (
        <button
          key={v.id}
          type="button"
          role="tab"
          aria-selected={active === v.id}
          onClick={() => onChange(v.id)}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors",
            active === v.id
              ? "bg-primary/15 text-primary shadow-[inset_0_0_0_1px_hsl(var(--primary)/0.3)]"
              : "text-muted-foreground hover:bg-accent/40 hover:text-foreground",
          )}
        >
          {v.icon}
          {v.label}
        </button>
      ))}
    </div>
  );
}

function SummaryView({ data }: { data: AdminSurfaces }) {
  const lastTurn = data.usage.last_turn ?? {};
  const promptTokens = Number(lastTurn.prompt_tokens ?? 0);
  const completionTokens = Number(lastTurn.completion_tokens ?? 0);
  const totalLastTurn = Number(
    lastTurn.total_tokens ?? promptTokens + completionTokens,
  );
  const sessions = data.usage.sessions ?? data.sessions.sessions ?? [];
  const consumption = data.usage.consumption ?? {
    total_tokens: 0,
    cost: null,
    currency: null,
  };

  const channelRollup = useMemo(() => {
    const m = new Map<string, number>();
    for (const s of sessions) {
      m.set(s.channel, (m.get(s.channel) ?? 0) + (s.usage?.used ?? 0));
    }
    return Array.from(m, ([channel, used]) => ({ channel, used }))
      .sort((a, b) => b.used - a.used);
  }, [sessions]);

  const topSessions = useMemo(
    () => [...sessions]
      .sort((a, b) => (b.usage?.used ?? 0) - (a.usage?.used ?? 0))
      .slice(0, 6),
    [sessions],
  );

  const channelTotal = channelRollup.reduce((acc, r) => acc + r.used, 0);

  return (
    <div className="space-y-4">
      <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <KPICard
          icon={<Coins className="h-4 w-4" />}
          label="Lifetime tokens"
          value={formatTokens(consumption.total_tokens)}
          hint={`${formatNumber(consumption.total_tokens)} total`}
          tone="emerald"
        />
        <KPICard
          icon={<CreditCard className="h-4 w-4" />}
          label="Cost accrued"
          value={formatCost(consumption.cost, consumption.currency)}
          hint={consumption.cost === null ? "Not tracked" : "Lifetime"}
          tone="amber"
        />
        <KPICard
          icon={<Database className="h-4 w-4" />}
          label="Tracked sessions"
          value={String(sessions.length)}
          hint={`across ${channelRollup.length} channel${channelRollup.length === 1 ? "" : "s"}`}
          tone="sky"
        />
        <KPICard
          icon={<Sparkles className="h-4 w-4" />}
          label="Last turn"
          value={formatTokens(totalLastTurn)}
          hint={`${formatTokens(promptTokens)} prompt · ${formatTokens(completionTokens)} completion`}
          tone="violet"
        />
      </section>

      <section className="grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
        <Panel title="Last-turn breakdown" icon={<Activity className="h-4 w-4" />}>
          <LastTurnBreakdown
            prompt={promptTokens}
            completion={completionTokens}
            total={totalLastTurn}
            extras={lastTurn}
          />
        </Panel>
        <Panel title="Top sessions" icon={<Database className="h-4 w-4" />}>
          {topSessions.length === 0 ? (
            <EmptyHint label="No sessions tracked yet." />
          ) : (
            <ul className="space-y-3">
              {topSessions.map((s) => (
                <SessionUsageRow
                  key={s.key}
                  label={s.title || s.preview || s.key}
                  sub={`${s.channel} · ${s.key}`}
                  used={s.usage?.used ?? 0}
                  limit={s.usage?.limit ?? 0}
                />
              ))}
            </ul>
          )}
        </Panel>
      </section>

      <Panel title="By channel" icon={<Layers className="h-4 w-4" />}>
        {channelRollup.length === 0 ? (
          <EmptyHint label="No channel activity yet." />
        ) : (
          <ul className="space-y-2.5">
            {channelRollup.map(({ channel, used }) => (
              <ChannelRow
                key={channel}
                label={channel}
                used={used}
                total={channelTotal}
              />
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

function SessionsView({ data }: { data: AdminSurfaces }) {
  const sessions = data.usage.sessions ?? data.sessions.sessions ?? [];
  return (
    <Panel title="Sessions" icon={<Database className="h-4 w-4" />}>
      <div className="overflow-auto rounded-lg border border-border/60">
        <table className="min-w-full text-left text-xs">
          <thead className="bg-muted/60 text-muted-foreground">
            <tr>
              <th className="px-3 py-2 font-semibold uppercase tracking-[0.12em]">
                Session
              </th>
              <th className="px-3 py-2 font-semibold uppercase tracking-[0.12em]">
                Channel
              </th>
              <th className="px-3 py-2 font-semibold uppercase tracking-[0.12em]">
                Used / Limit
              </th>
              <th className="w-[40%] px-3 py-2 font-semibold uppercase tracking-[0.12em]">
                Context window
              </th>
            </tr>
          </thead>
          <tbody>
            {sessions.length === 0 ? (
              <tr>
                <td
                  colSpan={4}
                  className="px-3 py-6 text-center text-muted-foreground"
                >
                  No sessions tracked.
                </td>
              </tr>
            ) : (
              sessions.map((s) => {
                const used = s.usage?.used ?? 0;
                const limit = s.usage?.limit ?? 0;
                const pct = limit > 0
                  ? Math.min(100, Math.round((used / limit) * 100))
                  : 0;
                return (
                  <tr key={s.key} className="border-t border-border/50">
                    <td className="max-w-[20rem] truncate px-3 py-2 font-medium">
                      {s.title || s.preview || s.key}
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">
                      {s.channel}
                    </td>
                    <td className="px-3 py-2 tabular-nums">
                      {formatTokens(used)} / {formatTokens(limit)}
                    </td>
                    <td className="px-3 py-2">
                      <ContextBar used={used} limit={limit} pct={pct} />
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function LedgerView({ data }: { data: AdminSurfaces }) {
  return (
    <Panel title="Usage Ledger" icon={<Gauge className="h-4 w-4" />}>
      <pre className="max-h-[36rem] overflow-auto rounded-xl border border-border/60 bg-background/65 p-4 text-xs leading-5">
        {JSON.stringify(data.usage.ledger ?? data.usage, null, 2)}
      </pre>
    </Panel>
  );
}

/* ─── primitives ─────────────────────────────────────────────────────── */

const TONE: Record<string, { bg: string; text: string; ring: string }> = {
  emerald: {
    bg: "bg-emerald-500/12",
    text: "text-emerald-600 dark:text-emerald-400",
    ring: "ring-emerald-500/25",
  },
  amber: {
    bg: "bg-amber-500/12",
    text: "text-amber-600 dark:text-amber-400",
    ring: "ring-amber-500/25",
  },
  sky: {
    bg: "bg-sky-500/12",
    text: "text-sky-600 dark:text-sky-400",
    ring: "ring-sky-500/25",
  },
  violet: {
    bg: "bg-violet-500/12",
    text: "text-violet-600 dark:text-violet-400",
    ring: "ring-violet-500/25",
  },
};

function KPICard({
  icon,
  label,
  value,
  hint,
  tone,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  hint?: string;
  tone: keyof typeof TONE;
}) {
  const t = TONE[tone];
  return (
    <div className="group relative overflow-hidden rounded-2xl border border-border/60 bg-background/55 p-4 shadow-sm transition-shadow hover:shadow-md">
      <div className="flex items-start justify-between gap-2">
        <span className="text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
          {label}
        </span>
        <span
          className={cn(
            "flex h-7 w-7 items-center justify-center rounded-lg ring-1",
            t.bg,
            t.text,
            t.ring,
          )}
          aria-hidden
        >
          {icon}
        </span>
      </div>
      <div className="mt-3 truncate text-2xl font-semibold tabular-nums">
        {value}
      </div>
      {hint ? (
        <div className="mt-1 truncate text-xs text-muted-foreground">{hint}</div>
      ) : null}
    </div>
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

function EmptyHint({ label }: { label: string }) {
  return (
    <div className="rounded-lg border border-dashed border-border/60 p-4 text-center text-xs text-muted-foreground">
      {label}
    </div>
  );
}

/** Last-turn donut + segment legend. SVG donut with two arcs. */
function LastTurnBreakdown({
  prompt,
  completion,
  total,
  extras,
}: {
  prompt: number;
  completion: number;
  total: number;
  extras: Record<string, number>;
}) {
  // Pull anything that looks like a token bucket beyond prompt/completion/total
  // — providers vary (cached_input_tokens, reasoning_tokens, etc.) — and show
  // them as an "Other" segment so the donut still sums to total when the model
  // reports more than two buckets.
  const knownKeys = new Set([
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
  ]);
  const other = Object.entries(extras)
    .filter(([k]) => !knownKeys.has(k) && typeof extras[k] === "number")
    .reduce((acc, [, v]) => acc + Number(v ?? 0), 0);

  const segments = [
    { label: "Prompt", value: prompt, color: "hsl(212, 92%, 60%)" },
    { label: "Completion", value: completion, color: "hsl(152, 64%, 45%)" },
    ...(other > 0
      ? [{ label: "Other", value: other, color: "hsl(280, 65%, 60%)" }]
      : []),
  ];
  const sum = segments.reduce((acc, s) => acc + s.value, 0) || 1;

  return (
    <div className="grid items-center gap-4 sm:grid-cols-[auto_1fr]">
      <Donut segments={segments} sum={sum} />
      <div className="space-y-2.5">
        {segments.map((s) => {
          const pct = Math.round((s.value / sum) * 100);
          return (
            <div key={s.label} className="space-y-1">
              <div className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-2">
                  <span
                    className="inline-block h-2.5 w-2.5 rounded-sm"
                    style={{ background: s.color }}
                    aria-hidden
                  />
                  <span className="font-medium">{s.label}</span>
                </div>
                <span className="tabular-nums text-muted-foreground">
                  {formatTokens(s.value)}
                  <span className="ml-2 text-muted-foreground/70">{pct}%</span>
                </span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full transition-[width] duration-300"
                  style={{
                    width: `${(s.value / sum) * 100}%`,
                    background: s.color,
                  }}
                />
              </div>
            </div>
          );
        })}
        <div className="mt-3 flex items-center justify-between border-t border-border/60 pt-2 text-xs">
          <span className="text-muted-foreground">Total</span>
          <span className="font-semibold tabular-nums">
            {formatTokens(total)}
          </span>
        </div>
      </div>
    </div>
  );
}

function Donut({
  segments,
  sum,
}: {
  segments: Array<{ label: string; value: number; color: string }>;
  sum: number;
}) {
  const size = 132;
  const stroke = 18;
  const radius = (size - stroke) / 2;
  const circ = 2 * Math.PI * radius;
  let offset = 0;
  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label="Last-turn token breakdown"
      className="shrink-0"
    >
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="hsl(var(--muted))"
        strokeWidth={stroke}
      />
      {segments.map((s) => {
        const len = (s.value / sum) * circ;
        const dasharray = `${len} ${circ - len}`;
        const dashoffset = -offset;
        offset += len;
        return (
          <circle
            key={s.label}
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={s.color}
            strokeWidth={stroke}
            strokeDasharray={dasharray}
            strokeDashoffset={dashoffset}
            transform={`rotate(-90 ${size / 2} ${size / 2})`}
            strokeLinecap="butt"
          />
        );
      })}
      <text
        x="50%"
        y="46%"
        textAnchor="middle"
        className="fill-foreground text-[18px] font-semibold tabular-nums"
        style={{ dominantBaseline: "middle" }}
      >
        {formatTokens(sum)}
      </text>
      <text
        x="50%"
        y="60%"
        textAnchor="middle"
        className="fill-muted-foreground text-[10px] uppercase tracking-[0.2em]"
        style={{ dominantBaseline: "middle" }}
      >
        tokens
      </text>
    </svg>
  );
}

function SessionUsageRow({
  label,
  sub,
  used,
  limit,
}: {
  label: string;
  sub: string;
  used: number;
  limit: number;
}) {
  const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  const hot = pct >= 85;
  return (
    <li className="space-y-1">
      <div className="flex items-baseline justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-xs font-medium">{label}</div>
          <div className="truncate text-[10px] text-muted-foreground">{sub}</div>
        </div>
        <div className="shrink-0 tabular-nums text-xs text-muted-foreground">
          <span className={cn(hot && "text-amber-600 dark:text-amber-400")}>
            {pct}%
          </span>
          <span className="ml-2 text-muted-foreground/70">
            {formatTokens(used)} / {formatTokens(limit)}
          </span>
        </div>
      </div>
      <ContextBar used={used} limit={limit} pct={pct} />
    </li>
  );
}

function ContextBar({
  pct,
}: {
  used: number;
  limit: number;
  pct: number;
}) {
  const hot = pct >= 85;
  const warn = pct >= 60;
  return (
    <div
      className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className={cn(
          "h-full rounded-full transition-[width] duration-300",
          hot
            ? "bg-rose-500"
            : warn
              ? "bg-amber-500"
              : "bg-primary/80",
        )}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

function ChannelRow({
  label,
  used,
  total,
}: {
  label: string;
  used: number;
  total: number;
}) {
  const pct = total > 0 ? Math.round((used / total) * 100) : 0;
  return (
    <li className="space-y-1">
      <div className="flex items-baseline justify-between gap-3 text-xs">
        <span className="truncate font-medium capitalize">{label}</span>
        <span className="shrink-0 tabular-nums text-muted-foreground">
          {formatTokens(used)}
          <span className="ml-2 text-muted-foreground/70">{pct}%</span>
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-gradient-to-r from-primary/80 to-primary/40 transition-[width] duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
    </li>
  );
}
