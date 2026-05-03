import { ChevronRight, Wrench } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

interface ToolTraceChipsProps {
  traces: string[];
  className?: string;
}

/** Match a "× N" repeat suffix the backend appends when the same hint
 * formatted into the same string multiple times in one turn (see
 * `pythinker/utils/tool_hints.py`). */
const REPEAT_SUFFIX_RE = /\s*×\s*(\d+)\s*$/;

/**
 * Extract a compact kind label from a single hint atom such as:
 *   "read /tmp/foo"        → "read"
 *   "$ ls -la"             → "exec"
 *   'search "rust"'        → "search"
 *   'weather("get")'       → "weather"
 *   "github::issues(...)"  → "github::issues"
 *
 * Falls back to the supplied label when the atom doesn't start with an
 * identifier-like token (matches the `format_tool_hints` shape).
 */
function extractToolKind(atom: string, fallback: string): string {
  const trimmed = atom.trim();
  if (!trimmed) return fallback;
  if (trimmed.startsWith("$")) return "exec";
  const ns = trimmed.match(/^[A-Za-z_][\w-]*::[A-Za-z_][\w-]*/);
  if (ns) return ns[0];
  const ident = trimmed.match(/^[A-Za-z_][\w-]*/);
  if (ident) return ident[0];
  return fallback;
}

/**
 * One trace event line can carry several comma-joined hints (e.g.
 * "read /tmp/foo, write /tmp/bar × 3"). Split into atoms, peel off any
 * trailing "× N" repeat suffix, and tally per kind.
 */
function tallyKinds(lines: string[], fallback: string): Map<string, number> {
  const counts = new Map<string, number>();
  for (const line of lines) {
    if (!line) continue;
    for (const segment of line.split(/,\s*/)) {
      const match = segment.match(REPEAT_SUFFIX_RE);
      const repeats = match ? parseInt(match[1], 10) : 1;
      const stripped = match ? segment.slice(0, match.index).trimEnd() : segment;
      const kind = extractToolKind(stripped, fallback);
      counts.set(kind, (counts.get(kind) ?? 0) + repeats);
    }
  }
  return counts;
}

/**
 * Compact tool-call summary: one chip per unique tool kind, with an expand
 * toggle that reveals the full per-line trace text underneath.
 */
export function ToolTraceChips({ traces, className }: ToolTraceChipsProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (traces.length === 0) return null;

  const fallback = t("trace.fallbackKind");
  const counts = tallyKinds(traces, fallback);

  return (
    <div className={cn("mt-1 flex flex-col gap-1", className)}>
      <div className="flex flex-wrap items-center gap-1">
        <Wrench size={12} className="text-muted-foreground" aria-hidden />
        {Array.from(counts.entries()).map(([kind, count]) => (
          <span
            key={kind}
            className={cn(
              "inline-flex items-center gap-1 rounded-full border border-border/50",
              "bg-secondary/40 px-2 py-0.5 text-[11px] text-muted-foreground",
            )}
          >
            {kind}
            {count > 1 ? (
              <span className="text-muted-foreground/60">×{count}</span>
            ) : null}
          </span>
        ))}
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-label={open ? t("trace.collapse") : t("trace.expand")}
          aria-expanded={open}
          className={cn(
            "ml-1 inline-flex h-5 w-5 items-center justify-center rounded",
            "text-muted-foreground/70 hover:bg-secondary/60 hover:text-foreground",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          )}
        >
          <ChevronRight
            size={12}
            className={cn(
              "transition-transform",
              open ? "rotate-90" : "rotate-0",
            )}
            aria-hidden
          />
        </button>
      </div>
      {open ? (
        <ul
          className={cn(
            "ml-4 list-none space-y-0.5 font-mono text-[11px] text-muted-foreground/80",
            "animate-in fade-in-0 slide-in-from-top-1 duration-150",
          )}
        >
          {traces.map((line, i) => (
            <li key={i} className="whitespace-pre-wrap break-words">
              {line}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
