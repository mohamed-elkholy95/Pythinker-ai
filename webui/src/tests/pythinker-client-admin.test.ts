import { beforeEach, describe, expect, it } from "vitest";

import { PythinkerClient } from "@/lib/pythinker-client";

class FakeSocket {
  static instances: FakeSocket[] = [];
  static readonly OPEN = 1;
  static readonly CLOSED = 3;

  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(public url: string) {
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

  fakeMessage(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent);
  }
}

function makeClient(): { client: PythinkerClient; socket: FakeSocket } {
  const client = new PythinkerClient({
    url: "ws://test",
    reconnect: false,
    socketFactory: (url) => new FakeSocket(url) as unknown as WebSocket,
  });
  client.connect();
  const socket = FakeSocket.instances.at(-1);
  if (!socket) throw new Error("no socket");
  socket.fakeOpen();
  return { client, socket };
}

beforeEach(() => {
  FakeSocket.instances = [];
});

describe("PythinkerClient admin config RPC", () => {
  it("sends config set requests and resolves on matching reply", async () => {
    const { client, socket } = makeClient();

    const pending = client.setAdminConfig("logging.level", "DEBUG");
    const frame = JSON.parse(socket.sent.at(-1) as string);
    expect(frame).toMatchObject({
      type: "admin_config_set",
      path: "logging.level",
      value: "DEBUG",
    });

    socket.fakeMessage({
      event: "admin_config_saved",
      request_id: frame.request_id,
      path: "logging.level",
      restart_required: true,
    });

    await expect(pending).resolves.toEqual({
      path: "logging.level",
      restartRequired: true,
    });
  });

  it("rejects config requests on admin errors", async () => {
    const { client, socket } = makeClient();

    const pending = client.setAdminConfig("logging.level", "LOUD");
    const frame = JSON.parse(socket.sent.at(-1) as string);
    socket.fakeMessage({
      event: "admin_config_error",
      request_id: frame.request_id,
      detail: "Input should be 'DEBUG'",
    });

    await expect(pending).rejects.toThrow("Input should be");
  });

  it("sends backup restore requests", async () => {
    const { client, socket } = makeClient();

    const pending = client.restoreAdminConfigBackup("backup-1");
    const frame = JSON.parse(socket.sent.at(-1) as string);
    expect(frame).toMatchObject({
      type: "admin_config_restore_backup",
      backup_id: "backup-1",
    });

    socket.fakeMessage({
      event: "admin_config_saved",
      request_id: frame.request_id,
      path: "config.backup",
      restart_required: true,
    });

    await expect(pending).resolves.toEqual({
      path: "config.backup",
      restartRequired: true,
    });
  });

  it("sends bind probe requests", async () => {
    const { client, socket } = makeClient();

    const pending = client.testAdminBind("127.0.0.1", 8765);
    const frame = JSON.parse(socket.sent.at(-1) as string);
    expect(frame).toMatchObject({
      type: "admin_test_bind",
      host: "127.0.0.1",
      port: 8765,
    });

    socket.fakeMessage({
      event: "admin_test_bind_result",
      request_id: frame.request_id,
      result: { ok: true },
    });

    await expect(pending).resolves.toEqual({ ok: true });
  });

  it("sends channel probe requests", async () => {
    const { client, socket } = makeClient();

    const pending = client.testAdminChannel("telegram");
    const frame = JSON.parse(socket.sent.at(-1) as string);
    expect(frame).toMatchObject({ type: "admin_test_channel", name: "telegram" });

    socket.fakeMessage({
      event: "admin_test_channel_result",
      request_id: frame.request_id,
      result: { ok: true, checks: ["channel_known"], last_error: null, ms: 1 },
    });

    await expect(pending).resolves.toEqual({
      ok: true,
      checks: ["channel_known"],
      last_error: null,
      ms: 1,
    });
  });

  it("sends MCP probe requests", async () => {
    const { client, socket } = makeClient();

    const pending = client.probeAdminMcp("local");
    const frame = JSON.parse(socket.sent.at(-1) as string);
    expect(frame).toMatchObject({ type: "admin_mcp_probe", server: "local" });

    socket.fakeMessage({
      event: "admin_mcp_probe_result",
      request_id: frame.request_id,
      result: { ok: true, tools: [], elapsed_ms: 0 },
    });

    await expect(pending).resolves.toEqual({ ok: true, tools: [], elapsed_ms: 0 });
  });

  it("sends browser probe requests", async () => {
    const { client, socket } = makeClient();

    const pending = client.probeAdminBrowser();
    const frame = JSON.parse(socket.sent.at(-1) as string);
    expect(frame).toMatchObject({ type: "admin_browser_probe" });

    socket.fakeMessage({
      event: "admin_browser_probe_result",
      request_id: frame.request_id,
      result: { active_contexts: 1, last_url: "https://example.test", cookie_size_bytes: 0 },
    });

    await expect(pending).resolves.toEqual({
      active_contexts: 1,
      last_url: "https://example.test",
      cookie_size_bytes: 0,
    });
  });
});
