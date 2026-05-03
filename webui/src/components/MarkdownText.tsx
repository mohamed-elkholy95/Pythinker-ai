import { Suspense, lazy } from "react";

import { cn } from "@/lib/utils";

interface MarkdownTextProps {
  children: string;
  className?: string;
  /** Optional in-chat search query. When non-empty, prose-bearing slots
   * (``p``, ``li``, ``em``, ``strong``, headings, ``blockquote``) wrap their
   * direct string children in ``<HighlightedText>``. */
  query?: string;
  /** Match id currently focused; passed through to ``HighlightedText``. */
  activeMatchId?: string;
  /** Stable id prefix used to derive each match's ``data-match-id``. Required
   * when ``query`` is provided so ids stay unique across bubbles. */
  idPrefix?: string;
  /** When true, the Streamdown renderer animates per-word fade-in for new
   * content and gracefully styles unterminated bold/italic/code-fence blocks
   * mid-stream. Should track the message's ``isStreaming`` flag. */
  isStreaming?: boolean;
}

const loadMarkdownRenderer = () => import("@/components/MarkdownTextRenderer");
const LazyMarkdownRenderer = lazy(loadMarkdownRenderer);

export function preloadMarkdownText(): void {
  void loadMarkdownRenderer();
}

/**
 * Lightweight markdown renderer mirroring agent-chat-ui: GFM + math via
 * ``remark-math`` / ``rehype-katex``, and fenced code blocks delegated to
 * ``CodeBlock`` for copy-to-clipboard and syntax highlighting.
 */
export function MarkdownText({
  children,
  className,
  query,
  activeMatchId,
  idPrefix,
  isStreaming,
}: MarkdownTextProps) {
  return (
    <Suspense
      fallback={
        <div
          className={cn(
            "whitespace-pre-wrap break-words leading-relaxed text-foreground/92",
            className,
          )}
        >
          {children}
        </div>
      }
    >
      <LazyMarkdownRenderer
        className={className}
        query={query}
        activeMatchId={activeMatchId}
        idPrefix={idPrefix}
        isStreaming={isStreaming}
      >
        {children}
      </LazyMarkdownRenderer>
    </Suspense>
  );
}
