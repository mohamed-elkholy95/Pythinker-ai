import { useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

import { ServicePanel, type WorkbenchServiceProps } from "./shared";

const TABS = ["browser", "web", "exec", "mcp", "my", "search"] as const;

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function mcpServerNames(toolsConfig: Record<string, unknown>, surfaces: Record<string, unknown>): string[] {
  const toolsSurface = asRecord(surfaces.tools);
  const surfaceServers = toolsSurface.mcp_servers;
  if (Array.isArray(surfaceServers)) {
    const names = surfaceServers
      .map((server) => asRecord(server).name)
      .filter((name): name is string => typeof name === "string" && name.length > 0);
    if (names.length > 0) return names;
  }

  const configuredServers = {
    ...asRecord(toolsConfig.mcpServers),
    ...asRecord(toolsConfig.mcp_servers),
  };
  return Object.keys(configuredServers);
}

export function ToolsView({ config, surfaces, onFocusPath }: WorkbenchServiceProps) {
  const { client } = useClient();
  const [active, setActive] = useState<(typeof TABS)[number]>("browser");
  const [mcpTools, setMcpTools] = useState<string[]>([]);
  const [browserContexts, setBrowserContexts] = useState<number | null>(null);
  const toolsConfig = asRecord(config.tools);
  const firstMcpServer = mcpServerNames(toolsConfig, surfaces)[0] ?? "local";
  return (
    <div className="space-y-4">
      <div role="tablist" aria-label="Tool views" className="inline-flex flex-wrap gap-1 rounded-xl border border-border bg-background p-1">
        {TABS.map((tab) => (
          <button key={tab} type="button" role="tab" aria-selected={active === tab} onClick={() => setActive(tab)} className={cn("rounded-lg px-3 py-1.5 text-xs", active === tab ? "bg-primary/15 text-primary" : "text-muted-foreground")}>{tab}</button>
        ))}
      </div>
      <ServicePanel title={`${active} tool`}>
        <pre className="overflow-auto rounded-lg bg-background p-3 text-xs">{JSON.stringify(toolsConfig[active] ?? {}, null, 2)}</pre>
        {active === "mcp" ? (
          <div className="mt-3 space-y-2">
            <Button type="button" variant="outline" onClick={async () => {
              const result = await client.probeAdminMcp(firstMcpServer);
              setMcpTools(result.tools);
            }}>Probe MCP {firstMcpServer}</Button>
            <div className="flex flex-wrap gap-1">{mcpTools.map((tool) => <span className="rounded-full border border-border px-2 py-1 text-xs" key={tool}>{tool}</span>)}</div>
          </div>
        ) : null}
        {active === "browser" ? (
          <div className="mt-3 space-y-2">
            <Button type="button" variant="outline" onClick={async () => {
              const result = await client.probeAdminBrowser();
              setBrowserContexts(result.active_contexts);
            }}>Probe browser</Button>
            {browserContexts !== null ? <p className="text-xs text-muted-foreground">Active contexts: {browserContexts}</p> : null}
          </div>
        ) : null}
        <button className="mt-3 text-xs text-primary underline" type="button" onClick={() => onFocusPath(`tools.${active}`)}>Focus tools.{active}</button>
      </ServicePanel>
    </div>
  );
}
