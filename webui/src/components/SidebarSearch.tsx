import { Search, X } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { SearchResults } from "@/components/SearchResults";
import { Button } from "@/components/ui/button";
import { useSessionSearch } from "@/hooks/useSessionSearch";
import { cn } from "@/lib/utils";
import type { SearchHit } from "@/lib/types";

interface SidebarSearchProps {
  /** Click handler for individual hit rows. The parent translates the hit
   * into a session selection + (eventually) scroll-to-message action. */
  onSelectHit: (location: { sessionKey: string; messageIndex: number }) => void;
  /** The sidebar's normal section list, rendered when the query is empty.
   * Wrapping it as ``children`` keeps the swap local to this component so
   * the parent doesn't need a separate ``searchActive`` flag. */
  children?: ReactNode;
  className?: string;
}

/** Cross-chat search box pinned above the sidebar's "Recent" section. The
 * actual fetch / debounce / pagination lives in ``useSessionSearch``; this
 * component is the thin presentation layer that owns the input, swaps
 * children for the result panel when the query is active, and bubbles
 * clicks back up to the parent (which knows how to flip the active
 * session). */
export function SidebarSearch({
  onSelectHit,
  children,
  className,
}: SidebarSearchProps) {
  const { t } = useTranslation();
  const { query, setQuery, hits, loading, hasMore, loadMore } =
    useSessionSearch();
  const active = query.trim().length > 0;

  const handle = (hit: SearchHit) =>
    onSelectHit({ sessionKey: hit.sessionKey, messageIndex: hit.messageIndex });

  return (
    <div className={cn("flex min-h-0 flex-1 flex-col", className)}>
      <div className="flex items-center gap-1 px-2 pb-2">
        <div
          className={cn(
            "flex h-7 flex-1 items-center gap-1 rounded-md border border-border/60",
            "bg-card/30 px-2 text-[12.5px] focus-within:border-border",
          )}
        >
          <Search className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("search.sidebarPlaceholder")}
            aria-label={t("search.sidebarPlaceholder")}
            className="h-6 flex-1 bg-transparent outline-none placeholder:text-muted-foreground/60"
          />
          {active ? (
            <Button
              variant="ghost"
              size="icon"
              className="h-5 w-5"
              aria-label={t("search.close")}
              onClick={() => setQuery("")}
            >
              <X className="h-3 w-3" />
            </Button>
          ) : null}
        </div>
      </div>
      {active ? (
        <div className="flex-1 overflow-hidden">
          <SearchResults
            hits={hits}
            query={query}
            loading={loading}
            hasMore={hasMore}
            onLoadMore={loadMore}
            onSelect={handle}
          />
        </div>
      ) : (
        children
      )}
    </div>
  );
}
