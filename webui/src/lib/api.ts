import type { ChatSummary, SearchHit } from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

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

function splitKey(key: string): { channel: string; chatId: string } {
  const idx = key.indexOf(":");
  if (idx === -1) return { channel: "", chatId: key };
  return { channel: key.slice(0, idx), chatId: key.slice(idx + 1) };
}

export async function listSessions(
  token: string,
  base: string = "",
): Promise<ChatSummary[]> {
  type Row = {
    key: string;
    created_at: string | null;
    updated_at: string | null;
    preview?: string;
    title?: string;
    pinned?: boolean;
    archived?: boolean;
    model_override?: string | null;
  };
  const body = await request<{ sessions: Row[] }>(
    `${base}/api/sessions`,
    token,
  );
  return body.sessions.map((s) => ({
    key: s.key,
    ...splitKey(s.key),
    createdAt: s.created_at,
    updatedAt: s.updated_at,
    preview: s.preview ?? "",
    ...(s.title ? { title: s.title } : {}),
    pinned: !!s.pinned,
    archived: !!s.archived,
    modelOverride: s.model_override ?? null,
  }));
}

/** Signed image URL attached to a historical user message. The server
 * emits these in place of raw on-disk paths so the client can render
 * previews without learning where media lives on disk. Each URL is a
 * self-authenticating ``/api/media/...`` route (see backend
 * ``_sign_media_path``) safe to drop into an ``<img src>`` attribute. */
export interface SessionMediaUrl {
  url: string;
  name?: string;
}

export async function fetchSessionMessages(
  token: string,
  key: string,
  base: string = "",
): Promise<{
  key: string;
  created_at: string | null;
  updated_at: string | null;
  messages: Array<{
    role: string;
    content: string;
    timestamp?: string;
    tool_calls?: unknown;
    tool_call_id?: string;
    name?: string;
    /** Present on ``user`` turns that attached images. Paths have already
     * been stripped server-side; only the signed fetch URLs survive. */
    media_urls?: SessionMediaUrl[];
  }>;
}> {
  return request(
    `${base}/api/sessions/${encodeURIComponent(key)}/messages`,
    token,
  );
}

export async function deleteSession(
  token: string,
  key: string,
  base: string = "",
): Promise<boolean> {
  const body = await request<{ deleted: boolean }>(
    `${base}/api/sessions/${encodeURIComponent(key)}/delete`,
    token,
  );
  return body.deleted;
}

export interface SessionUsage {
  used: number;
  limit: number;
}

export async function fetchSessionUsage(
  token: string,
  key: string,
  base: string = "",
): Promise<SessionUsage> {
  return request<SessionUsage>(
    `${base}/api/sessions/${encodeURIComponent(key)}/usage`,
    token,
  );
}

export interface CommandRow {
  name: string;
  summary: string;
  usage: string;
}

export async function fetchCommands(
  token: string,
  base: string = "",
): Promise<CommandRow[]> {
  const body = await request<{ commands: CommandRow[] }>(
    `${base}/api/commands`,
    token,
  );
  return body.commands;
}

export async function searchSessions(
  token: string,
  query: string,
  opts: {
    offset?: number;
    limit?: number;
    base?: string;
    signal?: AbortSignal;
  } = {},
): Promise<{
  results: SearchHit[];
  offset: number;
  limit: number;
  hasMore: boolean;
}> {
  const offset = opts.offset ?? 0;
  const limit = opts.limit ?? 50;
  const base = opts.base ?? "";
  type WireHit = {
    session_key: string;
    message_index: number;
    role: string;
    snippet: string;
    match_offsets: Array<[number, number]>;
    title?: string;
    archived?: boolean;
  };
  const url =
    `${base}/api/search?q=${encodeURIComponent(query)}` +
    `&offset=${offset}&limit=${limit}`;
  const body = await request<{
    results: WireHit[];
    offset: number;
    limit: number;
    has_more: boolean;
  }>(url, token, opts.signal ? { signal: opts.signal } : undefined);
  return {
    results: body.results.map((r) => ({
      sessionKey: r.session_key,
      messageIndex: r.message_index,
      role: r.role,
      snippet: r.snippet,
      matchOffsets: r.match_offsets,
      title: r.title ?? "",
      archived: !!r.archived,
    })),
    offset: body.offset,
    limit: body.limit,
    hasMore: !!body.has_more,
  };
}

export async function togglePinSession(
  token: string,
  key: string,
  base: string = "",
): Promise<boolean> {
  const body = await request<{ pinned: boolean }>(
    `${base}/api/sessions/${encodeURIComponent(key)}/pin`,
    token,
  );
  return !!body.pinned;
}

export async function toggleArchiveSession(
  token: string,
  key: string,
  base: string = "",
): Promise<boolean> {
  const body = await request<{ archived: boolean }>(
    `${base}/api/sessions/${encodeURIComponent(key)}/archive`,
    token,
  );
  return !!body.archived;
}

export interface ModelRow {
  name: string;
  is_default: boolean;
}

export async function fetchAvailableModels(
  token: string,
  base: string = "",
): Promise<ModelRow[]> {
  const body = await request<{ models: ModelRow[] }>(`${base}/api/models`, token);
  return body.models;
}
