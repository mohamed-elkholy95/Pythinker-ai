import { useTranslation } from "react-i18next";

import { HighlightedText } from "@/components/HighlightedText";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import type { SearchHit } from "@/lib/types";

export interface SearchResultsProps {
  hits: SearchHit[];
  query: string;
  loading: boolean;
  hasMore: boolean;
  onLoadMore: () => void;
  onSelect: (hit: SearchHit) => void;
}

/** Flat result list rendered when the cross-chat search has a non-empty
 * query. Each row shows the chat title plus a snippet with the query
 * highlighted via the existing ``HighlightedText`` (matching the in-chat
 * finder). The list is virtualization-free — the API caps results at
 * ``PAGE_SIZE`` (50) and a "Load more" button drives pagination through
 * ``useSessionSearch.loadMore``. */
export function SearchResults({
  hits,
  query,
  loading,
  hasMore,
  onLoadMore,
  onSelect,
}: SearchResultsProps) {
  const { t } = useTranslation();
  if (!loading && hits.length === 0) {
    return (
      <div className="px-3 py-6 text-xs text-muted-foreground">
        {t("search.noSidebarResults")}
      </div>
    );
  }
  return (
    <ScrollArea className="h-full">
      <ul className="space-y-0.5 px-2 py-1">
        {hits.map((hit, idx) => (
          <li key={`${hit.sessionKey}-${hit.messageIndex}-${idx}`}>
            <button
              type="button"
              onClick={() => onSelect(hit)}
              className={cn(
                "flex w-full flex-col items-start gap-0.5 rounded-md px-2 py-1.5 text-left",
                "text-[12.5px] transition-colors hover:bg-sidebar-accent/45",
              )}
            >
              <span className="flex w-full items-center gap-1 truncate font-medium leading-5">
                <span className="truncate">{hit.title || hit.sessionKey}</span>
                {hit.archived ? (
                  <span className="ml-1 rounded-full bg-secondary/60 px-1.5 py-[1px] text-[10px] uppercase tracking-wide text-muted-foreground">
                    {t("search.archivedChip")}
                  </span>
                ) : null}
              </span>
              <span className="line-clamp-2 text-[11.5px] text-muted-foreground/85">
                <HighlightedText
                  text={hit.snippet}
                  query={query}
                  idPrefix={`hit-${hit.sessionKey}-${hit.messageIndex}-${idx}`}
                />
              </span>
            </button>
          </li>
        ))}
      </ul>
      {hasMore ? (
        <div className="px-2 py-2">
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-center"
            onClick={onLoadMore}
            disabled={loading}
          >
            {t("search.loadMore")}
          </Button>
        </div>
      ) : null}
    </ScrollArea>
  );
}
