import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatSummary } from "@/lib/types";

const connectSpy = vi.fn();
const refreshSpy = vi.fn();
const createChatSpy = vi.fn().mockResolvedValue("chat-1");
const deleteChatSpy = vi.fn();
const togglePinSpy = vi.fn();
const toggleArchiveSpy = vi.fn();
let mockSessions: ChatSummary[] = [];

vi.mock("@/hooks/useSessions", async (importOriginal) => {
  const React = await import("react");
  const actual = await importOriginal<typeof import("@/hooks/useSessions")>();
  return {
    ...actual,
    useSessions: () => {
      const [sessions, setSessions] = React.useState(mockSessions);
      return {
        sessions,
        pinnedSessions: sessions.filter(
          (s: ChatSummary) => s.pinned && !s.archived,
        ),
        recentSessions: sessions.filter(
          (s: ChatSummary) => !s.pinned && !s.archived,
        ),
        archivedSessions: sessions.filter((s: ChatSummary) => s.archived),
        loading: false,
        error: null,
        refresh: refreshSpy,
        createChat: createChatSpy,
        deleteChat: async (key: string) => {
          await deleteChatSpy(key);
          setSessions((prev: ChatSummary[]) => prev.filter((s) => s.key !== key));
        },
        togglePin: async (key: string) => {
          togglePinSpy(key);
          setSessions((prev: ChatSummary[]) =>
            prev.map((s) =>
              s.key === key ? { ...s, pinned: !s.pinned } : s,
            ),
          );
        },
        toggleArchive: async (key: string) => {
          toggleArchiveSpy(key);
          setSessions((prev: ChatSummary[]) =>
            prev.map((s) =>
              s.key === key ? { ...s, archived: !s.archived } : s,
            ),
          );
        },
      };
    },
  };
});

vi.mock("@/hooks/useTheme", () => ({
  useTheme: () => ({
    theme: "light" as const,
    toggle: vi.fn(),
  }),
}));

vi.mock("@/lib/bootstrap", () => ({
  fetchBootstrap: vi.fn().mockResolvedValue({
    token: "tok",
    ws_path: "/",
    expires_in: 300,
  }),
  deriveWsUrl: vi.fn(() => "ws://test"),
}));

vi.mock("@/lib/pythinker-client", () => {
  class MockClient {
    status = "idle" as const;
    defaultChatId: string | null = null;
    connect = connectSpy;
    onStatus = () => () => {};
    onError = () => () => {};
    onChat = () => () => {};
    sendMessage = vi.fn();
    newChat = vi.fn();
    attach = vi.fn();
    close = vi.fn();
    updateUrl = vi.fn();
  }

  return { PythinkerClient: MockClient };
});

import App from "@/App";

