import { ChevronDown, ChevronUp, Search, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

import { useThreadSearch } from "@/components/ThreadSearchProvider";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface InChatSearchProps {
  open: boolean;
  onClose: () => void;
  className?: string;
}

/** Floating overlay docked at the top of the thread. ⌘F-driven. */
export function InChatSearch({ open, onClose, className }: InChatSearchProps) {
  const { t } = useTranslation();
  const search = useThreadSearch();
  const inputRef = useRef<HTMLInputElement>(null);

  // Esc closes; arrows step matches; Enter goes to next match.
  useEffect(() => {
    if (!open) return;
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") {
        ev.preventDefault();
        onClose();
        return;
      }
      // Only handle arrows when the search input itself is focused — the
      // user might be reading the thread with the overlay open.
      if (document.activeElement !== inputRef.current) return;
      if (ev.key === "ArrowDown" || (ev.key === "Enter" && !ev.shiftKey)) {
        ev.preventDefault();
        search?.next();
      } else if (ev.key === "ArrowUp" || (ev.key === "Enter" && ev.shiftKey)) {
        ev.preventDefault();
        search?.prev();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, search]);

  // Focus the input when opening; reset state on the open->closed transition.
  // Tracking previous-open avoids re-running ``reset()`` every time the search
  // context value changes (which would loop forever, since ``reset`` itself
  // mutates that context).
  const prevOpen = useRef(false);
  useEffect(() => {
    if (open && !prevOpen.current) {
      inputRef.current?.focus();
    } else if (!open && prevOpen.current && search) {
      search.reset();
    }
    prevOpen.current = open;
  }, [open, search]);

  // Scroll the active match into view whenever it changes.
  useEffect(() => {
    if (!search?.activeMatchId) return;
    const el = document.querySelector(
      `[data-match-id="${CSS.escape(search.activeMatchId)}"]`,
    );
    if (el instanceof HTMLElement) {
      el.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [search?.activeMatchId]);

  if (!open || !search) return null;

  const total = search.matchIds.length;
  const current =
    search.activeMatchId !== null && total > 0
      ? search.matchIds.indexOf(search.activeMatchId) + 1
      : 0;

  return (
    <div
      className={cn(
        "absolute right-3 top-2 z-30 flex items-center gap-1 rounded-full",
        "border border-border/60 bg-background/95 px-2 py-1 shadow-md backdrop-blur",
        className,
      )}
      role="search"
    >
      <Search className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
      <input
        ref={inputRef}
        type="search"
        value={search.query}
        onChange={(e) => search.setQuery(e.target.value)}
        placeholder={t("search.placeholder")}
        aria-label={t("search.placeholder")}
        className="h-6 w-44 bg-transparent text-[13px] outline-none placeholder:text-muted-foreground/70"
      />
      <span className="min-w-[3.5rem] text-[11px] tabular-nums text-muted-foreground">
        {total === 0 && search.query
          ? t("search.noResults")
          : t("search.matchOf", { current, total })}
      </span>
      <Button
        variant="ghost"
        size="icon"
        className="h-6 w-6"
        aria-label={t("search.prev")}
        onClick={() => search.prev()}
        disabled={total === 0}
      >
        <ChevronUp className="h-3.5 w-3.5" />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className="h-6 w-6"
        aria-label={t("search.next")}
        onClick={() => search.next()}
        disabled={total === 0}
      >
        <ChevronDown className="h-3.5 w-3.5" />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className="h-6 w-6"
        aria-label={t("search.close")}
        onClick={onClose}
      >
        <X className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}
