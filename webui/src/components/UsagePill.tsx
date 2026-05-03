import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

interface UsagePillProps {
  used: number;
  limit: number;
  className?: string;
}

function formatTokens(n: number): string {
  if (n < 1_000) return String(n);
  if (n < 1_000_000) return `${(n / 1_000).toFixed(1).replace(/\.0$/, "")}k`;
  return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
}

/**
 * Compact context-window usage indicator. Renders nothing when limit is 0
 * (no active session). Above 75% the colour shifts amber; above 90%, red.
 *
 * Bar width is clamped at 100% so an over-budget session still renders a
 * full-bar instead of overflowing the parent layout. Server-side usage is
 * already clamped via ``estimate_session_usage``; this is defence in depth.
 */
export function UsagePill({ used, limit, className }: UsagePillProps) {
  const { t } = useTranslation();
  if (limit <= 0) return null;
  const percent = Math.min(100, (used / limit) * 100);
  const tone =
    percent >= 90
      ? "text-destructive"
      : percent >= 75
        ? "text-amber-600 dark:text-amber-400"
        : "text-muted-foreground";
  const barTone =
    percent >= 90
      ? "bg-destructive"
      : percent >= 75
        ? "bg-amber-500"
        : "bg-muted-foreground/50";

  return (
    <div
      role="status"
      aria-label={t("usage.ariaLabel", { used, limit })}
      className={cn(
        "inline-flex flex-col items-end gap-0.5 text-[11px] tabular-nums",
        tone,
        className,
      )}
    >
      <span>{t("usage.label", { used: formatTokens(used), limit: formatTokens(limit) })}</span>
      <div className="h-[2px] w-24 overflow-hidden rounded-full bg-secondary/40">
        <div
          className={cn("h-full transition-[width] duration-300", barTone)}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}
