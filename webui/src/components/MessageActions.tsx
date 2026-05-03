import { Check, Copy, Pencil, RotateCcw } from "lucide-react";
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface MessageActionsProps {
  role: "user" | "assistant";
  text: string;
  onCopy?: () => void;
  onRegenerate: () => void;
  onEdit: () => void;
  className?: string;
}

/**
 * Hover-revealed action toolbar attached to every persisted message bubble.
 *
 * - Copy is shown on both user and assistant rows.
 * - Regenerate is assistant-only (re-runs the prior user turn via the hook).
 * - Edit is user-only (rewrites the user message and re-runs from there).
 *
 * Visibility is driven by ``group-hover`` / ``focus-within`` on the parent
 * bubble so the toolbar is unobtrusive but always discoverable via Tab.
 */
export function MessageActions({
  role,
  text,
  onCopy,
  onRegenerate,
  onEdit,
  className,
}: MessageActionsProps) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text);
      onCopy?.();
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      // Clipboard write can fail in non-secure contexts; swallow silently —
      // the user can still select-copy by hand.
    }
  }, [text, onCopy]);

  const isAssistant = role === "assistant";
  // Compact override of the project's icon button. The default icon size is
  // h-9/w-9; the toolbar wants something less obtrusive next to a bubble.
  const iconBtn =
    "h-7 w-7 text-muted-foreground/80 hover:bg-secondary/60 hover:text-foreground";

  return (
    <div
      className={cn(
        "flex items-center gap-0.5 opacity-0 transition-opacity",
        "group-hover:opacity-100 focus-within:opacity-100",
        className,
      )}
    >
      <Button
        type="button"
        variant="ghost"
        size="icon"
        aria-label={t("actions.copy")}
        title={t("actions.copy")}
        onClick={handleCopy}
        className={iconBtn}
      >
        {copied ? <Check size={14} /> : <Copy size={14} />}
      </Button>
      {isAssistant ? (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={t("actions.regenerate")}
          title={t("actions.regenerate")}
          onClick={onRegenerate}
          className={iconBtn}
        >
          <RotateCcw size={14} />
        </Button>
      ) : (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={t("actions.edit")}
          title={t("actions.edit")}
          onClick={onEdit}
          className={iconBtn}
        >
          <Pencil size={14} />
        </Button>
      )}
    </div>
  );
}
