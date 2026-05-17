import { formatTokens } from "@/lib/format";

export interface StackedUsageBarProps {
  floor: number;
  used: number;
  limit: number;
  label?: string;
}

export function StackedUsageBar({ floor, used, limit, label }: StackedUsageBarProps) {
  const safeLimit = Math.max(1, limit);
  const clampedUsed = Math.min(Math.max(0, used), safeLimit);
  const clampedFloor = Math.min(Math.max(0, floor), clampedUsed);
  const history = Math.max(0, clampedUsed - clampedFloor);
  const headroom = Math.max(0, safeLimit - clampedUsed);
  const pct = (n: number) => `${(n / safeLimit) * 100}%`;
  const usedPct = Math.round((clampedUsed / safeLimit) * 100);

  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>{label ?? ""}</span>
        <span>{usedPct}% · {formatTokens(clampedUsed)} / {formatTokens(safeLimit)}</span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded bg-muted">
        {clampedFloor > 0 && (
          <div
            data-segment="floor"
            className="inline-block h-full bg-zinc-400 align-top"
            style={{ width: pct(clampedFloor) }}
            title={`Floor: ${formatTokens(clampedFloor)} (system + tool defs)`}
          />
        )}
        <div
          data-segment="history"
          className="inline-block h-full bg-emerald-500 align-top"
          style={{ width: pct(history) }}
          title={`History: ${formatTokens(history)}`}
        />
        <div
          data-segment="headroom"
          className="inline-block h-full bg-emerald-100 align-top dark:bg-emerald-950"
          style={{ width: pct(headroom) }}
          title={`Headroom: ${formatTokens(headroom)}`}
        />
      </div>
    </div>
  );
}
