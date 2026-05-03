import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { formatRelativeTime } from "@/lib/formatRelativeTime";
import { cn } from "@/lib/utils";

interface MessageTimestampProps {
  createdAt: number;
  className?: string;
}

/**
 * Subtle subscript shown under each message bubble on hover.
 * Re-renders once a minute so "5 minutes ago" stays accurate without
 * needing the parent to pump prop updates.
 */
export function MessageTimestamp({ createdAt, className }: MessageTimestampProps) {
  const { i18n, t } = useTranslation();
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!createdAt) return;
    const id = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, [createdAt]);

  if (!createdAt) return null;

  const label = formatRelativeTime(createdAt, now, i18n.resolvedLanguage);
  const fullStamp = new Date(createdAt).toLocaleString(i18n.resolvedLanguage);

  return (
    <time
      role="time"
      dateTime={new Date(createdAt).toISOString()}
      title={t("timestamp.tooltipFull", { stamp: fullStamp })}
      className={cn(
        "select-none text-[10.5px] tabular-nums text-muted-foreground/60",
        "opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100",
        "motion-reduce:transition-none",
        className,
      )}
    >
      {label}
    </time>
  );
}
