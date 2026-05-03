import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { CommandRow } from "@/lib/api";
import { cn } from "@/lib/utils";

interface CommandPaletteProps {
  /** Controlled open flag. The composer owns this so it can close on space etc. */
  open: boolean;
  /** Full command list (already fetched by ``useCommands``). */
  commands: CommandRow[];
  /** Current filter text — the substring after the leading ``/`` in the textarea. */
  query: string;
  /** Fired when the user picks a row (Enter / Tab / click). */
  onSelect: (name: string) => void;
  /** Fired on Esc / click-outside. */
  onClose: () => void;
  /** Used to anchor the popover above the composer textarea. */
  anchorRef: React.RefObject<HTMLElement>;
}

/**
 * Slash-command palette mounted while the textarea content starts with ``/``.
 *
 * Keymap:
 *   Up / Down — move highlight, wrap at edges
 *   Enter / Tab — fill the textarea with the highlighted command + a space
 *   Esc — close without changes
 *   Click outside — close (handled by parent via the open flag)
 */
export function CommandPalette({
  open,
  commands,
  query,
  onSelect,
  onClose,
  anchorRef,
}: CommandPaletteProps) {
  const { t } = useTranslation();
  const listRef = useRef<HTMLUListElement>(null);
  const [highlight, setHighlight] = useState(0);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter(
      (c) =>
        c.name.toLowerCase().includes(q) ||
        c.summary.toLowerCase().includes(q),
    );
  }, [commands, query]);

  // Reset highlight when the filtered list changes so we never point past its end.
  useEffect(() => {
    setHighlight(0);
  }, [query, filtered.length]);

  // Click-outside detection. The composer owns ``onClose`` so the textarea's
  // own clicks (which arrive *before* this listener via React's synthetic
  // bubble) do not race with re-opening the popover.
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node | null;
      if (!target) return;
      if (listRef.current?.contains(target)) return;
      if (anchorRef.current?.contains(target)) return;
      onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open, onClose, anchorRef]);

  if (!open) return null;

  const onKeyDown = (e: React.KeyboardEvent<HTMLUListElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => (filtered.length === 0 ? 0 : (h + 1) % filtered.length));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) =>
        filtered.length === 0 ? 0 : (h - 1 + filtered.length) % filtered.length,
      );
      return;
    }
    if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      const row = filtered[highlight];
      if (row) onSelect(row.name);
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  };

  return (
    <ul
      ref={listRef}
      role="listbox"
      tabIndex={-1}
      onKeyDown={onKeyDown}
      // Anchored above the textarea via ``bottom-full`` + the parent's ``relative``.
      className={cn(
        "absolute bottom-full left-0 right-0 z-30 mb-2 max-h-[18rem] overflow-y-auto",
        "rounded-xl border border-border/70 bg-popover p-1 shadow-lg",
      )}
      aria-label={t("commands.placeholder")}
    >
      {filtered.length === 0 ? (
        <li className="px-3 py-2 text-[12px] text-muted-foreground">
          {t("commands.empty")}
        </li>
      ) : (
        filtered.map((cmd, i) => (
          <li
            key={cmd.name}
            role="option"
            aria-selected={i === highlight}
            onMouseEnter={() => setHighlight(i)}
            onClick={() => onSelect(cmd.name)}
            className={cn(
              "flex cursor-pointer items-baseline justify-between gap-3 rounded-md px-2.5 py-1.5",
              i === highlight ? "bg-accent text-accent-foreground" : "text-foreground",
            )}
          >
            <span className="font-mono text-[12px]">{cmd.name}</span>
            <span className="truncate text-[11.5px] text-muted-foreground">
              {cmd.summary}
            </span>
          </li>
        ))
      )}
    </ul>
  );
}
