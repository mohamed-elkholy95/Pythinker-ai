import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import { describe, expect, it, vi } from "vitest";

import { InChatSearch } from "@/components/InChatSearch";
import { MessageBubble } from "@/components/MessageBubble";
import {
  ThreadSearchProvider,
  useThreadSearch,
} from "@/components/ThreadSearchProvider";
import { useInChatSearch } from "@/hooks/useInChatSearch";
import i18n from "@/i18n";
import type { UIMessage } from "@/lib/types";

describe("useInChatSearch", () => {
  it("starts with an empty query, no matches, no active id", () => {
    const { result } = renderHook(() => useInChatSearch());
    expect(result.current.query).toBe("");
    expect(result.current.matchIds).toEqual([]);
    expect(result.current.activeMatchId).toBeNull();
  });

  it("aggregates registered matches across bubbles in registration order", () => {
    const { result } = renderHook(() => useInChatSearch());
    act(() => {
      result.current.registerMatches("a", ["a:0", "a:1"]);
      result.current.registerMatches("b", ["b:0"]);
    });
    expect(result.current.matchIds).toEqual(["a:0", "a:1", "b:0"]);
    expect(result.current.activeMatchId).toBe("a:0");
  });

  it("cycles activeMatchId via next() and prev() (wrapping)", () => {
    const { result } = renderHook(() => useInChatSearch());
    act(() => {
      result.current.registerMatches("a", ["a:0", "a:1", "a:2"]);
    });
    expect(result.current.activeMatchId).toBe("a:0");
    act(() => result.current.next());
    expect(result.current.activeMatchId).toBe("a:1");
    act(() => result.current.next());
    expect(result.current.activeMatchId).toBe("a:2");
    act(() => result.current.next());
    expect(result.current.activeMatchId).toBe("a:0");
    act(() => result.current.prev());
    expect(result.current.activeMatchId).toBe("a:2");
  });

  it("reset() clears query, matches, and active id", () => {
    const { result } = renderHook(() => useInChatSearch());
    act(() => {
      result.current.setQuery("foo");
      result.current.registerMatches("a", ["a:0"]);
    });
    expect(result.current.query).toBe("foo");
    expect(result.current.matchIds).toEqual(["a:0"]);
    act(() => result.current.reset());
    expect(result.current.query).toBe("");
    expect(result.current.matchIds).toEqual([]);
    expect(result.current.activeMatchId).toBeNull();
  });
});

describe("ThreadSearchProvider", () => {
  it("returns null from useThreadSearch when no provider is mounted", () => {
    const { result } = renderHook(() => useThreadSearch());
    expect(result.current).toBeNull();
  });

  it("exposes the in-chat search state to descendants", () => {
    const { result } = renderHook(() => useThreadSearch(), {
      wrapper: ({ children }) => (
        <ThreadSearchProvider>{children}</ThreadSearchProvider>
      ),
    });
    expect(result.current).not.toBeNull();
    expect(result.current?.query).toBe("");
    expect(result.current?.matchIds).toEqual([]);
  });
});

describe("MessageBubble + ThreadSearchProvider integration", () => {
  it("renders user-bubble text unchanged when no provider is mounted", () => {
    const message: UIMessage = {
      id: "u1",
      role: "user",
      content: "hello world",
      createdAt: 0,
    };
    render(<MessageBubble message={message} />);
    expect(screen.getByText("hello world")).toBeInTheDocument();
    expect(document.querySelectorAll("mark").length).toBe(0);
  });

  it("highlights the query inside a user bubble when wrapped in ThreadSearchProvider", () => {
    const message: UIMessage = {
      id: "u1",
      role: "user",
      content: "hello world",
      createdAt: 0,
    };
    function Harness() {
      const search = useThreadSearch();
      // Drive the query at mount so the bubble's first render already sees it.
      if (search && search.query !== "world") search.setQuery("world");
      return <MessageBubble message={message} />;
    }
    render(
      <ThreadSearchProvider>
        <Harness />
      </ThreadSearchProvider>,
    );
    const marks = document.querySelectorAll("mark");
    expect(marks.length).toBe(1);
    expect(marks[0].textContent).toBe("world");
    expect(marks[0].getAttribute("data-match-id")).toBe("bubble-u1-user:0");
  });
});

describe("InChatSearch overlay", () => {
  function renderOverlay(onClose: () => void = () => {}) {
    return render(
      <I18nextProvider i18n={i18n}>
        <ThreadSearchProvider>
          <InChatSearch open={true} onClose={onClose} />
        </ThreadSearchProvider>
      </I18nextProvider>,
    );
  }

  it("renders an input and navigation buttons", () => {
    renderOverlay();
    expect(
      screen.getByRole("searchbox", { name: /search in chat/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /next match/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /previous match/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /close search/i }),
    ).toBeInTheDocument();
  });

  it("Escape fires onClose", () => {
    const onClose = vi.fn();
    renderOverlay(onClose);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("typing updates the search input value", async () => {
    const user = userEvent.setup();
    renderOverlay();
    const input = screen.getByRole("searchbox", {
      name: /search in chat/i,
    });
    await user.type(input, "foo");
    expect(input).toHaveValue("foo");
  });

  it("renders nothing when open is false", () => {
    const { container } = render(
      <I18nextProvider i18n={i18n}>
        <ThreadSearchProvider>
          <InChatSearch open={false} onClose={() => {}} />
        </ThreadSearchProvider>
      </I18nextProvider>,
    );
    expect(container.querySelector("[role=\"search\"]")).toBeNull();
  });

  it("close button fires onClose", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderOverlay(onClose);
    await user.click(screen.getByRole("button", { name: /close search/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
