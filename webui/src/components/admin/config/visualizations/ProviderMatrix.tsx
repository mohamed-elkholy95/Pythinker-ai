import type { AdminProviderSurfaceRow } from "@/lib/admin-api";

import { Badge } from "../primitives/Badge";
import { StatusDot } from "./StatusDot";

export function ProviderMatrix({
  rows,
  onAddKey,
}: {
  rows: AdminProviderSurfaceRow[];
  onAddKey: (name: string) => void;
}) {
  if (rows.length === 0) {
    return <p className="rounded-lg border border-border p-3 text-sm text-muted-foreground">No providers discovered.</p>;
  }
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {rows.map((row) => (
        <article key={row.name} className="rounded-xl border border-border bg-background p-3 shadow-sm">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="font-semibold">{row.name}</h3>
              <p className="text-xs text-muted-foreground">{row.backend}</p>
            </div>
            <StatusDot tone={row.active ? "green" : row.configured ? "amber" : "muted"} label={row.active ? "active" : row.configured ? "ready" : "needs key"} />
          </div>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {row.is_oauth ? <Badge>OAuth</Badge> : null}
            {row.is_gateway ? <Badge>Gateway</Badge> : null}
            {row.is_local ? <Badge>Local</Badge> : null}
            {row.is_direct ? <Badge>Direct</Badge> : null}
          </div>
          <p className="mt-3 truncate text-xs text-muted-foreground">{row.api_base ?? "No API base"}</p>
          {!row.key_set && !row.is_oauth && !row.is_local ? (
            <button
              className="mt-3 rounded-lg border border-primary/40 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/10"
              onClick={() => onAddKey(row.name)}
              type="button"
            >
              Add key for {row.name}
            </button>
          ) : null}
        </article>
      ))}
    </div>
  );
}
