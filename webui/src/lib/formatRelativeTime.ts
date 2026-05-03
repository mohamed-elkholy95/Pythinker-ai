/**
 * Format a UTC epoch-ms timestamp as either a relative phrase ("2m ago",
 * "3 hours ago") for events under 24h old, or an absolute month-day-time
 * stamp ("Apr 25, 14:32") for older events. Locale-aware via Intl APIs;
 * no external dependency.
 */
const ONE_MIN = 60_000;
const ONE_HOUR = 60 * ONE_MIN;
const ONE_DAY = 24 * ONE_HOUR;

export function formatRelativeTime(
  epochMs: number,
  now: number = Date.now(),
  locale?: string,
): string {
  const delta = now - epochMs;
  if (delta < 0) {
    // Future timestamps are treated as "just now" rather than negative deltas;
    // realistic clock skew on the client is small.
    return "just now";
  }
  if (delta < ONE_MIN) return "just now";
  const rtf = new Intl.RelativeTimeFormat(locale ?? undefined, { numeric: "auto" });
  if (delta < ONE_HOUR) {
    return rtf.format(-Math.floor(delta / ONE_MIN), "minute");
  }
  if (delta < ONE_DAY) {
    return rtf.format(-Math.floor(delta / ONE_HOUR), "hour");
  }
  // >= 24h: absolute "Apr 25, 14:32" using local TZ.
  return new Intl.DateTimeFormat(locale ?? undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(epochMs));
}
