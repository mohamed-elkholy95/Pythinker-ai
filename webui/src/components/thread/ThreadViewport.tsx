import { type ReactNode, useEffect } from "react";
import { ArrowDown } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useStickToBottom } from "use-stick-to-bottom";

import { ThreadMessages } from "@/components/thread/ThreadMessages";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { UIMessage } from "@/lib/types";

interface ThreadViewportProps {
  messages: UIMessage[];
  isStreaming: boolean;
  composer: ReactNode;
  emptyState?: ReactNode;
  /** Re-run the last assistant turn from the trailing user message. */
  onRegenerate?: () => void;
  /** Rewrite a user bubble in place and resubmit from there. */
  onEdit?: (messageId: string, newContent: string) => void;
  /** One-shot scroll target — when set, scroll the bubble at
   * ``messageIndex`` into view. ``token`` is bumped each time the same
   * target is re-requested so the effect refires on repeat clicks. */
  scrollTarget?: { messageIndex: number; token: number } | null;
}

export function ThreadViewport({
  messages,
  isStreaming: _isStreaming,
  composer,
  emptyState,
  onRegenerate,
  onEdit,
  scrollTarget,
}: ThreadViewportProps) {
  const { t } = useTranslation();
  const hasMessages = messages.length > 0;
  // ``use-stick-to-bottom`` replaces our hand-rolled scrollTo logic. It uses a
  // ResizeObserver to follow content reflow during streaming with a spring
  // animation, distinguishes user scroll-up from animated scrolls (so a single
  // upward flick cleanly disengages "follow latest"), and re-engages once the
  // user scrolls back to the bottom. ``isAtBottom`` drives the floating
  // "scroll to bottom" pill below.
  const {
    scrollRef,
    contentRef,
    isAtBottom,
    scrollToBottom,
  } = useStickToBottom({
    initial: "instant",
    resize: "smooth",
    damping: 0.7,
    stiffness: 0.05,
    mass: 1.25,
  });
  // ``_isStreaming`` is intentionally unused here — content reflow is detected
  // via ResizeObserver inside ``useStickToBottom`` so we no longer need to
  // schedule scrolls manually on each delta.
  void _isStreaming;

  // Honor a one-shot search-hit jump-to-message request. Skips the
  // auto-stick-to-bottom branch by calling the bubble's native scrollIntoView;
  // the user's "follow latest" intent re-engages once they scroll back to the
  // bottom of the thread.
  useEffect(() => {
    if (!scrollTarget) return;
    const el = scrollRef.current;
    if (!el) return;
    const target = el.querySelector<HTMLElement>(
      `[data-message-index="${scrollTarget.messageIndex}"]`,
    );
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    // Brief flash highlight so the user sees what was matched.
    target.classList.add("ring-2", "ring-amber-400/70", "transition-shadow");
    const id = window.setTimeout(() => {
      target.classList.remove("ring-2", "ring-amber-400/70");
    }, 1_500);
    return () => window.clearTimeout(id);
  }, [scrollTarget?.messageIndex, scrollTarget?.token, scrollRef]);

  return (
    <div className="relative flex min-h-0 flex-1 overflow-hidden">
      <div
        ref={scrollRef}
        className={cn(
          "absolute inset-0 overflow-y-auto scrollbar-thin",
          "[&::-webkit-scrollbar]:w-1.5",
          "[&::-webkit-scrollbar-thumb]:rounded-full",
          "[&::-webkit-scrollbar-thumb]:bg-muted-foreground/30",
          "[&::-webkit-scrollbar-track]:bg-transparent",
        )}
      >
        <div ref={contentRef}>
          {hasMessages ? (
            <div className="mx-auto flex min-h-full w-full max-w-[64rem] flex-col">
              <div className="flex-1 px-5 pb-36 pt-4 sm:px-6 md:px-8">
                <ThreadMessages
                  messages={messages}
                  onRegenerate={onRegenerate}
                  onEdit={onEdit}
                />
              </div>

              <div
                className={cn(
                  "sticky bottom-0 z-10 mt-auto",
                  "bg-gradient-to-t from-background via-background/95 to-transparent",
                  "pt-6",
                )}
              >
                <div className="px-5 pb-3 sm:px-6 md:px-8">
                  {composer}
                </div>
              </div>
            </div>
          ) : (
            <div className="mx-auto flex min-h-full w-full max-w-[64rem] flex-col px-5 sm:px-6 md:px-8">
              <div className="flex w-full flex-1 justify-center pb-16 pt-14 md:pt-[3.5rem]">
                <div className="flex w-full max-w-[40rem] flex-col gap-5">
                  {emptyState}
                  <div className="w-full">{composer}</div>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-6 bg-gradient-to-b from-background to-transparent"
      />

      {!isAtBottom && (
        <Button
          variant="outline"
          size="icon"
          onClick={() => void scrollToBottom("smooth")}
          className={cn(
            "absolute bottom-28 left-1/2 h-8 w-8 -translate-x-1/2 rounded-full shadow-md",
            "bg-background/90 backdrop-blur",
            "animate-in fade-in-0 zoom-in-95",
          )}
          aria-label={t("thread.scrollToBottom")}
        >
          <ArrowDown className="h-4 w-4" />
        </Button>
      )}
    </div>
  );
}
