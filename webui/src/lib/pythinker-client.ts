import type {
  AdminBindTestResult,
  AdminBrowserProbeResult,
  AdminChannelTestResult,
  AdminConfigMutationResult,
  AdminMcpProbeResult,
  ConnectionStatus,
  InboundEvent,
  Outbound,
  OutboundMedia,
} from "./types";

/** WebSocket readyState constants, referenced by value to stay portable
 * across runtimes that don't expose a global ``WebSocket`` (tests, SSR). */
const WS_OPEN = 1;
const WS_CLOSING = 2;

type Unsubscribe = () => void;
type EventHandler = (ev: InboundEvent) => void;
type StatusHandler = (status: ConnectionStatus) => void;
/** Frames that carry no ``chat_id`` (transcription replies, top-level errors).
 * These can't be dispatched through ``onChat`` because the chat-id-based
 * routing in ``handleMessage`` would silently drop them. */
type TranscriptionHandler = (ev: InboundEvent) => void;

/** Structured connection-level errors surfaced to the UI.
 *
 * These are *not* InboundEvent errors from the server application layer —
 * those arrive as ``{event: "error"}`` messages via ``onChat``. These are
 * transport-level or protocol-level faults the UI should make visible so
 * the user understands *why* their action failed (as opposed to silently
 * reconnecting under the hood).
 */
export type StreamError =
  /** Server rejected the inbound frame as too large (WS close code 1009).
   * Typically means the user attached images whose base64 size exceeded
   * ``maxMessageBytes`` on the server. */
  | { kind: "message_too_big" };

type ErrorHandler = (error: StreamError) => void;

interface PendingNewChat {
  resolve: (chatId: string) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

export type AdminConfigResult = AdminConfigMutationResult;

interface PendingAdminRequest {
  resolve: (result: unknown) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

export interface PythinkerClientOptions {
  url: string;
  reconnect?: boolean;
  /** Called when a connection drops so the app can refresh its token. */
  onReauth?: () => Promise<string | null>;
  /** Inject a custom WebSocket factory (used by unit tests). */
  socketFactory?: (url: string) => WebSocket;
  /** Delay-cap for reconnect backoff (ms). */
  maxBackoffMs?: number;
}

/**
 * Singleton WebSocket client that multiplexes chat streams.
 *
 * One socket carries many chat_ids: the server tags every outbound event with
 * ``chat_id``, and this class fans those events out to handlers registered
 * per chat. Reconnects are transparent and re-attach every known chat_id.
 */
export class PythinkerClient {
  private socket: WebSocket | null = null;
  private statusHandlers = new Set<StatusHandler>();
  private errorHandlers = new Set<ErrorHandler>();
  // chat_id -> handlers listening on it
  private chatHandlers = new Map<string, Set<EventHandler>>();
  // Subscribers to chat-id-less frames: ``transcription_result`` and the
  // bare ``{event:"error", request_id}`` reply that the transcribe pipeline
  // emits. The hook filters by ``request_id`` on top of this fan-out.
  private transcriptionHandlers = new Set<TranscriptionHandler>();
  // chat_ids we've attached to since connect; re-attached after reconnects
  private knownChats = new Set<string>();
  private pendingNewChat: PendingNewChat | null = null;
  private pendingAdminConfig = new Map<string, PendingAdminRequest>();
  // Frames queued while the socket is not yet OPEN
  private sendQueue: Outbound[] = [];
  private reconnectAttempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly shouldReconnect: boolean;
  private readonly maxBackoffMs: number;
  private readonly socketFactory: (url: string) => WebSocket;
  private currentUrl: string;
  private status_: ConnectionStatus = "idle";
  private readyChatId: string | null = null;
  // Set by ``close()`` so the onclose handler knows the drop was intentional
  // and must not schedule a reconnect or flip status back to "reconnecting".
  private intentionallyClosed = false;

  constructor(private options: PythinkerClientOptions) {
    this.shouldReconnect = options.reconnect ?? true;
    this.maxBackoffMs = options.maxBackoffMs ?? 15_000;
    this.socketFactory =
      options.socketFactory ?? ((url) => new WebSocket(url));
    this.currentUrl = options.url;
  }

  get status(): ConnectionStatus {
    return this.status_;
  }

  get defaultChatId(): string | null {
    return this.readyChatId;
  }

  /** Swap the URL (e.g. after fetching a fresh token) then reconnect. */
  updateUrl(url: string): void {
    this.currentUrl = url;
  }

  onStatus(handler: StatusHandler): Unsubscribe {
    this.statusHandlers.add(handler);
    handler(this.status_);
    return () => {
      this.statusHandlers.delete(handler);
    };
  }

