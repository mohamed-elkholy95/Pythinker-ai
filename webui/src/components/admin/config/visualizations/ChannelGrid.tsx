import { Badge } from "../primitives/Badge";
import { StatusDot } from "./StatusDot";
import { UptimeSparkline } from "./UptimeSparkline";

type ChannelRow = Record<string, unknown> & { name: string };

const BUILT_INS = ["websocket", "telegram", "slack", "discord", "matrix", "whatsapp", "msteams", "email"];

export function ChannelGrid({ rows }: { rows: ChannelRow[] }) {
  const byName = new Map(rows.map((row) => [row.name, row]));
  const pluginRows = rows.filter((row) => !BUILT_INS.includes(row.name));
  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {BUILT_INS.map((name) => {
          const row = byName.get(name);
          const enabled = Boolean(row?.enabled);
          const running = Boolean(row?.running);
          const secrets = Array.isArray(row?.required_secrets) ? row.required_secrets.filter((item): item is string => typeof item === "string") : [];
          const uptimeBuckets = Array.isArray(row?.uptime_buckets) ? row.uptime_buckets.filter((item): item is number => typeof item === "number") : [];
          return (
            <article key={name} className="rounded-xl border border-border bg-background p-3">
              <div className="flex items-center justify-between gap-2">
                <h3 className="font-medium">{name}</h3>
                <StatusDot tone={running ? "green" : enabled ? "amber" : "muted"} label={running ? "running" : enabled ? "enabled" : "off"} />
              </div>
              <div className="mt-2 flex flex-wrap gap-1">
                {secrets.length === 0 ? <Badge>No required secrets</Badge> : secrets.map((secret) => <Badge key={secret}>{secret}</Badge>)}
              </div>
              {uptimeBuckets.length > 0 ? <UptimeSparkline buckets={uptimeBuckets} /> : null}
            </article>
          );
        })}
      </div>
      {pluginRows.length > 0 ? (
        <div className="rounded-xl border border-border bg-muted/30 p-3 text-sm">
          Plugin channels: {pluginRows.map((row) => row.name).join(", ")}
        </div>
      ) : null}
    </div>
  );
}
