import { beforeEach, describe, expect, it } from "vitest";

import { PythinkerClient } from "@/lib/pythinker-client";

/**
 * Minimal fake WebSocket — same shape used by ``pythinker-client.test.ts``.
 * We only need enough surface to drive an open lifecycle and capture frames.
 */
class FakeSocket {
  static instances: FakeSocket[] = [];
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  url: string;
  readyState = FakeSocket.CONNECTING;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((ev?: { code?: number }) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = FakeSocket.CLOSED;
    this.onclose?.();
  }

  fakeOpen() {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }
}

function lastSocket(): FakeSocket {
  const s = FakeSocket.instances.at(-1);
  if (!s) throw new Error("no socket created yet");
  return s;
}

function makeClient(): { client: PythinkerClient; socket: FakeSocket } {
  const client = new PythinkerClient({
    url: "ws://test",
    reconnect: false,
    socketFactory: (url) => new FakeSocket(url) as unknown as WebSocket,
  });
  client.connect();
  const socket = lastSocket();
  socket.fakeOpen();
  return { client, socket };
}

beforeEach(() => {
  FakeSocket.instances = [];
});

describe("PythinkerClient action envelopes", () => {
  it("sendStop emits a {type:'stop', chat_id} envelope", () => {
    const { client, socket } = makeClient();
    client.sendStop("abcd-1234");
    const last = JSON.parse(socket.sent.at(-1) as string);
    expect(last).toEqual({ type: "stop", chat_id: "abcd-1234" });
  });

  it("regenerate emits a {type:'regenerate', chat_id} envelope", () => {
    const { client, socket } = makeClient();
    client.regenerate("abcd-1234");
    const last = JSON.parse(socket.sent.at(-1) as string);
    expect(last).toEqual({ type: "regenerate", chat_id: "abcd-1234" });
  });

  it("editAndResend emits {type:'edit', chat_id, user_msg_index, content}", () => {
    const { client, socket } = makeClient();
    client.editAndResend("abcd-1234", 2, "fixed text");
    const last = JSON.parse(socket.sent.at(-1) as string);
    expect(last).toEqual({
      type: "edit",
      chat_id: "abcd-1234",
      user_msg_index: 2,
      content: "fixed text",
    });
  });
});