  /** Subscribe to transport-level faults (see :type:`StreamError`). */
  onError(handler: ErrorHandler): Unsubscribe {
    this.errorHandlers.add(handler);
    return () => {
      this.errorHandlers.delete(handler);
    };
  }

  /** Subscribe to chat-id-less server frames (``transcription_result`` and
   * top-level ``error`` replies that carry a ``request_id`` instead of a
   * ``chat_id``). The caller is responsible for filtering by ``request_id``.
   */
  onTranscription(handler: TranscriptionHandler): Unsubscribe {
    this.transcriptionHandlers.add(handler);
    return () => {
      this.transcriptionHandlers.delete(handler);
    };
  }

  /** Subscribe to events for a given chat_id. Auto-attaches on the next open. */
  onChat(chatId: string, handler: EventHandler): Unsubscribe {
    let handlers = this.chatHandlers.get(chatId);
    if (!handlers) {
      handlers = new Set();
      this.chatHandlers.set(chatId, handlers);
    }
    handlers.add(handler);
    this.attach(chatId);
    return () => {
      const current = this.chatHandlers.get(chatId);
      if (!current) return;
      current.delete(handler);
      if (current.size === 0) this.chatHandlers.delete(chatId);
    };
  }

  connect(): void {
    if (this.socket && this.socket.readyState < WS_CLOSING) return;
    this.intentionallyClosed = false;
    this.setStatus("connecting");
    const sock = this.socketFactory(this.currentUrl);
    this.socket = sock;
    sock.onopen = () => this.handleOpen();
    sock.onmessage = (ev) => this.handleMessage(ev);
    sock.onerror = () => this.setStatus("error");
    sock.onclose = (ev) => this.handleClose(ev);
  }

  close(): void {
    this.intentionallyClosed = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    const sock = this.socket;
    this.socket = null;
    try {
      sock?.close();
    } catch {
      // ignore
    }
    this.setStatus("closed");
  }

