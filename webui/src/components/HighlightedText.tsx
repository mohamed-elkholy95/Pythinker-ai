import { Fragment, type ReactNode } from "react";

import { cn } from "@/lib/utils";

interface HighlightedTextProps {
  text: string;
  query: string;
  /** Stable prefix used to derive each match's ``data-match-id``. The full id
   * is ``${idPrefix}:${matchIndex}``. */
  idPrefix: string;
  /** Match id (``${idPrefix}:${i}``) currently focused; renders with a
   * stronger style. ``undefined`` means no active match in this run. */
  activeMatchId?: string;
  className?: string;
}

/**
 * Pure text splitter: returns a fragment of plain strings interleaved with
 * ``<mark>`` spans wrapping every case-insensitive substring match of
 * ``query``. Designed for both the in-chat finder (assistant + user bubbles)
 * and the cross-chat search snippets in the sidebar.
 *
 * Substring match only — no regex parsing — so user input cannot blow up
 * with regex metacharacters or catastrophic backtracking.
 */
export function HighlightedText({
  text,
  query,
  idPrefix,
  activeMatchId,
  className,
}: HighlightedTextProps): ReactNode {
  if (!query) return <>{text}</>;

  const haystack = text.toLowerCase();
  const needle = query.toLowerCase();
  if (!needle) return <>{text}</>;

  const segments: ReactNode[] = [];
  let cursor = 0;
  let matchIndex = 0;
  while (cursor <= haystack.length) {
    const found = haystack.indexOf(needle, cursor);
    if (found < 0) {
      segments.push(<Fragment key={`t${cursor}`}>{text.slice(cursor)}</Fragment>);
      break;
    }
    if (found > cursor) {
      segments.push(
        <Fragment key={`t${cursor}`}>{text.slice(cursor, found)}</Fragment>,
      );
    }
    const matchId = `${idPrefix}:${matchIndex}`;
    const original = text.slice(found, found + needle.length);
    const isActive = activeMatchId === matchId;
    segments.push(
      <mark
        key={`m${matchIndex}`}
        data-match-id={matchId}
        className={cn(
          "rounded-[3px] px-[1px] bg-amber-200/60 text-foreground",
          "dark:bg-amber-300/30",
          isActive && "active bg-amber-400/80 dark:bg-amber-300/60 shadow-sm",
          className,
        )}
      >
        {original}
      </mark>,
    );
    cursor = found + needle.length;
    matchIndex += 1;
  }
  return <>{segments}</>;
}
