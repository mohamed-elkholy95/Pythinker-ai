import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ThreadShell } from "@/components/thread/ThreadShell";
import { ClientProvider } from "@/providers/ClientProvider";
import type { EncodeResponse } from "@/lib/imageEncode";

const encodeImage = vi.fn<(file: File) => Promise<EncodeResponse>>();

vi.mock("@/lib/imageEncode", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/imageEncode")>();
  return {
    ...actual,
    encodeImage: (file: File) => encodeImage(file),
  };
});

function makeClient() {
  return {
    status: "open" as const,
    defaultChatId: null as string | null,
    onStatus: () => () => {},
    onChat: () => () => {},
    onError: () => () => {},
    sendMessage: vi.fn(),
    newChat: vi.fn(),
    attach: vi.fn(),
    connect: vi.fn(),
    close: vi.fn(),
    updateUrl: vi.fn(),
  };
}

function wrap(client: ReturnType<typeof makeClient>, children: ReactNode) {
  return (
    <ClientProvider
      client={client as unknown as import("@/lib/pythinker-client").PythinkerClient}
      token="tok"
    >
      {children}
    </ClientProvider>
  );
}

function session(chatId: string) {
  return {
    key: `websocket:${chatId}`,
    channel: "websocket" as const,
    chatId,
    createdAt: null,
    updatedAt: null,
    preview: "",
  };
}

function pngFile(name = "drop.png", size = 8) {
  return new File([new Uint8Array(size)], name, { type: "image/png" });
}

beforeEach(() => {
  encodeImage.mockReset();
  let id = 0;
  if (!(globalThis.URL as unknown as { createObjectURL?: unknown }).createObjectURL) {
    (globalThis.URL as unknown as { createObjectURL: (b: Blob) => string }).createObjectURL =
      () => `blob:mock/${++id}`;
  }
  if (!(globalThis.URL as unknown as { revokeObjectURL?: unknown }).revokeObjectURL) {
    (globalThis.URL as unknown as { revokeObjectURL: (u: string) => void }).revokeObjectURL =
      () => {};
  }
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({}),
    }),
  );
});

describe("ThreadShell dropzone", () => {
  it("stages a dropped image when it lands on the outer chat section", async () => {
    const file = pngFile("drop.png");
    encodeImage.mockResolvedValueOnce({
      id: "stub",
      ok: true,
      dataUrl: `data:image/png;base64,${btoa("drop.png")}`,
      mime: "image/png",
      bytes: file.size,
      origBytes: file.size,
      normalized: false,
    } as EncodeResponse);
    const client = makeClient();

    const { container } = render(
      wrap(
        client,
        <ThreadShell
          session={session("chat-d")}
          title="Chat chat-d"
          onToggleSidebar={() => {}}
          onGoHome={() => {}}
          onNewChat={vi.fn().mockResolvedValue("chat-d")}
        />,
      ),
    );

    const root = container.querySelector("section");
    expect(root).toBeTruthy();

    // No chip yet — the composer hasn't seen any files.
    expect(screen.queryByTestId("composer-chip")).toBeNull();

    const dataTransfer = {
      files: [file],
      types: ["Files"],
      items: [
        {
          kind: "file",
          type: "image/png",
          getAsFile: () => file,
        },
      ],
    };

    await act(async () => {
      fireEvent.dragEnter(root!, { dataTransfer });
      fireEvent.dragOver(root!, { dataTransfer });
      fireEvent.drop(root!, { dataTransfer });
    });

    // Drop surface pushed the file through useAttachedImages.enqueue, which
    // is now owned by ThreadShell — the composer renders the chip from a
    // prop instead of its own internal hook.
    await waitFor(() => {
      expect(screen.getByTestId("composer-chip")).toBeInTheDocument();
    });
    expect(encodeImage).toHaveBeenCalledTimes(1);
  });
});
