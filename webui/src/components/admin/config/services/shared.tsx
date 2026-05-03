import type { ReactNode } from "react";

export type WorkbenchServiceProps = {
  config: Record<string, unknown>;
  surfaces: Record<string, unknown>;
  onFocusPath: (path: string) => void;
  onStage: (path: string, value: unknown) => void;
};

export function ServicePanel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-border bg-muted/20 p-4">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">{title}</h2>
      <div className="mt-3">{children}</div>
    </section>
  );
}

export function MetricCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-border bg-background p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 truncate text-lg font-semibold">{value}</p>
      {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

export function recordAt(value: Record<string, unknown>, path: string): Record<string, unknown> {
  return path.split(".").reduce<Record<string, unknown>>((current, segment) => {
    const next = current[segment];
    return next && typeof next === "object" && !Array.isArray(next) ? (next as Record<string, unknown>) : {};
  }, value);
}

export function stringAt(value: Record<string, unknown>, path: string, fallback = "—"): string {
  const segments = path.split(".");
  const last = segments.pop();
  const parent = segments.length > 0 ? recordAt(value, segments.join(".")) : value;
  const result = last ? parent[last] : undefined;
  return typeof result === "string" || typeof result === "number" || typeof result === "boolean" ? String(result) : fallback;
}
