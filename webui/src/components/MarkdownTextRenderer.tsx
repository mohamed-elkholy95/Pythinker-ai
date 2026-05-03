import type { ReactNode } from "react";
import { Streamdown } from "streamdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { CodeBlock } from "@/components/CodeBlock";
import { HighlightedText } from "@/components/HighlightedText";
import { useReducedMotion } from "@/hooks/useReducedMotion";
import { cn } from "@/lib/utils";

import "katex/dist/katex.min.css";
import "streamdown/styles.css";

interface MarkdownTextRendererProps {
  children: string;
  className?: string;
  query?: string;
  activeMatchId?: string;
  idPrefix?: string;
  /** Streamdown's per-word fade-in animation gating. Should track the
   * message's ``isStreaming`` flag so animation only runs while content is
   * arriving. */
  isStreaming?: boolean;
}

/**
 * Walk a Streamdown ``children`` payload and replace every direct string
 * child with a ``<HighlightedText>`` so substring matches of ``query`` are
 * wrapped in ``<mark>``. Non-string children (nested formatting like
 * ``<em>``, ``<code>``, links) pass through untouched so their own override
 * — or nested prose handler — gets a chance to highlight further down.
 *
 * No-op when ``query`` is falsy: returns the original ``children`` reference,
 * keeping React's reconciliation cheap on the common (no-search) path.
 */
function highlightChildren(
  children: ReactNode,
  query: string | undefined,
  activeMatchId: string | undefined,
  idPrefixFor: (textIndex: number) => string,
): ReactNode {
  if (!query) return children;
  const arr = Array.isArray(children) ? children : [children];
  return arr.map((c, i) => {
    if (typeof c === "string") {
      return (
        <HighlightedText
          key={`h${i}`}
          text={c}
          query={query}
          idPrefix={idPrefixFor(i)}
          activeMatchId={activeMatchId}
        />
      );
    }
    return c;
  });
}

/**
 * Heavy markdown stack (GFM, math, KaTeX, syntax highlighting) kept in a
 * separate chunk so the app shell can paint sooner on refresh.
 *
 * Backed by Streamdown — a drop-in react-markdown replacement built for AI
 * streaming. It splits content into blocks and memoizes each so only the
 * trailing block re-parses as deltas arrive (earlier blocks stay stable),
 * which is the main reason long messages stop flickering. It also handles
 * mid-stream unterminated bold/italic/inline-code/code-fences via the
 * ``remend`` preprocessor.
 */
