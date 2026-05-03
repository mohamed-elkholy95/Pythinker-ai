import { useState } from "react";
import { ImageIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { HighlightedText } from "@/components/HighlightedText";
import { ImageLightbox } from "@/components/ImageLightbox";
import { MarkdownText } from "@/components/MarkdownText";
import { MessageActions } from "@/components/MessageActions";
import { MessageTimestamp } from "@/components/MessageTimestamp";
import { ReasoningDrawer } from "@/components/ReasoningDrawer";
import { ToolTraceChips } from "@/components/ToolTraceChips";
import { useThreadSearch } from "@/components/ThreadSearchProvider";
import { Button } from "@/components/ui/button";
import { extractThinkBlocks } from "@/lib/extractThinkBlocks";
import { cn } from "@/lib/utils";
import type { UIImage, UIMessage } from "@/lib/types";

interface MessageBubbleProps {
  message: UIMessage;
  /** Re-run the most recent assistant turn from the trailing user message. */
  onRegenerate?: () => void;
  /** Rewrite a user bubble in place and resubmit from there. */
  onEdit?: (messageId: string, newContent: string) => void;
}

/**
 * Render a single message. Following agent-chat-ui: user turns are a rounded
 * "pill" right-aligned with a muted fill; assistant turns render as bare
 * markdown so prose/code read like a document rather than a chat bubble.
 * Each turn fades+slides in for a touch of motion polish.
 *
 * Trace rows (tool-call hints, progress breadcrumbs) render as a subdued
 * collapsible group so intermediate steps never masquerade as replies.
 */
export function MessageBubble({
  message,
  onRegenerate,
  onEdit,
}: MessageBubbleProps) {
  const baseAnim =
    "motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-300";
  // Hook must be unconditional — declared before any role/kind branch returns.
  const [isEditing, setIsEditing] = useState(false);
  const search = useThreadSearch();
  const query = search?.query ?? "";
  const activeMatchId = search?.activeMatchId ?? undefined;
  const idPrefix = `bubble-${message.id}`;

  if (message.kind === "trace") {
    return <TraceGroup message={message} animClass={baseAnim} />;
  }

  if (message.role === "user") {
    const images = message.images ?? [];
    const hasImages = images.length > 0;
    const hasText = message.content.trim().length > 0;
    return (
      <div
        className={cn(
          "group ml-auto flex max-w-[min(90%,40rem)] flex-col items-end gap-1",
          baseAnim,
        )}
      >
        {isEditing ? (
          <UserInlineEditor
            initial={message.content}
            onSave={(text) => {
              setIsEditing(false);
              onEdit?.(message.id, text);
            }}
            onCancel={() => setIsEditing(false)}
          />
        ) : (
          <>
            {hasImages ? <UserImages images={images} /> : null}
            {hasText ? (
              <p
                className={cn(
                  "ml-auto w-fit rounded-[14px] rounded-br-[4px] border border-border/60 bg-card p-3",
                  "text-left text-[15px] leading-relaxed whitespace-pre-wrap break-words",
                )}
              >
                <HighlightedText
                  text={message.content}
                  query={query}
                  idPrefix={`${idPrefix}-user`}
                  activeMatchId={activeMatchId}
                />
              </p>
            ) : null}
            <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
              <MessageActions
                role="user"
                text={message.content}
                onRegenerate={() => {}}
                onEdit={() => setIsEditing(true)}
              />
              <MessageTimestamp createdAt={message.createdAt} className="mr-1" />
            </div>
          </>
        )}
      </div>
    );
  }

  // Strip <think>...</think> blocks out of assistant turns into a collapsible
  // drawer above the visible answer. Copy/regenerate target only the visible
  // portion so chain-of-thought never lands on the user's clipboard.
  const { reasoning, visible } = extractThinkBlocks(message.content);
  // Keep the "typing dots" placeholder visible while the model is still in
  // reasoning mode (i.e. only ``<think>...</think>`` content has arrived so
  // far). For reasoning models — DeepSeek-R1, Claude with extended thinking,
  // MiniMax reasoning_split, Volcengine thinking — the first deltas can be
  // chain-of-thought; gating on raw ``content`` would make the dots vanish
  // the moment any reasoning token landed, leaving the user staring at an
  // empty bubble until the actual answer began. Gating on ``visible`` keeps
  // the indicator up until real answer text starts streaming, while the
  // reasoning drawer renders alongside so the user can watch reasoning roll
  // in if they want.
  const noVisibleYet = visible.trim().length === 0;
  if (noVisibleYet && message.isStreaming) {
    return (
      <div
        className={cn("w-full text-sm", baseAnim)}
        style={{ lineHeight: "var(--cjk-line-height)" }}
      >
        <ReasoningDrawer reasoning={reasoning} />
        <div className="flex items-center gap-2">
          <TypingDots />
          <LatencySubscript latencyMs={message.latencyMs} />
        </div>
      </div>
    );
  }
  // Final safety net for tool-pivot turns that escaped the stream/message
  // filters (e.g. history-replayed legacy messages persisted before the
  // backend strip_think landed). A bare reasoning pill with no answer text
  // adds visual noise between the user's question and the real reply.
  if (noVisibleYet) {
    return null;
  }
  return (
    <div
      className={cn("group w-full text-[15px]", baseAnim)}
      style={{ lineHeight: "var(--cjk-line-height)" }}
    >
      <ReasoningDrawer reasoning={reasoning} />
      <MarkdownText
        query={query}
        activeMatchId={activeMatchId}
        idPrefix={`${idPrefix}-md`}
        isStreaming={message.isStreaming === true}
      >
        {visible}
      </MarkdownText>
      {message.isStreaming && <StreamCursor />}
      {!message.isStreaming && (
        <div className="mt-1 flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
          <MessageActions
            role="assistant"
            text={visible}
            onRegenerate={onRegenerate ?? (() => {})}
            onEdit={() => {}}
          />
          <MessageTimestamp createdAt={message.createdAt} />
        </div>
      )}
    </div>
  );
}

/**
 * Inline editor for a user turn. Replaces the bubble pill with a textarea
 * sized to the original message; Save commits the rewrite and triggers a
 * resubmission from this point in the thread, Cancel just exits edit mode.
 */
function UserInlineEditor({
  initial,
  onSave,
  onCancel,
}: {
  initial: string;
  onSave: (text: string) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const [value, setValue] = useState(initial);
  return (
    <div className="ml-auto flex w-full max-w-[min(85%,36rem)] flex-col items-end gap-1.5">
      <textarea
        autoFocus
        value={value}
        onChange={(e) => setValue(e.target.value)}
        rows={Math.max(1, Math.min(8, initial.split("\n").length))}
        className={cn(
          "w-full resize-none rounded-[18px] border border-border/60 bg-secondary/70 px-4 py-2",
          "text-left text-[18px]/[1.8] whitespace-pre-wrap break-words",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
      />
      <div className="flex items-center gap-2">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onCancel}
        >
          {t("actions.cancelEdit")}
        </Button>
        <Button
          type="button"
          size="sm"
          onClick={() => onSave(value.trim())}
          disabled={!value.trim() || value.trim() === initial.trim()}
        >
          {t("actions.saveEdit")}
        </Button>
      </div>
    </div>
  );
}

/**
 * Right-aligned preview row for images attached to a user turn.
 *
 * Visual follows agent-chat-ui: a single wrapping row of fixed-size square
 * thumbnails that stay modest next to the text pill regardless of how many
 * images are attached.
 *
 * The URL is expected to be a self-contained ``data:`` URL (the Composer
 * hands the normalized base64 payload to the optimistic bubble so that the
 * preview survives React StrictMode double-mount — blob URLs would be
 * revoked by the Composer's cleanup before remount). Historical replays
 * have no URL (the backend strips data URLs before persisting), so we
 * render a labelled placeholder tile instead of a broken ``<img>``.
 */
function UserImages({ images }: { images: UIImage[] }) {
  const { t } = useTranslation();
  // Only real-URL images can open in the lightbox; historical-replay
  // placeholders (no URL) have nothing to zoom into.
  const viewable = images
    .map((img, i) => ({ img, i }))
    .filter(({ img }) => typeof img.url === "string" && img.url.length > 0);
  const viewableImages = viewable.map(({ img }) => img);
  const originalToViewable = new Map<number, number>(
    viewable.map(({ i }, v) => [i, v]),
  );

  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);

  return (
    <>
      <div className="ml-auto flex flex-wrap items-end justify-end gap-2">
        {images.map((img, i) => (
          <UserImageCell
            key={`${img.url ?? "placeholder"}-${i}`}
            image={img}
            placeholderLabel={t("message.imageAttachment")}
            openLabel={t("lightbox.open")}
            onOpen={
              originalToViewable.has(i)
                ? () => setLightboxIndex(originalToViewable.get(i)!)
                : undefined
            }
          />
        ))}
      </div>
      <ImageLightbox
        images={viewableImages}
        index={lightboxIndex}
        onIndexChange={setLightboxIndex}
        onOpenChange={(open) => {
          if (!open) setLightboxIndex(null);
        }}
      />
    </>
  );
}

function UserImageCell({
  image,
  placeholderLabel,
  openLabel,
  onOpen,
}: {
  image: UIImage;
  placeholderLabel: string;
  openLabel: string;
  onOpen?: () => void;
}) {
  const hasUrl = typeof image.url === "string" && image.url.length > 0;
  const tileClasses = cn(
    "relative h-24 w-24 overflow-hidden rounded-[14px] border border-border/60 bg-muted/40",
    "shadow-[0_6px_18px_-14px_rgba(0,0,0,0.45)]",
  );

  if (hasUrl && onOpen) {
    return (
      <button
        type="button"
        onClick={onOpen}
        aria-label={image.name ? `${openLabel}: ${image.name}` : openLabel}
        title={image.name ?? undefined}
        className={cn(
          tileClasses,
          "cursor-zoom-in transition-transform duration-150 motion-reduce:transition-none",
          "hover:scale-[1.02] hover:ring-2 hover:ring-primary/30",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50",
        )}
      >
        <img
          src={image.url}
          alt={image.name ?? ""}
          loading="lazy"
          decoding="async"
          draggable={false}
          className="h-full w-full object-cover"
        />
      </button>
    );
  }

  return (
    <div className={tileClasses} title={image.name ?? undefined}>
      <div
        className="flex h-full w-full flex-col items-center justify-center gap-1 px-2 text-[11px] text-muted-foreground"
        aria-label={placeholderLabel}
      >
        <ImageIcon className="h-4 w-4 flex-none" aria-hidden />
        <span className="line-clamp-2 text-center leading-tight">
          {image.name ?? placeholderLabel}
        </span>
      </div>
    </div>
  );
}

/** Blinking cursor appended at the end of streaming text. */
function StreamCursor() {
  const { t } = useTranslation();
  return (
    <span
      aria-label={t("message.streaming")}
      className={cn(
        "ml-0.5 inline-block h-[1em] w-[3px] translate-y-[2px] align-middle",
        "rounded-sm bg-foreground/70 motion-safe:animate-pulse",
      )}
    />
  );
}

/**
 * "thinking… Ns" subscript shown next to the typing dots once the wait
 * crosses one second. Hidden below 1s to avoid visual flicker on snappy
 * turns; null when the placeholder hasn't been stamped with a latency yet.
 */
function LatencySubscript({ latencyMs }: { latencyMs?: number }) {
  const { t } = useTranslation();
  if (latencyMs === undefined || latencyMs < 1_000) return null;
  const seconds = Math.floor(latencyMs / 1_000);
  return (
    <span className="text-[11px] text-muted-foreground/70 tabular-nums">
      {t("latency.thinking", { seconds })}
    </span>
  );
}

/** Pre-token-arrival placeholder: three bouncing dots. */
function TypingDots() {
  const { t } = useTranslation();
  return (
    <span
      aria-label={t("message.assistantTyping")}
      className="inline-flex items-center gap-1 py-1"
    >
      <Dot delay="0ms" />
      <Dot delay="150ms" />
      <Dot delay="300ms" />
    </span>
  );
}

function Dot({ delay }: { delay: string }) {
  return (
    <span
      style={{ animationDelay: delay }}
      className={cn(
        "inline-block h-1.5 w-1.5 rounded-full bg-muted-foreground/60",
        "motion-safe:animate-bounce",
      )}
    />
  );
}

interface TraceGroupProps {
  message: UIMessage;
  animClass: string;
}

/**
 * Compact tool-call summary: one chip per unique tool kind, with an expand
 * toggle to reveal the full trace lines underneath.
 */
function TraceGroup({ message, animClass }: TraceGroupProps) {
  const lines = message.traces ?? [message.content];
  return (
    <div className={cn("w-full px-1", animClass)}>
      <ToolTraceChips traces={lines} />
    </div>
  );
}