  /** Ask the server to provision a new chat_id; resolves with the assigned id. */
  newChat(timeoutMs: number = 5_000): Promise<string> {
    if (this.pendingNewChat) {
      return Promise.reject(new Error("newChat already in flight"));
    }
    return new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pendingNewChat = null;
        reject(new Error("newChat timed out"));
      }, timeoutMs);
      this.pendingNewChat = { resolve, reject, timer };
      this.queueSend({ type: "new_chat" });
    });
  }

  attach(chatId: string): void {
    this.knownChats.add(chatId);
    if (this.socket?.readyState === WS_OPEN) {
      this.queueSend({ type: "attach", chat_id: chatId });
    }
  }

  sendMessage(chatId: string, content: string, media?: OutboundMedia[]): void {
    this.knownChats.add(chatId);
    const frame: Outbound =
      media && media.length > 0
        ? { type: "message", chat_id: chatId, content, media }
        : { type: "message", chat_id: chatId, content };
    this.queueSend(frame);
  }

  /** Cancel the in-flight assistant turn for *chatId*. */
  sendStop(chatId: string): void {
    this.queueSend({ type: "stop", chat_id: chatId });
  }

  /** Drop the last assistant reply and re-run the prior user message. */
  regenerate(chatId: string): void {
    this.queueSend({ type: "regenerate", chat_id: chatId });
  }

  /** Replace user message at *userMsgIndex* with *content* and re-run from there. */
  editAndResend(chatId: string, userMsgIndex: number, content: string): void {
    this.queueSend({
      type: "edit",
      chat_id: chatId,
      user_msg_index: userMsgIndex,
      content,
    });
  }

  /** Set the per-chat model override. An empty *model* clears the override. */
  setModel(chatId: string, model: string): void {
    this.knownChats.add(chatId);
    this.queueSend({ type: "set_model", chat_id: chatId, model });
  }

  /** Submit a base64-encoded audio blob for server-side transcription. The
   * server replies on the same socket with a ``transcription_result`` frame
   * (or a chat-id-less ``error``); both fan out via ``onTranscription``. The
   * caller correlates request and reply via *requestId*. */
  transcribe(audioBase64: string, format: string, requestId: string): void {
    this.queueSend({
      type: "transcribe",
      audio_base64: audioBase64,
      format,
      request_id: requestId,
    });
  }

  setAdminConfig(
    path: string,
    value: unknown,
    timeoutMs: number = 5_000,
  ): Promise<AdminConfigResult> {
    return this.sendAdminConfigRequest(
      { type: "admin_config_set", path, value },
      timeoutMs,
    );
  }

  unsetAdminConfig(
    path: string,
    timeoutMs: number = 5_000,
  ): Promise<AdminConfigResult> {
    return this.sendAdminConfigRequest(
      { type: "admin_config_unset", path },
      timeoutMs,
    );
  }

  replaceAdminSecret(
    path: string,
    value: string,
    timeoutMs: number = 5_000,
  ): Promise<AdminConfigResult> {
    return this.sendAdminConfigRequest(
      { type: "admin_config_replace_secret", path, value },
      timeoutMs,
    );
  }

  restoreAdminConfigBackup(
    id: string,
    timeoutMs: number = 5_000,
  ): Promise<AdminConfigMutationResult> {
    return this.sendAdminConfigRequest(
      { type: "admin_config_restore_backup", backup_id: id },
      timeoutMs,
    );
  }

  testAdminBind(
    host: string,
    port: number,
    timeoutMs: number = 5_000,
  ): Promise<AdminBindTestResult> {
    return this.sendAdminRequest<AdminBindTestResult>(
      { type: "admin_test_bind", host, port },
      timeoutMs,
    );
  }

  testAdminChannel(
    name: string,
    timeoutMs: number = 5_000,
  ): Promise<AdminChannelTestResult> {
    return this.sendAdminRequest<AdminChannelTestResult>(
      { type: "admin_test_channel", name },
      timeoutMs,
    );
  }

  probeAdminMcp(
    server: string,
    timeoutMs: number = 5_000,
  ): Promise<AdminMcpProbeResult> {
    return this.sendAdminRequest<AdminMcpProbeResult>(
      { type: "admin_mcp_probe", server },
      timeoutMs,
    );
  }

  probeAdminBrowser(timeoutMs: number = 5_000): Promise<AdminBrowserProbeResult> {
    return this.sendAdminRequest<AdminBrowserProbeResult>(
      { type: "admin_browser_probe" },
      timeoutMs,
    );
  }

  // -- internals ---------------------------------------------------------

  private requestId(): string {
    const randomUUID = globalThis.crypto?.randomUUID;
    if (typeof randomUUID === "function") return randomUUID.call(globalThis.crypto);
    return `admin-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  private sendAdminConfigRequest(
    frame:
      | { type: "admin_config_set"; path: string; value: unknown }
      | { type: "admin_config_unset"; path: string }
      | { type: "admin_config_replace_secret"; path: string; value: string }
      | { type: "admin_config_restore_backup"; backup_id: string },
    timeoutMs: number,
  ): Promise<AdminConfigMutationResult> {
    return this.sendAdminRequest<AdminConfigMutationResult>(frame, timeoutMs);
  }

  private sendAdminRequest<T>(
    frame:
      | { type: "admin_config_set"; path: string; value: unknown }
      | { type: "admin_config_unset"; path: string }
      | { type: "admin_config_replace_secret"; path: string; value: string }
      | { type: "admin_config_restore_backup"; backup_id: string }
      | { type: "admin_test_bind"; host: string; port: number }
      | { type: "admin_test_channel"; name: string }
      | { type: "admin_mcp_probe"; server: string }
      | { type: "admin_browser_probe" },
    timeoutMs: number,
  ): Promise<T> {
    const requestId = this.requestId();
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pendingAdminConfig.delete(requestId);
        reject(new Error("admin request timed out"));
      }, timeoutMs);
      this.pendingAdminConfig.set(requestId, {
        resolve: (result) => resolve(result as T),
        reject,
        timer,
      });
      this.queueSend({ ...frame, request_id: requestId });
    });
  }

  private setStatus(status: ConnectionStatus): void {
    if (this.status_ === status) return;
    this.status_ = status;
    for (const handler of this.statusHandlers) handler(status);
  }

  private handleOpen(): void {
    this.setStatus("open");
    this.reconnectAttempts = 0;
    // Re-attach every known chat_id so deliveries continue routing after a drop.
    for (const chatId of this.knownChats) {
      this.rawSend({ type: "attach", chat_id: chatId });
    }
    // Flush anything queued during reconnect.
    const queued = this.sendQueue.splice(0);
    for (const frame of queued) this.rawSend(frame);
  }

  private handleMessage(ev: MessageEvent): void {
    let parsed: InboundEvent;
    try {
      parsed = JSON.parse(typeof ev.data === "string" ? ev.data : "") as InboundEvent;
    } catch {
      return;
    }

    if (parsed.event === "ready") {
      this.readyChatId = parsed.chat_id;
      this.knownChats.add(parsed.chat_id);
      return;
    }

    if (parsed.event === "attached") {
      this.knownChats.add(parsed.chat_id);
      if (this.pendingNewChat) {
        clearTimeout(this.pendingNewChat.timer);
        this.pendingNewChat.resolve(parsed.chat_id);
        this.pendingNewChat = null;
      }
      this.dispatch(parsed.chat_id, parsed);
      return;
    }

    // Transcription replies and the chat-id-less error reply (which still
    // carries a ``request_id``) bypass the chat-id router; they fan out to
    // every transcription subscriber so the in-flight hook can filter by
    // ``request_id`` itself.
    if (parsed.event === "transcription_result") {
      this.dispatchTranscription(parsed);
      return;
    }
    if (
      parsed.event === "admin_config_saved" ||
      parsed.event === "admin_config_error" ||
      parsed.event === "admin_test_bind_result" ||
      parsed.event === "admin_test_channel_result" ||
      parsed.event === "admin_mcp_probe_result" ||
      parsed.event === "admin_browser_probe_result"
    ) {
      this.dispatchAdminConfig(parsed);
      return;
    }
    if (parsed.event === "error" && !parsed.chat_id) {
      this.dispatchTranscription(parsed);
      return;
    }

    const chatId = (parsed as { chat_id?: string }).chat_id;
    if (chatId) this.dispatch(chatId, parsed);
  }

  private dispatchTranscription(ev: InboundEvent): void {
    for (const h of this.transcriptionHandlers) {
      try {
        h(ev);
      } catch {
        // Swallow subscriber faults so one consumer can't block another's
        // delivery. Mirrors the ``emitError`` isolation policy.
      }
    }
  }

  private dispatchAdminConfig(ev: InboundEvent): void {
    if (
      ev.event !== "admin_config_saved" &&
      ev.event !== "admin_config_error" &&
      ev.event !== "admin_test_bind_result" &&
      ev.event !== "admin_test_channel_result" &&
      ev.event !== "admin_mcp_probe_result" &&
      ev.event !== "admin_browser_probe_result"
    ) {
      return;
    }
    const requestId = ev.request_id;
    if (!requestId) return;
    const pending = this.pendingAdminConfig.get(requestId);
    if (!pending) return;
    clearTimeout(pending.timer);
    this.pendingAdminConfig.delete(requestId);
    if (ev.event === "admin_config_error") {
      pending.reject(new Error(ev.detail ?? "admin config request failed"));
      return;
    }
    if (ev.event === "admin_config_saved") {
      pending.resolve({
        path: ev.path,
        restartRequired: !!ev.restart_required,
      });
      return;
    }
    pending.resolve(ev.result);
  }

  private dispatch(chatId: string, ev: InboundEvent): void {
    const handlers = this.chatHandlers.get(chatId);
    if (!handlers) return;
    for (const h of handlers) h(ev);
  }

  private handleClose(event?: { code?: number }): void {
    this.socket = null;
    if (this.pendingNewChat) {
      clearTimeout(this.pendingNewChat.timer);
      this.pendingNewChat.reject(new Error("socket closed"));
      this.pendingNewChat = null;
    }
    for (const [requestId, pending] of this.pendingAdminConfig) {
      clearTimeout(pending.timer);
      pending.reject(new Error("socket closed"));
      this.pendingAdminConfig.delete(requestId);
    }
    // Surface structured reasons *before* reconnect logic so the UI can
    // display the error even while the client transparently reconnects.
    // Browsers populate ``CloseEvent.code`` with the wire-level close code;
    // 1009 = Message Too Big (server's max frame guard).
    if (event?.code === 1009) {
      this.emitError({ kind: "message_too_big" });
    }
    if (this.intentionallyClosed || !this.shouldReconnect) {
      this.setStatus("closed");
      return;
    }
    this.scheduleReconnect();
  }

  private emitError(error: StreamError): void {
    // Isolate subscribers so a throwing handler cannot abort the surrounding
    // ``handleClose`` flow (which still owes us a reconnect decision + status
    // update). We deliberately swallow here: error reporting is best-effort
    // and must never be allowed to compound the failure it's reporting.
    for (const handler of this.errorHandlers) {
      try {
        handler(error);
      } catch {
        // best-effort: subscriber fault must not stall transport bookkeeping
      }
    }
  }

  private scheduleReconnect(): void {
    this.setStatus("reconnecting");
    const attempt = this.reconnectAttempts++;
    // Exponential backoff: 0.5s, 1s, 2s, 4s, capped.
    const delay = Math.min(500 * 2 ** attempt, this.maxBackoffMs);
    this.reconnectTimer = setTimeout(async () => {
      this.reconnectTimer = null;
      if (this.options.onReauth) {
        try {
          const refreshed = await this.options.onReauth();
          if (refreshed) this.currentUrl = refreshed;
        } catch {
          // fall through to retry with current URL
        }
      }
      this.connect();
    }, delay);
  }

  private queueSend(frame: Outbound): void {
    if (this.socket?.readyState === WS_OPEN) {
      this.rawSend(frame);
    } else {
      this.sendQueue.push(frame);
    }
  }

  private rawSend(frame: Outbound): void {
    if (!this.socket) return;
    try {
      this.socket.send(JSON.stringify(frame));
    } catch {
      // Send failure will materialize as a close; queue the frame for retry.
      this.sendQueue.push(frame);
    }
  }
}
