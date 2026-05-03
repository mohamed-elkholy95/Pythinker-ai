import { useEffect } from "react";
import { render, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it } from "vitest";

import { MessageBubble } from "@/components/MessageBubble";
import { preloadMarkdownText } from "@/components/MarkdownText";
import {
  ThreadSearchProvider,
  useThreadSearch,
} from "@/components/ThreadSearchProvider";
import type { UIMessage } from "@/lib/types";

// Preload the lazy MarkdownTextRenderer chunk before any test runs.
// Under default parallel mode the dynamic import otherwise races with
// `waitFor`'s 1 s default and the Suspense fallback wins, breaking CI.
beforeAll(async () => {
  await import("@/components/MarkdownTextRenderer");
  preloadMarkdownText();
});

function assistantMsg(content: string): UIMessage {
  return { id: "a1", role: "assistant", content, createdAt: 0 };
}

function Harness({
  message,
  query,
}: {
  message: UIMessage;
  query: string;
}) {
  const search = useThreadSearch();
  useEffect(() => {
    if (search) search.setQuery(query);
  }, [search, query]);
  return <MessageBubble message={message} />;
}

describe("MessageBubble assistant search highlighting", () => {
  it("renders no <mark> elements when the query is empty", async () => {
    const message = assistantMsg(
      "Rust is a systems language. I love rust because rust is fast.",
    );
    const { container } = render(
      <ThreadSearchProvider>
        <Harness message={message} query="" />
      </ThreadSearchProvider>,
    );
    // Wait for the lazy MarkdownText chunk to mount and render the prose.
    await waitFor(() => {
      expect(container.querySelector("p, .markdown-content")).not.toBeNull();
    });
    expect(container.querySelectorAll("mark").length).toBe(0);
  });

  it("highlights query matches inside an assistant Markdown bubble", async () => {
    const message = assistantMsg(
      "Rust is a systems language. I love rust because rust is fast.",
    );
    const { container } = render(
      <ThreadSearchProvider>
        <Harness message={message} query="rust" />
      </ThreadSearchProvider>,
    );
    await waitFor(() => {
      const marks = container.querySelectorAll("mark");
      expect(marks.length).toBeGreaterThan(0);
    });
    const marks = container.querySelectorAll("mark");
    // Three case-insensitive occurrences of "rust" in the prose.
    expect(marks.length).toBe(3);
    for (const m of marks) {
      expect(m.textContent?.toLowerCase()).toBe("rust");
      expect(m.getAttribute("data-match-id")).toMatch(
        /^bubble-a1-md-/,
      );
    }
  });

  it("highlights matches inside list items and emphasis", async () => {
    const message = assistantMsg(
      "Languages:\n\n- *Rust* is fast\n- **Go** is simple\n- rust again",
    );
    const { container } = render(
      <ThreadSearchProvider>
        <Harness message={message} query="rust" />
      </ThreadSearchProvider>,
    );
    await waitFor(() => {
      const marks = container.querySelectorAll("mark");
      expect(marks.length).toBeGreaterThan(0);
    });
    // One inside <em>, one inside <li> bare text.
    const marks = container.querySelectorAll("mark");
    expect(marks.length).toBe(2);
  });

  it("gives every match a distinct data-match-id across sibling blocks", async () => {
    // Two paragraphs each containing two matches: ensures the per-render
    // monotonic slot counter prevents id collisions between siblings.
    const message = assistantMsg("rust rust\n\nrust rust");
    const { container } = render(
      <ThreadSearchProvider>
        <Harness message={message} query="rust" />
      </ThreadSearchProvider>,
    );
    await waitFor(() => {
      expect(container.querySelectorAll("mark").length).toBe(4);
    });
    const ids = Array.from(
      container.querySelectorAll<HTMLElement>("mark"),
    ).map((m) => m.getAttribute("data-match-id"));
    expect(new Set(ids).size).toBe(4);
  });
});
