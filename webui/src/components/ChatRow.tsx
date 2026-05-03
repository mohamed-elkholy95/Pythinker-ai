import {
  Archive,
  ArchiveRestore,
  MoreHorizontal,
  Pin,
  PinOff,
  Trash2,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { chatIconFor } from "@/lib/chatIcon";
import { relativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { ChatSummary } from "@/lib/types";

interface ChatRowProps {
  session: ChatSummary;
  active: boolean;
  title: string;
  onSelect: (key: string) => void;
  onRequestDelete: (key: string, label: string) => void;
  /** When omitted, the Pin / Unpin entry is hidden — keeps the dropdown
   * compatible with callers that haven't wired the new mutation yet. */
  onTogglePin?: (key: string) => void;
  /** Same fallback contract as ``onTogglePin`` for archive. */
  onToggleArchive?: (key: string) => void;
}

export function ChatRow({
  session,
  active,
  title,
  onSelect,
  onRequestDelete,
  onTogglePin,
  onToggleArchive,
}: ChatRowProps) {
  const { t } = useTranslation();
  const { Icon: ChatIcon, bgClass, fgClass } = chatIconFor(session.key);
  return (
    <div
      className={cn(
        "group flex items-center gap-2 rounded-md px-2 py-1.5 text-[12.5px] transition-colors",
        active
          ? "bg-sidebar-accent/80 text-sidebar-accent-foreground dark:text-white shadow-[inset_0_0_0_1px_hsl(var(--border)/0.4)]"
          : "text-sidebar-foreground/88 hover:bg-sidebar-accent/45",
      )}
    >
      <button
        type="button"
        onClick={() => onSelect(session.key)}
        className="flex min-w-0 flex-1 items-center gap-2 text-left"
      >
        <span
          className={cn(
            "flex h-7 w-7 flex-none items-center justify-center rounded-lg",
            bgClass,
          )}
          aria-hidden
        >
          <ChatIcon className={cn("h-3.5 w-3.5", fgClass)} />
        </span>
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="flex w-full items-center gap-1 truncate font-medium leading-5">
            {session.pinned ? (
              <Pin
                className="h-3 w-3 flex-none text-muted-foreground"
                aria-hidden
              />
            ) : null}
            <span className="truncate">{title}</span>
          </span>
          <span
            className={cn(
              "text-[11px] font-normal tabular-nums",
              // Active rows inherit ``text-sidebar-accent-foreground`` from
              // the parent button. Inheriting at full opacity is too loud
              // (timestamps are subordinate to titles), but at <70% the
              // dark-mode green-on-white path lost legibility. ~85% keeps
              // the meta subordinate while staying readable in both themes
              // for both active and inactive rows.
              active ? "text-current/85" : "opacity-65",
            )}
          >
            {relativeTime(session.updatedAt ?? session.createdAt) || "—"}
          </span>
        </span>
      </button>
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger
          className={cn(
            "inline-flex h-6 w-6 items-center justify-center rounded-md opacity-0 transition-opacity",
            active
              ? "text-current opacity-100 hover:bg-sidebar-accent/60"
              : "text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground group-hover:opacity-100",
            "focus-visible:opacity-100",
          )}
          aria-label={t("chat.actions", { title })}
        >
          <MoreHorizontal className="h-4 w-4" />
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          onCloseAutoFocus={(event) => event.preventDefault()}
        >
          {onTogglePin ? (
            <DropdownMenuItem onSelect={() => onTogglePin(session.key)}>
              {session.pinned ? (
                <PinOff className="mr-2 h-4 w-4" />
              ) : (
                <Pin className="mr-2 h-4 w-4" />
              )}
              {session.pinned ? t("chat.unpin") : t("chat.pin")}
            </DropdownMenuItem>
          ) : null}
          {onToggleArchive ? (
            <DropdownMenuItem
              onSelect={() => onToggleArchive(session.key)}
            >
              {session.archived ? (
                <ArchiveRestore className="mr-2 h-4 w-4" />
              ) : (
                <Archive className="mr-2 h-4 w-4" />
              )}
              {session.archived ? t("chat.unarchive") : t("chat.archive")}
            </DropdownMenuItem>
          ) : null}
          {onTogglePin || onToggleArchive ? <DropdownMenuSeparator /> : null}
          <DropdownMenuItem
            onSelect={() => {
              window.setTimeout(() => onRequestDelete(session.key, title), 0);
            }}
            className="text-destructive focus:text-destructive"
          >
            <Trash2 className="mr-2 h-4 w-4" />
            {t("chat.delete")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
