import type { HTMLAttributes } from "react";

export function Badge({ className = "", ...props }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={`inline-flex items-center rounded-full border border-border bg-muted px-2 py-0.5 text-xs text-muted-foreground ${className}`}
      {...props}
    />
  );
}
