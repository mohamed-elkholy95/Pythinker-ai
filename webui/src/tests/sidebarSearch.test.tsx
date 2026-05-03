import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { I18nextProvider } from "react-i18next";
import { describe, expect, it, vi } from "vitest";

import { SidebarSearch } from "@/components/SidebarSearch";
import i18n from "@/i18n";
import { ClientProvider } from "@/providers/ClientProvider";

const fakeClient = { onChat: vi.fn(() => () => {}) };

function renderSearch(
  onSelectHit: (loc: { sessionKey: string; messageIndex: number }) => void = vi.fn(),
  children?: ReactNode,
) {
  return render(
    <I18nextProvider i18n={i18n}>
      <ClientProvider client={fakeClient as never} token="t">
        <SidebarSearch onSelectHit={onSelectHit}>{children}</SidebarSearch>
      </ClientProvider>
    </I18nextProvider>,
  );
}

describe("SidebarSearch", () => {
  it("renders an input", () => {
    renderSearch();
    expect(
      screen.getByRole("searchbox", { name: /search all chats/i }),
    ).toBeInTheDocument();
  });

  it("renders children when the query is empty", () => {
    renderSearch(vi.fn(), <div data-testid="recent-section">recent</div>);
    expect(screen.getByTestId("recent-section")).toBeInTheDocument();
  });

  it("typing triggers a debounced fetch and renders hits", async () => {
    global.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        results: [
          {
            session_key: "websocket:abcd",
            message_index: 0,
            role: "user",
            snippet: "rust release notes",
            match_offsets: [[0, 4]],
            title: "Rust chat",
            archived: false,
          },
        ],
        offset: 0,
        limit: 50,
        has_more: false,
      }),
    })) as unknown as typeof fetch;

    const user = userEvent.setup();
    renderSearch(vi.fn(), <div data-testid="recent-section">recent</div>);
    const input = screen.getByRole("searchbox", {
      name: /search all chats/i,
    });
    await user.type(input, "rust");

    // After debounce settles, the result row should appear and replace the
    // recent-section children.
    await screen.findByText(/Rust chat/);
    expect(screen.queryByTestId("recent-section")).not.toBeInTheDocument();
    const mark = document.querySelector("mark");
    expect(mark?.textContent).toBe("rust");
  });

  it("clicking a hit calls onSelectHit with sessionKey + messageIndex", async () => {
    global.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        results: [
          {
            session_key: "websocket:abcd",
            message_index: 7,
            role: "user",
            snippet: "rust",
            match_offsets: [[0, 4]],
            title: "Rust",
            archived: false,
          },
        ],
        offset: 0,
        limit: 50,
        has_more: false,
      }),
    })) as unknown as typeof fetch;

    const onSelect = vi.fn();
    const user = userEvent.setup();
    renderSearch(onSelect);
    await user.type(
      screen.getByRole("searchbox", { name: /search all chats/i }),
      "rust",
    );
    const row = await screen.findByRole("button", { name: /Rust/i });
    fireEvent.click(row);
    expect(onSelect).toHaveBeenCalledWith({
      sessionKey: "websocket:abcd",
      messageIndex: 7,
    });
  });
});
