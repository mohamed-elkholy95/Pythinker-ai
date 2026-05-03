import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConfigWorkbench } from "@/components/admin/config/ConfigWorkbench";
import { RuntimeView } from "@/components/admin/config/services/RuntimeView";
import type { PythinkerClient } from "@/lib/pythinker-client";
import { ClientProvider } from "@/providers/ClientProvider";

vi.mock("@/lib/admin-api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/admin-api")>("@/lib/admin-api");
  return {
    ...actual,
    fetchAdminConfigBackups: vi.fn().mockResolvedValue([]),
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
          runtime: { type: "object", properties: {} },
          gateway: { type: "object", properties: {} },
          api: { type: "object", properties: {} },
          logging: { type: "object", properties: {} },
          updates: { type: "object", properties: {} },
          cli: { type: "object", properties: {} },
        },
      },
      secret_paths: ["providers.openai.api_key"],
      restart_required_paths: ["*"],
    }),
  };
});

const client = {
  setAdminConfig: vi.fn().mockResolvedValue({ ok: true, restart_required: true }),
  unsetAdminConfig: vi.fn().mockResolvedValue({ ok: true, restart_required: true }),
  replaceAdminSecret: vi.fn().mockResolvedValue({ ok: true, restart_required: true }),
};

const surfaces = {
  config: {
    config: {
      agents: { defaults: { model: "openai/gpt" } },
      providers: { openai: { api_key: "placeholder-live-secret" } },
      tools: { ssrf_whitelist: ["100.64.0.0/10"] },
      runtime: { policy_enabled: false },
      gateway: { host: "127.0.0.1", port: 8765 },
      api: { host: "127.0.0.1", port: 8900 },
      logging: { level: "INFO" },
      updates: { check: true },
      cli: { tui: { theme: "default" } },
    },
    secret_paths: [],
    restart_required_paths: ["*"],
  },
  agents: {
    routing: {
      model: "openai/gpt",
      matched_spec: "openai",
      matched_keyword: "openai",
      resolved_api_base: "https://api.openai.com/v1",
    },
  },
  channels: { rows: [{ name: "websocket", enabled: true, running: true, required_secrets: [] }] },
  providers: { rows: [] },
  tools: { ssrf: { whitelist: ["100.64.0.0/10"], blocked_categories: ["rfc1918", "loopback"] } },
  runtime: { policy_enabled: false, manifests_dir: null },
  overview: {
    workspace: "/tmp/workspace",
    gateway: { host: "127.0.0.1", port: 8765 },
    api: { host: "127.0.0.1", port: 8900 },
  },
};

