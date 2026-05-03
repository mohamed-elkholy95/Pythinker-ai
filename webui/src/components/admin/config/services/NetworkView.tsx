import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useClient } from "@/providers/ClientProvider";

import { MetricCard, type WorkbenchServiceProps } from "./shared";

export function NetworkView({ config, surfaces }: WorkbenchServiceProps) {
  const { client } = useClient();
  const [bindResult, setBindResult] = useState<string | null>(null);
  const overview = (surfaces.overview && typeof surfaces.overview === "object" ? surfaces.overview : {}) as Record<string, unknown>;
  const gateway = (overview.gateway && typeof overview.gateway === "object" ? overview.gateway : {}) as Record<string, unknown>;
  const api = (overview.api && typeof overview.api === "object" ? overview.api : {}) as Record<string, unknown>;
  const host = typeof gateway.host === "string" ? gateway.host : "127.0.0.1";
  const port = typeof gateway.port === "number" ? gateway.port : 8765;
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <MetricCard label="Gateway" value={`${host}:${port}`} hint="WebUI and websocket entrypoint" />
      <MetricCard label="API" value={`${api.host ?? "127.0.0.1"}:${api.port ?? "—"}`} hint="OpenAI-compatible endpoint" />
      <MetricCard label="Configured gateway" value={JSON.stringify(config.gateway ?? {})} />
      <MetricCard label="Configured API" value={JSON.stringify(config.api ?? {})} />
      <div className="rounded-xl border border-border bg-background p-3">
        <p className="text-xs text-muted-foreground">Bind probe</p>
        <Button className="mt-2" type="button" variant="outline" onClick={async () => {
          const result = await client.testAdminBind(host, port);
          setBindResult(result.ok ? "OK" : result.errno ?? "EUNKNOWN");
        }}>Test bind</Button>
        {bindResult ? <span className="ml-2 rounded-full border border-border px-2 py-1 text-xs">{bindResult}</span> : null}
      </div>
    </div>
  );
}