describe("App layout", () => {
  beforeEach(() => {
    mockSessions = [];
    connectSpy.mockClear();
    refreshSpy.mockReset();
    createChatSpy.mockClear();
    deleteChatSpy.mockReset();
    togglePinSpy.mockReset();
    toggleArchiveSpy.mockReset();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
      }),
    );
  });

  it("keeps sidebar layout out of the main thread width contract", async () => {
    const { container } = render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());

    const main = container.querySelector("main");
    expect(main).toBeInTheDocument();
    expect(main).not.toHaveAttribute("style");

    const asideClassNames = Array.from(container.querySelectorAll("aside")).map(
      (el) => el.className,
    );
    expect(asideClassNames.some((cls) => cls.includes("lg:block"))).toBe(true);
  });

  it("switches to the next session when deleting the active chat", async () => {
    mockSessions = [
      {
        key: "websocket:chat-a",
        channel: "websocket",
        chatId: "chat-a",
        createdAt: "2026-04-16T10:00:00Z",
        updatedAt: "2026-04-16T10:00:00Z",
        preview: "First chat",
      },
      {
        key: "websocket:chat-b",
        channel: "websocket",
        chatId: "chat-b",
        createdAt: "2026-04-16T11:00:00Z",
        updatedAt: "2026-04-16T11:00:00Z",
        preview: "Second chat",
      },
    ];

    render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^First chat$/ })).toBeInTheDocument(),
    );

    fireEvent.pointerDown(screen.getByLabelText("Chat actions for First chat"), {
      button: 0,
    });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));

    await waitFor(() =>
      expect(screen.getByText('Delete “First chat”?')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(deleteChatSpy).toHaveBeenCalledWith("websocket:chat-a"),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /^Second chat$/ }),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText('Delete “First chat”?')).not.toBeInTheDocument();
    expect(document.body.style.pointerEvents).not.toBe("none");
  }, 15_000);

  it("renders the sidebar split into Pinned / Recent / Archived sections", async () => {
    mockSessions = [
      {
        key: "websocket:pinned",
        channel: "websocket",
        chatId: "pinned",
        createdAt: "2026-04-26T10:00:00Z",
        updatedAt: "2026-04-26T10:00:00Z",
        preview: "Pinned topic",
        pinned: true,
      },
      {
        key: "websocket:recent",
        channel: "websocket",
        chatId: "recent",
        createdAt: "2026-04-26T11:00:00Z",
        updatedAt: "2026-04-26T11:00:00Z",
        preview: "Recent topic",
      },
      {
        key: "websocket:archived",
        channel: "websocket",
        chatId: "archived",
        createdAt: "2026-04-26T12:00:00Z",
        updatedAt: "2026-04-26T12:00:00Z",
        preview: "Archived topic",
        archived: true,
      },
    ];

    render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getAllByText("Pinned").length).toBeGreaterThan(0),
    );
    expect(screen.getAllByText("Recent").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Archived").length).toBeGreaterThan(0);

    expect(
      screen.getByLabelText("Chat actions for Pinned topic"),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText("Chat actions for Recent topic"),
    ).toBeInTheDocument();
    // Archived row is collapsed under the foldable section by default.
    expect(
      screen.queryByLabelText("Chat actions for Archived topic"),
    ).not.toBeInTheDocument();
  });

  it("archived section is collapsed by default and reveals rows when expanded", async () => {
    mockSessions = [
      {
        key: "websocket:open",
        channel: "websocket",
        chatId: "open",
        createdAt: "2026-04-26T11:00:00Z",
        updatedAt: "2026-04-26T11:00:00Z",
        preview: "Active topic",
      },
      {
        key: "websocket:archived",
        channel: "websocket",
        chatId: "archived",
        createdAt: "2026-04-26T12:00:00Z",
        updatedAt: "2026-04-26T12:00:00Z",
        preview: "Buried topic",
        archived: true,
      },
    ];

    render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getAllByText("Archived").length).toBeGreaterThan(0),
    );
    // Archived section starts collapsed: its row is not in the DOM yet.
    expect(
      screen.queryByLabelText("Chat actions for Buried topic"),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getAllByText("Archived")[0]);
    await waitFor(() =>
      expect(
        screen.getByLabelText("Chat actions for Buried topic"),
      ).toBeInTheDocument(),
    );
  });

  it("shows Pin and Archive entries in the chat row dropdown menu", async () => {
    mockSessions = [
      {
        key: "websocket:row",
        channel: "websocket",
        chatId: "row",
        createdAt: "2026-04-26T11:00:00Z",
        updatedAt: "2026-04-26T11:00:00Z",
        preview: "Solo chat",
      },
    ];

    render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());
    await waitFor(() =>
      expect(
        screen.getByLabelText("Chat actions for Solo chat"),
      ).toBeInTheDocument(),
    );

    fireEvent.pointerDown(
      screen.getByLabelText("Chat actions for Solo chat"),
      { button: 0 },
    );
    expect(
      await screen.findByRole("menuitem", { name: "Pin" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: "Archive" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: "Delete" }),
    ).toBeInTheDocument();
  });
});
