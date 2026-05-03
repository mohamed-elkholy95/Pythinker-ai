import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminDashboard } from "@/components/admin/AdminDashboard";
import { ClientProvider } from "@/providers/ClientProvider";

vi.mock("@/lib/admin-api", () => ({
  fetchAdminOverview: vi.fn(async () => ({
    version: "0.1.0",
    uptime_s: 42,
    workspace: "/tmp/workspace",
    config_path: "/tmp/config.json",
    gateway: { host: "127.0.0.1", port: 18790 },
    api: { host: "127.0.0.1", port: 8900 },
    websocket: { host: "127.0.0.1", port: 8765, path: "/" },
    agent: {
      provider: "openai",
      model: "openai/gpt-4o",
      configured_model: "openai/gpt-4o",
    },
    channels: [{ name: "websocket", enabled: true }],
    local_admin: true,
  })),
  fetchAdminSessions: vi.fn(async () => [
    {
      key: "slack:C123",
      channel: "slack",
      chat_id: "C123",
      created_at: null,
      updated_at: null,
      preview: "hello",
      usage: { used: 10, limit: 100 },
    },
  ]),
  fetchAdminModels: vi.fn(async () => ({
    provider: "openai",
    active_model: "openai/gpt-4o",
    models: [{ name: "openai/gpt-4o", source: "configured", active: true }],
  })),
  fetchAdminUsage: vi.fn(async () => ({
    last_turn: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
    sessions: [],
    consumption: { total_tokens: 15, cost: null, currency: null },
  })),
  fetchAdminConfig: vi.fn(async () => ({
    config: { logging: { level: "INFO" } },
    secret_paths: ["providers.openai.api_key"],
    restart_required_paths: ["*"],
  })),
  fetchAdminConfigSchema: vi.fn(async () => ({
    schema: {
      type: "object",
      properties: {
        agents: { type: "object", properties: {} },
        providers: { type: "object", properties: {} },
        logging: {
          type: "object",
          properties: { level: { type: "string", title: "Level" } },
        },
      },
    },
    secret_paths: ["providers.openai.api_key"],
    restart_required_paths: ["*"],
  })),
  fetchAdminConfigBackups: vi.fn(async () => []),
  fetchAdminSurfaces: vi.fn(async () => ({
    overview: {
      version: "0.1.0",
      uptime_s: 42,
      workspace: "/tmp/workspace",
      config_path: "/tmp/config.json",
      gateway: { host: "127.0.0.1", port: 18790 },
      api: { host: "127.0.0.1", port: 8900 },
      websocket: { host: "127.0.0.1", port: 8765, path: "/" },
      agent: {
        provider: "openai",
        model: "openai/gpt-4o",
        configured_model: "openai/gpt-4o",
      },
      channels: [{ name: "websocket", enabled: true }],
      local_admin: true,
    },
    channels: {
      total: 1,
      running: 1,
      rows: [{ name: "websocket", enabled: true, running: true }],
    },
    sessions: {
      sessions: [
        {
          key: "slack:C123",
          channel: "slack",
          chat_id: "C123",
          created_at: null,
          updated_at: null,
          preview: "hello",
          usage: { used: 10, limit: 100 },
        },
      ],
    },
    usage: {
      last_turn: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
      sessions: [],
      consumption: { total_tokens: 15, cost: null, currency: null },
    },
    models: {
      provider: "openai",
      active_model: "openai/gpt-4o",
      models: [{ name: "openai/gpt-4o", source: "configured", active: true }],
    },
    agents: {
      default_agent_id: "default",
      policy_enabled: false,
      manifests_dir: null,
      total: 0,
      agents: [],
    },
    skills: {
      total: 1,
      disabled: 0,
      rows: [{ name: "memory", source: "builtin", description: "Memory" }],
    },
    cron: {
      status: { enabled: true, jobs: 1 },
      jobs: [{ id: "dream", name: "Dream" }],
    },
    dreams: { schedule: "every 2h", max_batch_size: 20 },
    config: {
      config: { logging: { level: "INFO" } },
      secret_paths: ["providers.openai.api_key"],
      restart_required_paths: ["*"],
    },
    appearance: { theme: "system", mode: "light/dark" },
    infrastructure: {
      workspace: "/tmp/workspace",
      config_path: "/tmp/config.json",
      gateway: { host: "127.0.0.1", port: 18790 },
      api: { host: "127.0.0.1", port: 8900 },
      websocket: { host: "127.0.0.1", port: 8765, path: "/" },
      telemetry: { sink: "off", jsonl_path: null },
      mcp_servers: 0,
      ssrf_whitelist: [],
    },
    debug: {
      policy_enabled: false,
      blocked_senders: 0,
      queue_depth: { inbound: 0, outbound: 0 },
      subagents_running: 0,
      session_cache_max: 256,
    },
    logs: { entries: [{ level: "info", message: "hello log" }], sources: [] },
  })),
}));

function renderDashboard() {
  const client = {
    setAdminConfig: vi.fn(async () => ({
      path: "logging.level",
      restartRequired: true,
    })),
    unsetAdminConfig: vi.fn(),
    replaceAdminSecret: vi.fn(),
  };
  render(
    <ClientProvider
      client={client as never}
      token="tok"
      modelName="openai/gpt-4o"
      voiceEnabled={false}
    >
      <AdminDashboard />
    </ClientProvider>,
  );
  return client;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AdminDashboard", () => {
  it("renders runtime, sessions, models, usage, and redacted config panels", async () => {
    renderDashboard();

    expect(await screen.findByLabelText("Admin breadcrumb")).toHaveTextContent(
      "Overview",
    );
    expect(screen.getByText("/tmp/workspace")).toBeInTheDocument();
    expect(screen.getByText("slack:C123")).toBeInTheDocument();
    expect(screen.getAllByText("openai/gpt-4o").length).toBeGreaterThan(0);
    expect(screen.getByText("providers.openai.api_key")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Channels" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Agents" })).toBeInTheDocument();
  });

  it("switches between control-console tabs", async () => {
    renderDashboard();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Channels" }));
    expect(screen.getByLabelText("Admin breadcrumb")).toHaveTextContent(
      "Channels",
    );
    expect(screen.getByText("Channel Health")).toBeInTheDocument();
    expect(screen.getByText("websocket")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Logs" }));
    expect(screen.getByLabelText("Admin breadcrumb")).toHaveTextContent("Logs");
    expect(screen.getByText("Log Feed")).toBeInTheDocument();
    expect(screen.getByText("hello log")).toBeInTheDocument();
  });

  it("renders the config workbench in the config tab", async () => {
    renderDashboard();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Config" }));
    expect(await screen.findByText("Config Workbench")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /agents/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: /providers/i }).length).toBeGreaterThan(0);
  });
});
