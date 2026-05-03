import { PanelLeftOpen } from "lucide-react";
import { useTranslation } from "react-i18next";

import { UsagePill } from "@/components/UsagePill";
import { Button } from "@/components/ui/button";
import { useSessionUsage } from "@/hooks/useSessionUsage";
import { cn } from "@/lib/utils";

interface ThreadHeaderProps {
  title: string;
  onToggleSidebar: () => void;
  onGoHome: () => void;
  hideSidebarToggleOnDesktop?: boolean;
  chatId?: string | null;
}

export function ThreadHeader({
  title,
  onToggleSidebar,
  onGoHome,
  hideSidebarToggleOnDesktop = false,
  chatId,
}: ThreadHeaderProps) {
  const { t } = useTranslation();
  const usage = useSessionUsage(chatId ?? null);
  return (
    <div className="control-topbar relative z-10 flex min-h-[58px] items-center justify-between gap-3 px-4 py-2">
      <div className="relative flex min-w-0 items-center gap-2">
        <Button
          variant="ghost"
          size="icon"
          aria-label={t("thread.header.toggleSidebar")}
          onClick={onToggleSidebar}
          className={cn(
            "h-9 w-9 rounded-full border border-border/70 bg-card/65 text-muted-foreground shadow-sm hover:bg-accent/50 hover:text-foreground",
            hideSidebarToggleOnDesktop && "lg:pointer-events-none lg:opacity-0",
          )}
        >
          <PanelLeftOpen className="h-3.5 w-3.5" />
        </Button>
        <button
          type="button"
          aria-label={title}
          onClick={onGoHome}
          className="flex min-w-0 items-center gap-2 rounded-lg px-2 py-1 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-accent/45 hover:text-foreground"
        >
          <img
            src="/brand/icon.svg"
            alt=""
            className="h-4 w-4 rounded-[5px] opacity-85"
            aria-hidden
          />
          <span className="max-w-[min(60vw,32rem)] truncate">
            <span className="text-muted-foreground">Chat</span>
            <span className="px-1.5 text-muted-foreground/60">/</span>
            <span className="text-primary">{title}</span>
          </span>
        </button>
      </div>

      <UsagePill used={usage.used} limit={usage.limit} />

      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-full h-4" />
    </div>
  );
}
