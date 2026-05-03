import { Button } from "@/components/ui/button";

import { MetricCard, stringAt, type WorkbenchServiceProps } from "./shared";

export function CliView({ config, onFocusPath }: WorkbenchServiceProps) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <MetricCard label="TUI theme" value={stringAt(config, "cli.tui.theme", "default")} />
      <MetricCard label="Logs" value={stringAt(config, "cli.logs", "default")} />
      <Button type="button" variant="outline" onClick={() => onFocusPath("cli.tui.theme")}>Focus CLI theme</Button>
    </div>
  );
}
