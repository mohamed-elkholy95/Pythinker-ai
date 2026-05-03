import { Badge } from "../primitives/Badge";
import { MetricCard, ServicePanel, stringAt, type WorkbenchServiceProps } from "./shared";

export function AgentsView({ config, surfaces, onFocusPath }: WorkbenchServiceProps) {
  const agents = (surfaces.agents && typeof surfaces.agents === "object" ? surfaces.agents : {}) as Record<string, unknown>;
  const routing = (agents.routing && typeof agents.routing === "object" ? agents.routing : {}) as Record<string, unknown>;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Model" value={stringAt(config, "agents.defaults.model")} />
        <MetricCard label="Provider" value={String(routing.matched_spec ?? "auto")} />
        <MetricCard label="Workspace" value={stringAt(config, "agents.defaults.workspace", "default")} />
        <MetricCard label="Max tokens" value={stringAt(config, "agents.defaults.max_tokens", "auto")} />
      </div>
      <ServicePanel title="Routing trace">
        <div className="flex flex-wrap gap-2 text-sm">
          <Badge>{String(routing.match_phase ?? "unknown")}</Badge>
          <Badge>{String(routing.matched_keyword ?? "no keyword")}</Badge>
          <Badge>{String(routing.resolved_api_base ?? "no api base")}</Badge>
        </div>
        <button className="mt-3 text-xs text-primary underline" onClick={() => onFocusPath("agents.defaults.model")} type="button">Focus model path</button>
      </ServicePanel>
    </div>
  );
}
