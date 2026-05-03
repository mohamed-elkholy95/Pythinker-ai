import { ApiError } from "./api";
import type { AdminConfigBackup } from "./types";

async function request<T>(
  url: string,
  token: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(url, {
    ...(init ?? {}),
    headers: {
      ...(init?.headers ?? {}),
      Authorization: `Bearer ${token}`,
    },
    credentials: "same-origin",
  });
  if (!res.ok) {
    throw new ApiError(res.status, `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

export interface AdminOverview {
  version: string;
  uptime_s: number;
  workspace: string;
  config_path: string;
  gateway: { host: string; port: number };
  api: { host: string; port: number };
  websocket: { host: string; port: number; path: string };
  agent: {
    provider: string;
    model: string;
    configured_model: string;
  };
  channels: Array<{ name: string; enabled: boolean }>;
  local_admin: boolean;
}

export interface AdminSessionRow {
  key: string;
  channel: string;
  chat_id: string;
  created_at: string | null;
  updated_at: string | null;
  preview: string;
  title?: string;
  pinned?: boolean;
  archived?: boolean;
  model_override?: string | null;
  usage: { used: number; limit: number };
}

export interface AdminModelRow {
  name: string;
  source: string;
  active: boolean;
}

export interface AdminModels {
  provider: string;
  active_model: string;
  models: AdminModelRow[];
}

export interface AdminUsage {
  last_turn: Record<string, number>;
  sessions: AdminSessionRow[];
  consumption: {
    total_tokens: number;
    cost: number | null;
    currency: string | null;
  };
  ledger?: Record<string, unknown>;
}

export interface AdminConfigPayload {
  config: Record<string, unknown>;
  secret_paths: string[];
  env_references?: Record<string, { env_var: string; is_secret: boolean }>;
  field_defaults?: Record<string, unknown>;
  restart_required_paths: string[];
}

export type JsonSchemaNode = {
  type?: string | string[];
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  minimum?: number;
  maximum?: number;
  properties?: Record<string, JsonSchemaNode>;
  items?: JsonSchemaNode;
  additionalProperties?: boolean | JsonSchemaNode;
  anyOf?: JsonSchemaNode[];
  oneOf?: JsonSchemaNode[];
  required?: string[];
};

export interface AdminConfigSchemaPayload {
  schema: JsonSchemaNode;
  secret_paths: string[];
  field_defaults?: Record<string, unknown>;
  restart_required_paths: string[];
}

interface AdminConfigBackupsPayload {
  backups: AdminConfigBackup[];
}

export interface AdminProviderSurfaceRow {
  name: string;
  backend: string;
  is_oauth: boolean;
  is_local: boolean;
  is_gateway: boolean;
  is_direct: boolean;
  configured: boolean;
  key_set: boolean;
  api_base: string | null;
  active: boolean;
}

export interface AdminSubagentStatus {
  task_id: string;
  label: string;
  task_description: string;
  started_at_wall?: number;
  started_at_iso?: string;
  elapsed_s: number;
  phase: string;
  iteration: number;
  tool_events: Array<Record<string, unknown> & { name?: string; status?: string; detail?: string }>;
  usage: Record<string, number>;
  stop_reason?: string | null;
  error?: string | null;
  session_key?: string;
}

export interface AdminLiveSession {
  key: string;
  in_flight: number;
  subagent_count: number;
  subagents: AdminSubagentStatus[];
}

export interface AdminProviderRoutingSurface {
  model: string;
  matched_spec: string | null;
  matched_keyword: string | null;
  match_phase: string;
  resolved_api_base: string | null;
}

export interface AdminSurfaces {
  overview: AdminOverview;
  channels: {
    total: number;
    running: number;
    rows: Array<Record<string, unknown> & { name: string }>;
  };
  sessions: { sessions: AdminSessionRow[] };
  usage: AdminUsage;
  models: AdminModels;
  agents: {
    default_agent_id: string;
    policy_enabled: boolean;
    manifests_dir: string | null;
    total: number;
    agents: Array<Record<string, unknown> & { id?: string; name?: string }>;
    routing?: AdminProviderRoutingSurface;
    live?: { sessions: AdminLiveSession[] };
  };
  providers?: { rows: AdminProviderSurfaceRow[] };
  tools?: Record<string, unknown>;
  runtime?: Record<string, unknown>;
  skills: {
    total: number;
    disabled: number;
    rows: Array<Record<string, unknown> & { name: string; source?: string }>;
  };
  cron: {
    status: Record<string, unknown>;
    jobs: Array<Record<string, unknown> & { id?: string; name?: string }>;
  };
  dreams: Record<string, unknown>;
  config: AdminConfigPayload;
  appearance: Record<string, unknown>;
  infrastructure: Record<string, unknown>;
  debug: Record<string, unknown>;
  logs: {
    entries: Array<Record<string, unknown> & { level?: string; message?: string }>;
    sources?: string[];
    truncated?: boolean;
  };
}

export async function fetchAdminOverview(token: string): Promise<AdminOverview> {
  return request<AdminOverview>("/api/admin/overview", token);
}

export async function fetchAdminSessions(
  token: string,
): Promise<AdminSessionRow[]> {
  const body = await request<{ sessions: AdminSessionRow[] }>(
    "/api/admin/sessions",
    token,
  );
  return body.sessions;
}

export async function fetchAdminModels(token: string): Promise<AdminModels> {
  return request<AdminModels>("/api/admin/models", token);
}

export async function fetchAdminUsage(token: string): Promise<AdminUsage> {
  return request<AdminUsage>("/api/admin/usage", token);
}

export async function fetchAdminConfig(
  token: string,
): Promise<AdminConfigPayload> {
  return request<AdminConfigPayload>("/api/admin/config", token);
}

export async function fetchAdminConfigSchema(
  token: string,
): Promise<AdminConfigSchemaPayload> {
  return request<AdminConfigSchemaPayload>("/api/admin/config/schema", token);
}

export async function fetchAdminConfigBackups(
  token: string,
): Promise<AdminConfigBackup[]> {
  const body = await request<AdminConfigBackupsPayload>(
    "/api/admin/config/backups",
    token,
  );
  return body.backups;
}

export async function fetchAdminSurfaces(token: string): Promise<AdminSurfaces> {
  return request<AdminSurfaces>("/api/admin/surfaces", token);
}