export default function MarkdownTextRenderer({
  children,
  className,
  query,
  activeMatchId,
  idPrefix,
  isStreaming,
}: MarkdownTextRendererProps) {
  // Stable prefix for highlight match ids; falls back to a generic slug when
  // the caller didn't pass one (existing non-search call sites).
  const hlPrefix = idPrefix ?? "md";
  // Streamdown's per-word fade-in is injected via a rehype plugin that emits
  // inline transition styles, which bypass Tailwind's ``motion-safe:`` CSS
  // gate. Honor ``prefers-reduced-motion`` from JS so the OS setting actually
  // disables the animation.
  const reducedMotion = useReducedMotion();
  // Monotonic counter scoped to the current render. Each invocation of `hl`
  // claims a fresh slot (e.g. "p#0", "p#1", "li#2") so two sibling <p>s no
  // longer collide on `<HighlightedText>` ids — which would otherwise leave
  // every <p>'s first match sharing the same `data-match-id`.
  let slotCounter = 0;
  const hl = (kids: ReactNode, slot: string): ReactNode => {
    const slotId = `${slot}#${slotCounter++}`;
    return highlightChildren(
      kids,
      query,
      activeMatchId,
      (textIndex) => `${hlPrefix}-${slotId}-${textIndex}`,
    );
  };
  return (
    <div
      className={cn(
        "markdown-content prose prose-lg max-w-none dark:prose-invert",
        "prose-headings:mt-4 prose-headings:mb-2 prose-headings:font-semibold prose-headings:tracking-tight",
        "prose-h1:text-xl prose-h2:text-lg prose-h3:text-base prose-h4:text-sm",
        "prose-p:my-2",
        "prose-ul:my-2 prose-ol:my-2 prose-li:my-0.5",
        "prose-blockquote:my-3 prose-blockquote:border-l-2 prose-blockquote:font-normal",
        "prose-blockquote:not-italic prose-blockquote:text-foreground/80",
        "prose-a:text-primary prose-a:underline-offset-2 hover:prose-a:opacity-80",
        "prose-hr:my-6",
        "prose-pre:my-0 prose-pre:bg-transparent prose-pre:p-0",
        "prose-code:before:content-none prose-code:after:content-none prose-code:font-normal",
        "prose-table:my-3 prose-th:text-left prose-th:font-medium",
        className,
      )}
      style={{ lineHeight: "var(--cjk-line-height)" }}
    >
      <Streamdown
        // Streamdown memoizes each block by markdown-string equality. When
        // the in-chat search query changes, the markdown string is unchanged
        // so cached blocks skip re-rendering — meaning our component overrides
        // with the new ``hl`` closure are never invoked and matches go
        // unhighlighted. A two-state key (search vs stream) forces one full
        // remount when toggling search on/off, bypassing the block cache,
        // without churning the tree on every keystroke.
        key={query ? "search" : "stream"}
        parseIncompleteMarkdown
        animated={
          query || reducedMotion
            ? false
            : {
                animation: "fadeIn",
                duration: 180,
                easing: "ease-out",
                sep: "word",
              }
        }
        isAnimating={isStreaming === true && !query && !reducedMotion}
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          code({ className: cls, children: kids, node: _node, ...props }) {
            void _node;
            const match = /language-(\w+)/.exec(cls || "");
            if (!match) {
              return (
                <code
                  className={cn(
                    "rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]",
                    cls,
                  )}
                  {...props}
                >
                  {kids}
                </code>
              );
            }
            const code = String(kids).replace(/\n$/, "");
            return <CodeBlock language={match[1]} code={code} className="my-3" />;
          },
          pre({ children: markdownChildren }) {
            return <>{markdownChildren}</>;
          },
          a({ href, children: markdownChildren, node: _n, ...props }) {
            void _n;
            return (
              <a
                href={href}
                target="_blank"
                rel="noreferrer noopener"
                className="text-primary underline underline-offset-2 hover:opacity-80"
                {...props}
              >
                {markdownChildren}
              </a>
            );
          },
          p({ children: kids, node: _n, ...props }) {
            void _n;
            return <p {...props}>{hl(kids, "p")}</p>;
          },
          li({ children: kids, node: _n, ...props }) {
            void _n;
            return <li {...props}>{hl(kids, "li")}</li>;
          },
          em({ children: kids, node: _n, ...props }) {
            void _n;
            return <em {...props}>{hl(kids, "em")}</em>;
          },
          strong({ children: kids, node: _n, ...props }) {
            void _n;
            return <strong {...props}>{hl(kids, "strong")}</strong>;
          },
          h1({ children: kids, node: _n, ...props }) {
            void _n;
            return <h1 {...props}>{hl(kids, "h1")}</h1>;
          },
          h2({ children: kids, node: _n, ...props }) {
            void _n;
            return <h2 {...props}>{hl(kids, "h2")}</h2>;
          },
          h3({ children: kids, node: _n, ...props }) {
            void _n;
            return <h3 {...props}>{hl(kids, "h3")}</h3>;
          },
          h4({ children: kids, node: _n, ...props }) {
            void _n;
            return <h4 {...props}>{hl(kids, "h4")}</h4>;
          },
          h5({ children: kids, node: _n, ...props }) {
            void _n;
            return <h5 {...props}>{hl(kids, "h5")}</h5>;
          },
          h6({ children: kids, node: _n, ...props }) {
            void _n;
            return <h6 {...props}>{hl(kids, "h6")}</h6>;
          },
          blockquote({ children: kids, node: _n, ...props }) {
            void _n;
            return <blockquote {...props}>{hl(kids, "blockquote")}</blockquote>;
          },
        }}
      >
        {children}
      </Streamdown>
    </div>
  );
}
