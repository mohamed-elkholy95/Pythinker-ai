export function SsrfCoverageBar({ blocked }: { blocked: string[] }) {
  return (
    <div className="space-y-2">
      <div className="h-2 overflow-hidden rounded-full bg-muted">
        <div className="h-full w-3/4 rounded-full bg-emerald-500" />
      </div>
      <p className="text-xs text-muted-foreground">Blocked by default: {blocked.join(", ") || "standard private ranges"}</p>
    </div>
  );
}
