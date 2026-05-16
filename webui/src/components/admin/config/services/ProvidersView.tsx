import { useState } from "react";

import { Button } from "@/components/ui/button";
import type { AdminProviderSurfaceRow } from "@/lib/admin-api";
import { cn } from "@/lib/utils";

import { ProviderHeatmap } from "../visualizations/ProviderHeatmap";
import { ProviderMatrix } from "../visualizations/ProviderMatrix";
import type { WorkbenchServiceProps } from "./shared";

type Filter = "all" | "configured" | "needs-key";

export function ProvidersView({ surfaces, onFocusPath }: WorkbenchServiceProps) {
  const providerSurface = (surfaces.providers && typeof surfaces.providers === "object" ? surfaces.providers : {}) as { rows?: AdminProviderSurfaceRow[] };
  const rows = providerSurface.rows ?? [];
  const [filter, setFilter] = useState<Filter>("all");
  const [secretProvider, setSecretProvider] = useState<string | null>(null);
  const visible = rows.filter((row) => filter === "all" || (filter === "configured" ? row.configured : !row.key_set));
  return (
    <div className="space-y-4">
      <ProviderHeatmap rows={rows} />
      <div className="flex flex-wrap gap-2" aria-label="Provider filters">
        {(["all", "configured", "needs-key"] as const).map((item) => (
          <button
            key={item}
            type="button"
            aria-pressed={filter === item}
            onClick={() => setFilter(item)}
            className={cn(
              "menu-green-hover inline-flex h-9 items-center rounded-md border border-input bg-background px-3 text-sm font-medium",
              filter === item && "menu-green-active",
            )}
          >
            {item === "needs-key" ? "Needs key" : item[0].toUpperCase() + item.slice(1)}
          </button>
        ))}
      </div>
      <ProviderMatrix rows={visible} onAddKey={(name) => setSecretProvider(name)} />
      {secretProvider ? (
        <div role="dialog" aria-label="Replace secret" className="rounded-xl border border-primary/40 bg-background p-4 shadow-lg">
          <h3 className="font-semibold">Replace secret for {secretProvider}</h3>
          <p className="mt-1 text-sm text-muted-foreground">Secret values are write-only. Open the schema field to save the key.</p>
          <div className="mt-3 flex gap-2">
            <Button type="button" onClick={() => onFocusPath(`providers.${secretProvider}.api_key`)}>Focus secret field</Button>
            <Button type="button" variant="outline" onClick={() => setSecretProvider(null)}>Close</Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
