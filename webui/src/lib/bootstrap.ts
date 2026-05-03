import type { BootstrapResponse as BaseBootstrapResponse } from "./types";

/**
 * Bootstrap response shape consumed by ``App.tsx``.
 *
 * Extends the wire-level ``BootstrapResponse`` with ``voice_enabled`` — a
 * flag that gates the composer mic button. The full T11 transcription
 * pipeline (recording, transcription, transcript paste-in) is deferred
 * because the Python ``websockets`` library can't serve ``POST
 * /api/transcribe``; the gateway therefore always reports
 * ``voice_enabled: false`` today. The flag is still plumbed through so a
 * future enable doesn't require a UI rewrite.
 */
export interface BootstrapResponse extends BaseBootstrapResponse {
  voice_enabled?: boolean;
}

/**
 * Fetch a short-lived token + the WebSocket path from the gateway's
 * ``/webui/bootstrap`` endpoint. Localhost-only on the server side.
 */
export async function fetchBootstrap(
  baseUrl: string = "",
): Promise<BootstrapResponse> {
  const res = await fetch(`${baseUrl}/webui/bootstrap`, {
    method: "GET",
    credentials: "same-origin",
  });
  if (!res.ok) {
    throw new Error(`bootstrap failed: HTTP ${res.status}`);
  }
  const body = (await res.json()) as BootstrapResponse;
  if (!body.token || !body.ws_path) {
    throw new Error("bootstrap response missing token or ws_path");
  }
  return body;
}

/** Derive a WebSocket URL from the current window location and the server-provided path.
 *
 * Keeps the path segment exactly as the server registered it: the root ``/``
 * stays ``/`` and non-root paths are not given an extra trailing slash. This
 * matters because some WS servers dispatch handshakes based on the literal
 * path, not a normalised form.
 */
export function deriveWsUrl(wsPath: string, token: string): string {
  const path = wsPath && wsPath.startsWith("/") ? wsPath : `/${wsPath || ""}`;
  const query = `?token=${encodeURIComponent(token)}`;
  if (typeof window === "undefined") {
    return `ws://127.0.0.1:8765${path}${query}`;
  }
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.host;
  return `${scheme}://${host}${path}${query}`;
}
