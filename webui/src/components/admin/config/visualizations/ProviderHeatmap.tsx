import type { AdminProviderSurfaceRow } from "@/lib/admin-api";

import { cn } from "@/lib/utils";

export function ProviderHeatmap({ rows }: { rows: AdminProviderSurfaceRow[] }) {
  return (
    <div className="flex flex-wrap gap-1" aria-label="Provider heatmap">
      {rows.map((row) => (
        <span
          key={row.name}
          title={row.name}
          className={cn(
            "h-3 w-8 rounded-full border border-border",
            row.active && "bg-emerald-500/80",
            !row.active && row.configured && "bg-amber-500/70",
            !row.configured && "bg-muted",
          )}
        />
      ))}
    </div>
  );
}
