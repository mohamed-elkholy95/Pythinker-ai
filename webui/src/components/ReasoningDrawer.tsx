import { ChevronRight } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

interface ReasoningDrawerProps {
  reasoning: string;
  className?: string;
}

/**
 * Collapsed-by-default disclosure for a model's chain-of-thought. Renders
 * nothing when ``reasoning`` is empty so it stays out of the DOM for
 * non-reasoning models. The reasoning text is rendered as a plain
 * monospace block — no markdown, no syntax highlighting — to keep this
 * component cheap and to discourage encouraging users to over-trust
 * inline reasoning.
 */
export function ReasoningDrawer({ reasoning, className }: ReasoningDrawerProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (!reasoning) return null;

  return (
    <div className={cn("mb-2 flex flex-col gap-1", className)}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label={open ? t("reasoning.hide") : t("reasoning.show")}
        className={cn(
          "inline-flex w-fit items-center gap-1 rounded-md border border-border/40",
          "bg-secondary/30 px-2 py-0.5 text-[11px] text-muted-foreground",
          "hover:bg-secondary/50 hover:text-foreground",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
      >
        <ChevronRight
          size={11}
          className={cn(
            "transition-transform motion-reduce:transition-none",
            open ? "rotate-90" : "rotate-0",
          )}
        />
        <span>{t("reasoning.label")}</span>
      </button>
      {open ? (
        <pre
          className={cn(
            "ml-3 max-h-[40vh] overflow-auto rounded-md border border-border/40 bg-muted/40",
            "px-3 py-2 text-[12px] leading-5 font-mono whitespace-pre-wrap text-muted-foreground/90",
          )}
        >
          {reasoning}
        </pre>
      ) : null}
    </div>
  );
}
