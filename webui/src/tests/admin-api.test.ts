import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  fetchAdminConfig,
  fetchAdminConfigBackups,
  fetchAdminConfigSchema,
  fetchAdminOverview,
  fetchAdminSessions,
  fetchAdminSurfaces,
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
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("fetches redacted admin config", async () => {
    const body = await fetchAdminConfig("tok");

    expect(fetch).toHaveBeenCalledWith(
      "/api/admin/config",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
    expect(body.config.providers.openai.apiKey).toBe("********");
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
      expect.objectContaining({ headers: { Authorization: "Bearer token-1" } }),
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
      expect.objectContaining({ headers: { Authorization: "Bearer token-2" } }),
    );
    expect(backups).toHaveLength(1);
    expect(backups[0].id).toBe("backup-1");
  });

  it("fetches all-channel admin sessions", async () => {
    await fetchAdminSessions("tok");

    expect(fetch).toHaveBeenCalledWith(
      "/api/admin/sessions",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("fetches the consolidated control-console surfaces", async () => {
    const body = await fetchAdminSurfaces("tok");

    expect(fetch).toHaveBeenCalledWith(
      "/api/admin/surfaces",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
    expect(body.overview.version).toBe("0.1.0");
    expect(body.channels.rows).toEqual([]);
  });
});
