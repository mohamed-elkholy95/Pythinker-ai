export function UptimeSparkline({ buckets }: { buckets: number[] }) {
  const ticks = buckets.slice(-60);
  const padded = [...Array(Math.max(0, 60 - ticks.length)).fill(0), ...ticks];
  return (
    <div aria-label="uptime sparkline" className="mt-3 flex items-end gap-px" role="img">
      {padded.map((value, index) => (
        <span
          aria-label={`uptime tick ${index + 1}`}
          className={value > 0 ? "h-5 bg-emerald-500" : "h-2 bg-muted-foreground/25"}
          key={`${index}-${value}`}
          role="presentation"
          style={{ width: 3 }}
        />
      ))}
    </div>
  );
}
