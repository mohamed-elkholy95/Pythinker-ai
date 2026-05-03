import { Button } from "@/components/ui/button";

import { MetricCard, stringAt, type WorkbenchServiceProps } from "./shared";

export function UpdatesView({ config, onFocusPath }: WorkbenchServiceProps) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <MetricCard label="Update checks" value={stringAt(config, "updates.check", "true")} />
      <MetricCard label="Prereleases" value={stringAt(config, "updates.prerelease", "false")} />
      <Button type="button" variant="outline" onClick={() => onFocusPath("updates")}>Focus updates</Button>
    </div>
  );
}
