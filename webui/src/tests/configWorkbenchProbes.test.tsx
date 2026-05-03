import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConfigWorkbench } from "@/components/admin/config/ConfigWorkbench";
import type { PythinkerClient } from "@/lib/pythinker-client";
import { ClientProvider } from "@/providers/ClientProvider";

vi.mock("@/lib/admin-api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/admin-api")>("@/lib/admin-api");
  const backupRows = Array.from({ length: 5 }, (_, index) => ({
    id: `backup-${index + 1}`,
    mtime_ms: 1000 + index,
    size_bytes: 200 + index,
    source: "sibling",
    kind: "pre-edit-bak",
    summary: { valid: true },
  }));
  return {
    ...actual,
    fetchAdminConfigBackups: vi.fn().mockResolvedValue(backupRows),
    fetchAdminConfigSchema: vi.fn().mockResolvedValue({
      schema: {
        type: "object",
        properties: {
          agents: {
            type: "object",
            properties: {
              defaults: {
                type: "object",
                properties: { model: { type: "string", title: "Model" } },
              },
            },
          },
          channels: { type: "object", properties: {} },
          providers: {
            type: "object",
            properties: {
              openai: {
                type: "object",
                properties: { apiKey: { type: "string", title: "API Key" } },
              },
            },
          },
          tools: { type: "object", properties: {} },
          gateway: { type: "object", properties: {} },
          api: { type: "object", properties: {} },
        },
      },
      secret_paths: ["providers.openai.api_key"],
      field_defaults: { "agents.defaults.model": "openai/default" },
      restart_required_paths: ["*"],
    }),
  };
});

const client = {
  setAdminConfig: vi.fn(),
  unsetAdminConfig: vi.fn(),
  replaceAdminSecret: vi.fn(),
  restoreAdminConfigBackup: vi.fn().mockResolvedValue({ path: "config.backup", restartRequired: true }),
  testAdminBind: vi.fn().mockResolvedValue({ ok: false, errno: "EADDRINUSE", message: "EADDRINUSE" }),
  testAdminChannel: vi.fn().mockResolvedValue({
    ok: true,
    checks: [
      "channel_known",
      "config_present",
      "config_shape_valid",
      "enabled_flag_valid",
      "required_secrets_present",
      "allow_from_posture",
      "local_dependencies_present",
    ],
    last_error: null,
    ms: 1,
  }),
  probeAdminMcp: vi.fn().mockResolvedValue({ ok: true, tools: ["search", "read"], elapsed_ms: 2 }),
  probeAdminBrowser: vi.fn().mockResolvedValue({ active_contexts: 3, last_url: "https://example.test", cookie_size_bytes: 0 }),
};

const surfaces = {
  config: {
    config: {
      agents: { defaults: { model: "openai/custom" } },
      providers: { openai: { api_key: "********" } },
      channels: {},
      tools: { mcpServers: { remote: {} }, browser: { enable: true } },
      gateway: { host: "127.0.0.1", port: 8765 },
      api: { host: "127.0.0.1", port: 8900 },
    },
    secret_paths: ["providers.openai.api_key"],
    env_references: { "providers.openai.api_key": { env_var: "ADMIN_TEST_OPENAI_KEY", is_secret: true } },
    field_defaults: { "agents.defaults.model": "openai/default" },
    restart_required_paths: ["*"],
  },
  channels: { rows: [{ name: "websocket", enabled: true, running: true, required_secrets: [], uptime_buckets: Array(60).fill(1) }] },
  overview: { gateway: { host: "127.0.0.1", port: 8765 }, api: { host: "127.0.0.1", port: 8900 } },
  tools: { mcp_servers: [{ name: "remote", status: "configured" }] },
  agents: { routing: { model: "openai/custom", match_phase: "exact" } },
  providers: { rows: [] },
  runtime: {},
};

function renderWorkbench() {
  return render(
    <ClientProvider client={client as unknown as PythinkerClient} token="admin-token">
      <ConfigWorkbench token="admin-token" surfaces={surfaces as never} onRefresh={vi.fn()} />
    </ClientProvider>,
  );
}

describe("ConfigWorkbench PR-B probes", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders backups and confirms restore", async () => {
    renderWorkbench();

    expect(await screen.findByText(/version 5: backup-5/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /restore backup 1/i }));
    await userEvent.click(screen.getByRole("button", { name: /confirm restore/i }));

    expect(client.restoreAdminConfigBackup).toHaveBeenCalledWith("backup-1");
    expect(await screen.findByText(/restart required after restore/i)).toBeInTheDocument();
  });

  it("renders probe results for bind, channel, MCP, and browser", async () => {
    renderWorkbench();

    await userEvent.click(await screen.findByRole("button", { name: /gateway/i }));
    await userEvent.click(screen.getByRole("button", { name: /test bind/i }));
    expect(await screen.findByText("EADDRINUSE")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /channels/i }));
    await userEvent.click(screen.getByRole("button", { name: /validate websocket/i }));
    expect(await screen.findByText("channel_known")).toBeInTheDocument();
    expect(screen.getAllByLabelText(/uptime tick/i)).toHaveLength(60);

    await userEvent.click(screen.getByRole("button", { name: /tools/i }));
    await userEvent.click(screen.getByRole("tab", { name: /mcp/i }));
    await userEvent.click(screen.getByRole("button", { name: /probe mcp remote/i }));
    await waitFor(() => expect(screen.getAllByText("search").length).toBeGreaterThan(1));
    expect(client.probeAdminMcp).toHaveBeenCalledWith("remote");

    await userEvent.click(screen.getByRole("tab", { name: /browser/i }));
    await userEvent.click(screen.getByRole("button", { name: /probe browser/i }));
    expect(await screen.findByText(/active contexts: 3/i)).toBeInTheDocument();
  });

  it("shows env-reference and default badges without secret values", async () => {
    renderWorkbench();

    await userEvent.click(await screen.findByRole("button", { name: /agents/i }));
    expect(screen.getByText(/default: "openai\/default"/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /providers/i }));
    expect(await screen.findByText("${ADMIN_TEST_OPENAI_KEY}")).toBeInTheDocument();
    expect(screen.queryByText(/sk-expanded/i)).not.toBeInTheDocument();
  });
});