describe("ConfigWorkbench", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("stages and applies a config edit", async () => {
    render(
      <ClientProvider client={client as unknown as PythinkerClient} token="admin-token">
        <ConfigWorkbench token="admin-token" surfaces={surfaces as never} onRefresh={vi.fn()} />
      </ClientProvider>,
    );

    await userEvent.click(await screen.findByRole("button", { name: /agents/i }));
    await userEvent.clear(screen.getByLabelText("Model"));
    await userEvent.type(screen.getByLabelText("Model"), "openrouter/auto");
    expect(screen.getByText("Pending changes")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /apply/i }));

    await waitFor(() =>
      expect(client.setAdminConfig).toHaveBeenCalledWith(
        "agents.defaults.model",
        "openrouter/auto",
      ),
    );
  });

  it("copies redacted JSON and exports redacted env lines", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(
      <ClientProvider client={client as unknown as PythinkerClient} token="admin-token">
        <ConfigWorkbench token="admin-token" surfaces={surfaces as never} onRefresh={vi.fn()} />
      </ClientProvider>,
    );

    await userEvent.click(await screen.findByRole("button", { name: /copy as json/i }));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('"agents"'));

    await userEvent.click(screen.getByRole("button", { name: /export as env/i }));
    expect(writeText).toHaveBeenCalledWith(
      expect.stringContaining("PYTHINKER_AGENTS_DEFAULTS_MODEL="),
    );
    expect(writeText).not.toHaveBeenCalledWith(expect.stringContaining("sk-"));
  });

  it("renders provider matrix filters and opens secret flow for unconfigured provider", async () => {
    const providerSurfaces = {
      ...surfaces,
      providers: {
        rows: [
          { name: "openai", backend: "openai_compat", is_oauth: false, is_local: false, is_gateway: false, is_direct: true, configured: true, key_set: true, api_base: "https://api.openai.com/v1", active: true },
          { name: "openrouter", backend: "openai_compat", is_oauth: false, is_local: false, is_gateway: true, is_direct: true, configured: false, key_set: false, api_base: "https://openrouter.ai/api/v1", active: false },
        ],
      },
    };

    render(
      <ClientProvider client={client as unknown as PythinkerClient} token="admin-token">
        <ConfigWorkbench token="admin-token" surfaces={providerSurfaces as never} onRefresh={vi.fn()} />
      </ClientProvider>,
    );

    await userEvent.click(await screen.findByRole("button", { name: /providers/i }));

    expect(screen.getByText("openai")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /configured/i })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /add key for openrouter/i }));
    expect(screen.getByRole("dialog", { name: /replace secret/i })).toBeInTheDocument();
  });

  it("searches paths, exposes raw and diff modes, and confirms unset", async () => {
    render(
      <ClientProvider client={client as unknown as PythinkerClient} token="admin-token">
        <ConfigWorkbench token="admin-token" surfaces={surfaces as never} onRefresh={vi.fn()} />
      </ClientProvider>,
    );

    await userEvent.type(await screen.findByPlaceholderText(/search paths/i), "api key");
    await userEvent.click(await screen.findByRole("button", { name: /providers\.openai\.api_key/i }));
    expect(screen.getAllByText("providers.openai.api_key").length).toBeGreaterThan(0);

    await userEvent.click(screen.getByRole("button", { name: /raw json/i }));
    expect(screen.getByText(/redacted effective config/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /guided/i }));
    await userEvent.click(screen.getByRole("button", { name: /unset focused path/i }));
    await userEvent.click(await screen.findByRole("button", { name: /stage unset/i }));
    await userEvent.click(screen.getByRole("button", { name: /^diff$/i }));
    expect(screen.getAllByText(/after: unset/i).length).toBeGreaterThan(0);
  });

  it("validates SSRF whitelist edits before staging", async () => {
    const stage = vi.fn();
    render(
      <RuntimeView
        config={{ tools: { ssrf_whitelist: ["100.64.0.0/10"] }, runtime: {} }}
        surfaces={{ tools: { ssrf: { whitelist: ["100.64.0.0/10"], blocked_categories: ["rfc1918", "loopback"] } } } as never}
        onFocusPath={vi.fn()}
        onStage={stage}
      />,
    );

    await userEvent.type(screen.getByLabelText(/ssrf whitelist cidr/i), "0.0.0.0/0");
    expect(screen.getByText(/overbroad ssrf whitelist range/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /stage ssrf whitelist/i })).toBeDisabled();

    await userEvent.clear(screen.getByLabelText(/ssrf whitelist cidr/i));
    await userEvent.type(screen.getByLabelText(/ssrf whitelist cidr/i), "203.0.113.0/24");
    await userEvent.click(screen.getByRole("button", { name: /stage ssrf whitelist/i }));
    expect(stage).toHaveBeenCalledWith("tools.ssrf_whitelist", ["100.64.0.0/10", "203.0.113.0/24"]);
  });

  it("replaces a secret without rendering the raw value", async () => {
    render(
      <ClientProvider client={client as unknown as PythinkerClient} token="admin-token">
        <ConfigWorkbench token="admin-token" surfaces={surfaces as never} onRefresh={vi.fn()} />
      </ClientProvider>,
    );

    await userEvent.click(await screen.findByRole("button", { name: /providers/i }));
    expect(screen.queryByText("placeholder-live-secret")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /replace secret/i }));
    await userEvent.type(screen.getByLabelText(/new secret/i), "placeholder-new-secret");
    await userEvent.click(screen.getByRole("button", { name: /^replace secret$/i }));

    expect(screen.queryByText("placeholder-new-secret")).not.toBeInTheDocument();
    expect(client.replaceAdminSecret).toHaveBeenCalledWith("providers.openai.api_key", "placeholder-new-secret");
  });
});
