import { Button } from "@/components/ui/button";

import { MetricCard, stringAt, type WorkbenchServiceProps } from "./shared";

export function LoggingView({ config, onFocusPath }: WorkbenchServiceProps) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <MetricCard label="Level" value={stringAt(config, "logging.level", "INFO")} />
      <MetricCard label="Sink" value={stringAt(config, "logging.sink", "loguru")} />
      <Button type="button" variant="outline" onClick={() => onFocusPath("logging.level")}>Focus logging level</Button>
    </div>
  );
}
