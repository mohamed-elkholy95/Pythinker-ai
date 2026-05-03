import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  cancelSubagent,
  fetchAdminConfig,
  fetchAdminConfigBackups,
  fetchAdminConfigSchema,
  fetchAdminOverview,
  fetchAdminSessions,
  fetchAdminSurfaces,
  restartSession,
  stopSession,
} from "@/lib/admin-api";

describe("admin API helpers", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          version: "0.1.0",
          overview: { version: "0.1.0" },
          channels: { rows: [] },
          config: { providers: { openai: { apiKey: "********" } } },
          secret_paths: ["providers.openai.api_key"],
          sessions: [],
        }),
      }),
    );
  });

  it("fetches admin overview with bearer auth", async () => {
    await fetchAdminOverview("tok");

    expect(fetch).toHaveBeenCalledWith(
      "/api/admin/overview",
      expect.objectContaining({
        headers: {
          Authorization: "Bearer tok",
          "X-Pythinker-Admin-Action": "1",
        },
      }),
    );
  });

  it("fetches redacted admin config", async () => {
    const body = await fetchAdminConfig("tok");

    expect(fetch).toHaveBeenCalledWith(
      "/api/admin/config",
      expect.objectContaining({
        headers: {
          Authorization: "Bearer tok",
          "X-Pythinker-Admin-Action": "1",
        },
      }),
    );
    const providers = body.config.providers as { openai: { apiKey: string } };
    expect(providers.openai.apiKey).toBe("********");
    expect(body.secret_paths).toContain("providers.openai.api_key");
  });

  it("fetches admin config schema with bearer auth", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          schema: { type: "object", properties: { agents: { type: "object" } } },
          secret_paths: ["providers.openai.api_key"],
          restart_required_paths: ["*"],
        }),
      }),
    );

    const payload = await fetchAdminConfigSchema("token-1");

    expect(fetch).toHaveBeenCalledWith(
      "/api/admin/config/schema",
      expect.objectContaining({
        headers: {
          Authorization: "Bearer token-1",
          "X-Pythinker-Admin-Action": "1",
        },
      }),
    );
    expect(payload.secret_paths).toContain("providers.openai.api_key");
  });

  it("fetches admin config backups with bearer auth", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          backups: [
            {
              id: "backup-1",
              mtime_ms: 123,
              size_bytes: 456,
              source: "sibling",
              kind: "pre-edit-bak",
              summary: { valid: true },
            },
          ],
        }),
      }),
    );

    const backups = await fetchAdminConfigBackups("token-2");

    expect(fetch).toHaveBeenCalledWith(
      "/api/admin/config/backups",
      expect.objectContaining({
        headers: {
          Authorization: "Bearer token-2",
          "X-Pythinker-Admin-Action": "1",
        },
      }),
    );
    expect(backups).toHaveLength(1);
    expect(backups[0].id).toBe("backup-1");
  });

  it("fetches all-channel admin sessions", async () => {
    await fetchAdminSessions("tok");

    expect(fetch).toHaveBeenCalledWith(
      "/api/admin/sessions",
      expect.objectContaining({
        headers: {
          Authorization: "Bearer tok",
          "X-Pythinker-Admin-Action": "1",
        },
      }),
    );
  });

  it("fetches the consolidated control-console surfaces", async () => {
    const body = await fetchAdminSurfaces("tok");

    expect(fetch).toHaveBeenCalledWith(
      "/api/admin/surfaces",
      expect.objectContaining({
        headers: {
          Authorization: "Bearer tok",
          "X-Pythinker-Admin-Action": "1",
        },
      }),
    );
    expect(body.overview.version).toBe("0.1.0");
    expect(body.channels.rows).toEqual([]);
  });

  it("calls the stop-session route with action-in-path and CSRF header", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ cancelled: 2 }),
      }),
    );
    const result = await stopSession("tok", "websocket:browser");
    expect(fetch).toHaveBeenCalledWith(
      "/api/admin/sessions/websocket%3Abrowser/stop",
      expect.objectContaining({
        headers: {
          Authorization: "Bearer tok",
          "X-Pythinker-Admin-Action": "1",
        },
      }),
    );
    expect(result.cancelled).toBe(2);
  });

  it("calls the restart-session route and parses found/cleared flags", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ cancelled: 0, checkpoint_cleared: true, found: true }),
      }),
    );
    const result = await restartSession("tok", "slack:C1");
    expect(fetch).toHaveBeenCalledWith(
      "/api/admin/sessions/slack%3AC1/restart",
      expect.anything(),
    );
    expect(result.found).toBe(true);
    expect(result.checkpoint_cleared).toBe(true);
  });

  it("calls the cancel-subagent route", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ cancelled: true }),
      }),
    );
    const result = await cancelSubagent("tok", "abc123");
    expect(fetch).toHaveBeenCalledWith(
      "/api/admin/subagents/abc123/cancel",
      expect.anything(),
    );
    expect(result.cancelled).toBe(true);
  });
});
