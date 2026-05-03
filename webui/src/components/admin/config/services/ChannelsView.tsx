import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useClient } from "@/providers/ClientProvider";

import { ChannelGrid } from "../visualizations/ChannelGrid";
import type { WorkbenchServiceProps } from "./shared";

type ChannelRow = Record<string, unknown> & { name: string };

export function ChannelsView({ surfaces }: WorkbenchServiceProps) {
  const { client } = useClient();
  const [checks, setChecks] = useState<string[]>([]);
  const channels = (surfaces.channels && typeof surfaces.channels === "object" ? surfaces.channels : {}) as { rows?: ChannelRow[] };
  const rows = channels.rows ?? [];
  return (
    <div className="space-y-4">
      <ChannelGrid rows={rows} />
      <div className="rounded-xl border border-border bg-background p-3">
        <p className="text-xs text-muted-foreground">Config-only validation</p>
        <div className="mt-2 flex flex-wrap gap-2">
          {rows.map((row) => (
            <Button key={row.name} type="button" variant="outline" onClick={async () => {
              const result = await client.testAdminChannel(row.name);
              setChecks(result.checks);
            }}>Validate {row.name}</Button>
          ))}
        </div>
        {checks.length > 0 ? <div className="mt-3 flex flex-wrap gap-1">{checks.map((check) => <span className="rounded-full border border-border px-2 py-1 text-xs" key={check}>{check}</span>)}</div> : null}
      </div>
    </div>
  );
}
