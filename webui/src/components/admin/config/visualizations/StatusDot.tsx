import { cn } from "@/lib/utils";

export type StatusTone = "green" | "amber" | "red" | "muted";

const toneClass: Record<StatusTone, string> = {
  green: "bg-emerald-500 shadow-emerald-500/40",
  amber: "bg-amber-500 shadow-amber-500/40",
  red: "bg-red-500 shadow-red-500/40",
  muted: "bg-muted-foreground/40 shadow-muted-foreground/20",
};

export function StatusDot({ tone, label }: { tone: StatusTone; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <span
        aria-hidden
        className={cn("h-2 w-2 rounded-full shadow-[0_0_10px]", toneClass[tone])}
      />
      {label}
    </span>
  );
}
