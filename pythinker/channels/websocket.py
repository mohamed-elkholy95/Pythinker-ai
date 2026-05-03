"""WebSocket server channel: pythinker acts as a WebSocket server and serves connected clients."""

from __future__ import annotations

import asyncio
import base64
import binascii
import email.utils
import hashlib
import hmac
import http
import json
import mimetypes
import re
import secrets
import ssl
import tempfile
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self
from urllib.parse import parse_qs, unquote, urlparse

from loguru import logger
from pydantic import Field, field_validator, model_validator
from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from pythinker.bus.events import OutboundMessage
from pythinker.bus.queue import MessageBus
from pythinker.channels.base import BaseChannel
from pythinker.config.paths import get_media_dir
from pythinker.config.schema import AgentDefaults, Base
from pythinker.utils.media_decode import (
    FileSizeExceeded,
    save_base64_data_url,
)

CONFIG_FIELDS = {
    "label": "WebSocket",
    "required_secrets": ["channels.websocket.token", "channels.websocket.token_issue_secret"],
    "fields": ["enabled", "host", "port", "path", "token", "allow_from", "streaming"],
    "local_dependency_checks": [],
}

if TYPE_CHECKING:
    from pythinker.admin.service import AdminService
    from pythinker.session.manager import SessionManager


def _strip_trailing_slash(path: str) -> str:
    if len(path) > 1 and path.endswith("/"):
        return path.rstrip("/")
    return path or "/"


def _normalize_config_path(path: str) -> str:
    return _strip_trailing_slash(path)


class WebSocketConfig(Base):
    """WebSocket server channel configuration.

    Clients connect with URLs like ``ws://{host}:{port}{path}?client_id=...&token=...``.
    - ``client_id``: Used for ``allow_from`` authorization; if omitted, a value is generated and logged.
    - ``token``: If non-empty, the ``token`` query param may match this static secret; short-lived tokens
      from ``token_issue_path`` are also accepted.
    - ``token_issue_path``: If non-empty, **GET** (HTTP/1.1) to this path returns JSON
      ``{"token": "...", "expires_in": <seconds>}``; use ``?token=...`` when opening the WebSocket.
      Must differ from ``path`` (the WS upgrade path). If the client runs in the **same process** as
      pythinker and shares the asyncio loop, use a thread or async HTTP client for GET—do not call
      blocking ``urllib`` or synchronous ``httpx`` from inside a coroutine.
    - ``token_issue_secret``: If non-empty, token requests must send ``Authorization: Bearer <secret>`` or
      ``X-Pythinker-Auth: <secret>``.
    - ``websocket_requires_token``: If True, the handshake must include a valid token (static or issued and not expired).
    - Each connection has its own session: a unique ``chat_id`` maps to the agent session internally.
    - ``media`` field in outbound messages contains local filesystem paths; remote clients need a
      shared filesystem or an HTTP file server to access these files.
    """

    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8765
    path: str = "/"
    token: str = ""
    token_issue_path: str = ""
    token_issue_secret: str = ""
    token_ttl_s: int = Field(default=300, ge=30, le=86_400)
    websocket_requires_token: bool = True
    allow_from: list[str] = Field(default_factory=lambda: ["*"])
    streaming: bool = True
    # Refuse to start on non-loopback hosts without TLS unless this flag is
    # explicitly true. Plaintext tokens over a network interface are a clear
    # vulnerability; flip this only for trusted LAN dev with eyes open.
    allow_insecure_remote: bool = False
    # When non-empty, the WebSocket handshake's ``Origin`` header must match
    # one of these exact values.  Use to defend against browser-based
    # cross-site WebSocket abuse when the channel is exposed via a reverse
    # proxy / public URL.  Empty list = no origin check.
    allowed_origins: list[str] = Field(default_factory=list)
    # Default 36 MB, upper 40 MB: supports up to 4 images at ~6 MB each after
    # client-side Worker normalization (see webui Composer). 4 × 6 MB × 1.37
    # (base64 overhead) + envelope framing stays under 36 MB; the 40 MB ceiling
    # leaves a small margin for sender slop without opening a DoS avenue.
    max_message_bytes: int = Field(default=37_748_736, ge=1024, le=41_943_040)
    ping_interval_s: float = Field(default=20.0, ge=5.0, le=300.0)
    ping_timeout_s: float = Field(default=20.0, ge=5.0, le=300.0)
    ssl_certfile: str = ""
    ssl_keyfile: str = ""

    @field_validator("path")
    @classmethod
    def path_must_start_with_slash(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError('path must start with "/"')
        return _normalize_config_path(value)

    @field_validator("token_issue_path")
    @classmethod
    def token_issue_path_format(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        if not value.startswith("/"):
            raise ValueError('token_issue_path must start with "/"')
        return _normalize_config_path(value)

    @model_validator(mode="after")
    def token_issue_path_differs_from_ws_path(self) -> Self:
        if not self.token_issue_path:
            return self
        if _normalize_config_path(self.token_issue_path) == _normalize_config_path(self.path):
            raise ValueError("token_issue_path must differ from path (the WebSocket upgrade path)")
        return self


def _http_json_response(data: dict[str, Any], *, status: int = 200) -> Response:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    headers = Headers(
        [
            ("Date", email.utils.formatdate(usegmt=True)),
            ("Connection", "close"),
            ("Content-Length", str(len(body))),
            ("Content-Type", "application/json; charset=utf-8"),
        ]
    )
    reason = http.HTTPStatus(status).phrase
    return Response(status, reason, headers, body)


def _read_webui_model_name() -> str | None:
    """Return the configured default model for readonly webui display."""
    try:
        from pythinker.config.loader import load_config

        model = load_config().agents.defaults.model.strip()
        return model or None
    except Exception as e:
        logger.debug("webui bootstrap could not load model name: {}", e)
        return None


def _parse_request_path(path_with_query: str) -> tuple[str, dict[str, list[str]]]:
    """Parse normalized path and query parameters in one pass."""
    parsed = urlparse("ws://x" + path_with_query)
    path = _strip_trailing_slash(parsed.path or "/")
    return path, parse_qs(parsed.query)


def _normalize_http_path(path_with_query: str) -> str:
    """Return the path component (no query string), with trailing slash normalized (root stays ``/``)."""
    return _parse_request_path(path_with_query)[0]


def _parse_query(path_with_query: str) -> dict[str, list[str]]:
    return _parse_request_path(path_with_query)[1]


def _query_first(query: dict[str, list[str]], key: str) -> str | None:
    """Return the first value for *key*, or None."""
    values = query.get(key)
    return values[0] if values else None


def _safe_int(
    raw: str | None, *, default: int, lo: int | None = None, hi: int | None = None
) -> int:
    """Parse a query-string integer with bounds; fall back to *default* on bad input."""
    if raw is None:
        return default
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return default
    if lo is not None and v < lo:
        return lo
    if hi is not None and v > hi:
        return hi
    return v


def _parse_inbound_payload(raw: str) -> str | None:
    """Parse a client frame into text; return None for empty or unrecognized content."""
    text = raw.strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(data, dict):
            for key in ("content", "text", "message"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            return None
        return None
    return text


# Accept UUIDs and short scoped keys like "unified:default". Keeps the capability
# namespace small enough to rule out path traversal / quote injection tricks.
_CHAT_ID_RE = re.compile(r"^[A-Za-z0-9_:-]{1,64}$")


def _is_valid_chat_id(value: Any) -> bool:
    return isinstance(value, str) and _CHAT_ID_RE.match(value) is not None


def _parse_envelope(raw: str) -> dict[str, Any] | None:
    """Return a typed envelope dict if the frame is a new-style JSON envelope, else None.

    A frame qualifies when it parses as a JSON object with a string ``type`` field.
    Legacy frames (plain text, or ``{"content": ...}`` without ``type``) return None;
    callers should fall back to :func:`_parse_inbound_payload` for those.
    """
    text = raw.strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    t = data.get("type")
    if not isinstance(t, str):
        return None
    return data


# Per-message image limits. The server-side guard is a touch looser than the
# client's ``Worker`` normalization target (6 MB) — tolerate client slop, but
# still cap total ingress at ``_MAX_IMAGES_PER_MESSAGE * _MAX_IMAGE_BYTES``
# which fits comfortably inside ``max_message_bytes``.
_MAX_IMAGES_PER_MESSAGE = 4
_MAX_IMAGE_BYTES = 8 * 1024 * 1024

# Image MIME whitelist — matches the Composer's ``accept`` list. SVG is
# explicitly excluded to avoid the XSS surface inside embedded scripts.
_IMAGE_MIME_ALLOWED: frozenset[str] = frozenset({
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
})

_DATA_URL_MIME_RE = re.compile(r"^data:([^;]+);base64,", re.DOTALL)


def _extract_data_url_mime(url: str) -> str | None:
    """Return the MIME type of a ``data:<mime>;base64,...`` URL, else ``None``."""
    if not isinstance(url, str):
        return None
    m = _DATA_URL_MIME_RE.match(url)
    if not m:
        return None
    return m.group(1).strip().lower() or None


_LOCALHOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _is_local_bind(host: str) -> bool:
    """Return True when *host* refers exclusively to the loopback interface."""
    return host.strip().lower() in _LOCALHOSTS

# Matches the legacy chat-id pattern but allows file-system-safe stems too,
# so the API can address sessions whose keys came from non-WebSocket channels.
_API_KEY_RE = re.compile(r"^[A-Za-z0-9_:.-]{1,128}$")


def _decode_api_key(raw_key: str) -> str | None:
    """Decode a percent-encoded API path segment, then validate the result."""
    key = unquote(raw_key)
    if _API_KEY_RE.match(key) is None:
        return None
    return key


def _is_localhost(connection: Any) -> bool:
    """Return True if *connection* originated from the loopback interface."""
    addr = getattr(connection, "remote_address", None)
    if not addr:
        return False
    host = addr[0] if isinstance(addr, tuple) else addr
    if not isinstance(host, str):
        return False
    # ``::ffff:127.0.0.1`` is loopback in IPv6-mapped form.
    if host.startswith("::ffff:"):
        host = host[7:]
    return host in _LOCALHOSTS


def _http_response(
    body: bytes,
    *,
    status: int = 200,
    content_type: str = "text/plain; charset=utf-8",
    extra_headers: list[tuple[str, str]] | None = None,
) -> Response:
    headers = [
        ("Date", email.utils.formatdate(usegmt=True)),
        ("Connection", "close"),
        ("Content-Length", str(len(body))),
        ("Content-Type", content_type),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    reason = http.HTTPStatus(status).phrase
    return Response(status, reason, Headers(headers), body)


def _http_error(status: int, message: str | None = None) -> Response:
    body = (message or http.HTTPStatus(status).phrase).encode("utf-8")
    return _http_response(body, status=status)


def _bearer_token(headers: Any) -> str | None:
    """Pull a Bearer token out of standard or query-style headers."""
    auth = headers.get("Authorization") or headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


def _is_websocket_upgrade(request: WsRequest) -> bool:
    """Detect an actual WS upgrade; plain HTTP GETs to the same path should fall through."""
    upgrade = request.headers.get("Upgrade") or request.headers.get("upgrade")
    connection = request.headers.get("Connection") or request.headers.get("connection")
    if not upgrade or "websocket" not in upgrade.lower():
        return False
    if not connection or "upgrade" not in connection.lower():
        return False
    return True


def _b64url_encode(data: bytes) -> str:
    """URL-safe base64 without padding — compact + friendly in URL paths."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    """Reverse of :func:`_b64url_encode`; caller handles ``ValueError``."""
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


# Allowed MIME types we actually serve from the media endpoint. Anything
# outside this set is degraded to ``application/octet-stream`` so an
# attacker who somehow gets a signed URL for an unexpected file type can't
# trick the browser into sniffing executable content.
_MEDIA_ALLOWED_MIMES: frozenset[str] = frozenset({
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
})


def _issue_route_secret_matches(headers: Any, configured_secret: str) -> bool:
    """Return True if the token-issue HTTP request carries credentials matching ``token_issue_secret``."""
    if not configured_secret:
        return True
    authorization = headers.get("Authorization") or headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
        return hmac.compare_digest(supplied, configured_secret)
    header_token = headers.get("X-Pythinker-Auth") or headers.get("x-pythinker-auth")
    if not header_token:
        return False
    return hmac.compare_digest(header_token.strip(), configured_secret)


class WebSocketChannel(BaseChannel):
    """Run a local WebSocket server; forward text/JSON messages to the message bus."""

    name = "websocket"
    display_name = "WebSocket"

    def __init__(
        self,
        config: Any,
        bus: MessageBus,
        *,
        session_manager: "SessionManager | None" = None,
        static_dist_path: Path | None = None,
        agent_defaults: AgentDefaults | None = None,
        admin_service: "AdminService | None" = None,
    ):
        if isinstance(config, dict):
            config = WebSocketConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: WebSocketConfig = config
        # chat_id -> connections subscribed to it (fan-out target).
        self._subs: dict[str, set[Any]] = {}
        # connection -> chat_ids it is subscribed to (O(1) cleanup on disconnect).
        self._conn_chats: dict[Any, set[str]] = {}
        # connection -> default chat_id for legacy frames that omit routing.
        self._conn_default: dict[Any, str] = {}
        # Connections whose WS handshake carried a token that also authorizes
        # the embedded WebUI admin REST surface or the configured static token.
        self._admin_connections: set[Any] = set()
        # Single-use tokens consumed at WebSocket handshake.
        self._issued_tokens: dict[str, float] = {}
        # Multi-use tokens for the embedded webui's REST surface; checked but not consumed.
        self._api_tokens: dict[str, float] = {}
        self._stop_event: asyncio.Event | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._session_manager = session_manager
        self._static_dist_path: Path | None = (
            static_dist_path.resolve() if static_dist_path is not None else None
        )
        # Token-window snapshot needed by the WebUI usage pill; the route is a
        # no-op (503) when this isn't wired by ``ChannelManager``.
        self._agent_defaults = agent_defaults
        self._admin_service = admin_service
        self._admin_bind_attempts: dict[Any, list[float]] = {}
        # Process-local secret used to HMAC-sign media URLs. The signed URL is
        # the capability — anyone who holds a valid URL can fetch that one
        # file, nothing else. The secret regenerates on restart so links
        # become self-expiring (callers just refresh the session list).
        self._media_secret: bytes = secrets.token_bytes(32)

    # -- Subscription bookkeeping -------------------------------------------

    def _attach(self, connection: Any, chat_id: str) -> None:
        """Idempotently subscribe *connection* to *chat_id*."""
        self._subs.setdefault(chat_id, set()).add(connection)
        self._conn_chats.setdefault(connection, set()).add(chat_id)

    def _cleanup_connection(self, connection: Any) -> None:
        """Remove *connection* from every subscription set; safe to call multiple times."""
        chat_ids = self._conn_chats.pop(connection, set())
        for cid in chat_ids:
            subs = self._subs.get(cid)
            if subs is None:
                continue
            subs.discard(connection)
            if not subs:
                self._subs.pop(cid, None)
        self._conn_default.pop(connection, None)
        self._admin_connections.discard(connection)

    async def _send_event(self, connection: Any, event: str, **fields: Any) -> None:
        """Send a control event (attached, error, ...) to a single connection."""
        payload: dict[str, Any] = {"event": event}
        payload.update(fields)
        raw = json.dumps(payload, ensure_ascii=False)
        try:
            await connection.send(raw)
        except ConnectionClosed:
            self._cleanup_connection(connection)
        except Exception as e:
            logger.warning("websocket: failed to send {} event: {}", event, e)

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return WebSocketConfig().model_dump(by_alias=True)

    def _expected_path(self) -> str:
        return _normalize_config_path(self.config.path)

    def _build_ssl_context(self) -> ssl.SSLContext | None:
        cert = self.config.ssl_certfile.strip()
        key = self.config.ssl_keyfile.strip()
        if not cert and not key:
            return None
        if not cert or not key:
            raise ValueError(
                "websocket: ssl_certfile and ssl_keyfile must both be set for WSS, or both left empty"
            )
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile=cert, keyfile=key)
        return ctx

    _MAX_ISSUED_TOKENS = 10_000

    def _purge_expired_issued_tokens(self) -> None:
        now = time.monotonic()
        for token_key, expiry in list(self._issued_tokens.items()):
            if now > expiry:
                self._issued_tokens.pop(token_key, None)

    def _take_issued_token_if_valid(self, token_value: str | None) -> bool:
        """Validate and consume one issued token (single use per connection attempt).

        Uses single-step pop to minimize the window between lookup and removal;
        safe under asyncio's single-threaded cooperative model.
        """
        if not token_value:
            return False
        self._purge_expired_issued_tokens()
        expiry = self._issued_tokens.pop(token_value, None)
        if expiry is None:
            return False
        if time.monotonic() > expiry:
            return False
        return True

    def _handle_token_issue_http(self, connection: Any, request: Any) -> Any:
        secret = self.config.token_issue_secret.strip()
        if secret:
            if not _issue_route_secret_matches(request.headers, secret):
                return connection.respond(401, "Unauthorized")
        else:
            logger.warning(
                "websocket: token_issue_path is set but token_issue_secret is empty; "
                "any client can obtain connection tokens — set token_issue_secret for production."
            )
        self._purge_expired_issued_tokens()
        if len(self._issued_tokens) >= self._MAX_ISSUED_TOKENS:
            logger.error(
                "websocket: too many outstanding issued tokens ({}), rejecting issuance",
                len(self._issued_tokens),
            )
            return _http_json_response({"error": "too many outstanding tokens"}, status=429)
        token_value = f"nbwt_{secrets.token_urlsafe(32)}"
        self._issued_tokens[token_value] = time.monotonic() + float(self.config.token_ttl_s)

        return _http_json_response(
            {"token": token_value, "expires_in": self.config.token_ttl_s}
        )

    # -- HTTP dispatch ------------------------------------------------------

    async def _dispatch_http(self, connection: Any, request: WsRequest) -> Any:
        """Route an inbound HTTP request to a handler or to the WS upgrade path."""
        got, query = _parse_request_path(request.path)

        # 1. Token issue endpoint (legacy, optional, gated by configured secret).
        if self.config.token_issue_path:
            issue_expected = _normalize_config_path(self.config.token_issue_path)
            if got == issue_expected:
                return self._handle_token_issue_http(connection, request)

        # 2. WebUI bootstrap: mints tokens for the embedded UI.  Localhost-only
        #    by default; remote callers must present token_issue_secret.
        if got == "/webui/bootstrap":
            return self._handle_webui_bootstrap(connection, request)

        # 3. REST surface for the embedded UI.
        if got == "/api/sessions":
            return self._handle_sessions_list(request)

        m = re.match(r"^/api/sessions/([^/]+)/messages$", got)
        if m:
            return self._handle_session_messages(request, m.group(1))

        # NOTE: websockets' HTTP parser only accepts GET, so we cannot expose a
        # true ``DELETE`` verb. The action is folded into the path instead.
        m = re.match(r"^/api/sessions/([^/]+)/delete$", got)
        if m:
            return self._handle_session_delete(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/usage$", got)
        if m:
            return self._handle_session_usage(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/pin$", got)
        if m:
            return self._handle_session_pin(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/archive$", got)
        if m:
            return self._handle_session_archive(request, m.group(1))

        if got == "/api/search":
            return self._handle_search(request)

        if got == "/api/commands":
            return self._handle_commands_list(request)

        if got == "/api/models":
            return self._handle_models_list(request)

        if got == "/api/admin/overview":
            return self._handle_admin_overview(request)

        if got == "/api/admin/sessions":
            return self._handle_admin_sessions(request)

        if got == "/api/admin/models":
            return self._handle_admin_models(request)

        if got == "/api/admin/usage":
            return self._handle_admin_usage(request)

        if got == "/api/admin/surfaces":
            return self._handle_admin_surfaces(request)

        if got == "/api/admin/config":
            return self._handle_admin_config(request)

        if got == "/api/admin/config/schema":
            return self._handle_admin_config_schema(request)

        if got == "/api/admin/config/backups":
            return self._handle_admin_config_backups(request)

        m = re.match(r"^/api/admin/sessions/([^/]+)/stop$", got)
        if m:
            return await self._handle_admin_session_stop(request, m.group(1))

        m = re.match(r"^/api/admin/sessions/([^/]+)/restart$", got)
        if m:
            return await self._handle_admin_session_restart(request, m.group(1))

        m = re.match(r"^/api/admin/subagents/([^/]+)/cancel$", got)
        if m:
            return await self._handle_admin_subagent_cancel(request, m.group(1))

        # Signed media fetch: ``<sig>`` is an HMAC over ``<payload>``; the
        # payload decodes to a path inside :func:`get_media_dir`. See
        # :meth:`_sign_media_path` for the inverse direction used to build
        # these URLs when replaying a session.
        m = re.match(r"^/api/media/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)$", got)
        if m:
            return self._handle_media_fetch(m.group(1), m.group(2))

        # 4. WebSocket upgrade (the channel's primary purpose). Only run the
        # handshake gate on requests that actually ask to upgrade; otherwise
        # a bare ``GET /`` from the browser would be rejected as an
        # unauthorized WS handshake instead of serving the SPA's index.html.
        expected_ws = self._expected_path()
        if got == expected_ws and _is_websocket_upgrade(request):
            client_id = _query_first(query, "client_id") or ""
            if len(client_id) > 128:
                client_id = client_id[:128]
            if not self.is_allowed(client_id):
                return connection.respond(403, "Forbidden")
            allowed_origins = [o.strip() for o in self.config.allowed_origins if o.strip()]
            if allowed_origins:
                origin = ""
                headers = getattr(request, "headers", None)
                if headers is not None:
                    try:
                        origin = (headers.get("Origin") or "").strip()
                    except Exception:
                        origin = ""
                if origin not in allowed_origins:
                    logger.warning(
                        "websocket: rejected handshake from origin {!r} (not in allowed_origins)",
                        origin,
                    )
                    return connection.respond(403, "Forbidden")
            return self._authorize_websocket_handshake(connection, query)

        # 5. Static SPA serving (only if a build directory was wired in).
        #    Without a working auth path for non-loopback clients the SPA shell
        #    is just a useless preview that fails to bootstrap; hide it
        #    entirely so we don't leak the existence of the service.
        if self._static_dist_path is not None:
            if (
                not _is_localhost(connection)
                and not self.config.token_issue_secret.strip()
            ):
                return connection.respond(404, "Not Found")
            response = self._serve_static(got)
            if response is not None:
                return response

        return connection.respond(404, "Not Found")

    # -- HTTP route handlers ------------------------------------------------

    def _check_api_token(self, request: WsRequest) -> bool:
        """Validate a request against the API token pool (multi-use, TTL-bound)."""
        self._purge_expired_api_tokens()
        token = _bearer_token(request.headers) or _query_first(
            _parse_query(request.path), "token"
        )
        if not token:
            return False
        expiry = self._api_tokens.get(token)
        if expiry is None or time.monotonic() > expiry:
            self._api_tokens.pop(token, None)
            return False
        return True

    def _purge_expired_api_tokens(self) -> None:
        now = time.monotonic()
        for token_key, expiry in list(self._api_tokens.items()):
            if now > expiry:
                self._api_tokens.pop(token_key, None)

    def _token_allows_admin(self, token: str | None) -> bool:
        if not token:
            return False
        static_token = self.config.token.strip()
        if static_token and hmac.compare_digest(token, static_token):
            return True
        self._purge_expired_api_tokens()
        expiry = self._api_tokens.get(token)
        return expiry is not None and time.monotonic() <= expiry

    def _handle_webui_bootstrap(self, connection: Any, request: Any | None = None) -> Response:
        if not _is_localhost(connection):
            # Remote bootstrap is gated on the same shared secret used by the
            # legacy token-issue endpoint, so deployments with a real auth
            # surface keep working over the network without lowering the
            # localhost-only default.
            secret = self.config.token_issue_secret.strip()
            if not secret:
                return _http_error(403, "webui bootstrap is localhost-only")
            headers = getattr(request, "headers", None) if request is not None else None
            if headers is None or not _issue_route_secret_matches(headers, secret):
                return _http_error(401, "Unauthorized")
        # Cap outstanding tokens to avoid runaway growth from a misbehaving client.
        self._purge_expired_issued_tokens()
        self._purge_expired_api_tokens()
        if (
            len(self._issued_tokens) >= self._MAX_ISSUED_TOKENS
            or len(self._api_tokens) >= self._MAX_ISSUED_TOKENS
        ):
            return _http_response(
                json.dumps({"error": "too many outstanding tokens"}).encode("utf-8"),
                status=429,
                content_type="application/json; charset=utf-8",
            )
        token = f"nbwt_{secrets.token_urlsafe(32)}"
        expiry = time.monotonic() + float(self.config.token_ttl_s)
        # Same string registered in both pools: the WS handshake consumes one copy
        # while the REST surface keeps validating the other until TTL expiry.
        self._issued_tokens[token] = expiry
        self._api_tokens[token] = expiry
        return _http_json_response(
            {
                "token": token,
                "ws_path": self._expected_path(),
                "expires_in": self.config.token_ttl_s,
                "model_name": _read_webui_model_name(),
                # Voice transcription rides the WS ``transcribe`` envelope
                # (the ``websockets`` HTTP parser hard-rejects POST, so a
                # REST route is impossible). The flag toggles based on
                # whether ``ChannelManager`` wired a provider + API key
                # onto this channel; the frontend renders the mic button
                # disabled with an explanatory tooltip when False.
                "voice_enabled": bool(self.transcription_provider)
                and bool(self.transcription_api_key),
            }
        )

    def _handle_sessions_list(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self._session_manager is None:
            return _http_error(503, "session manager unavailable")
        sessions = self._session_manager.list_sessions()
        # The webui is only meaningful for websocket-channel chats — CLI /
        # Slack / Lark / Discord sessions can't be resumed from the browser,
        # so leaking them into the sidebar is just noise. Filter to the
        # ``websocket:`` prefix and strip absolute paths on the way out.
        cleaned = [
            {k: v for k, v in s.items() if k != "path"}
            for s in sessions
            if isinstance(s.get("key"), str) and s["key"].startswith("websocket:")
        ]
        return _http_json_response({"sessions": cleaned})

    @staticmethod
    def _is_webui_session_key(key: str) -> bool:
        """Return True when *key* belongs to the webui's websocket-only surface."""
        return key.startswith("websocket:")

    def _handle_session_messages(self, request: WsRequest, key: str) -> Response:
        if not self._check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self._session_manager is None:
            return _http_error(503, "session manager unavailable")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        # The embedded webui only understands websocket-channel sessions. Keep
        # its read surface aligned with ``/api/sessions`` instead of letting a
        # caller probe arbitrary CLI / Slack / Lark history by handcrafted URL.
        if not self._is_webui_session_key(decoded_key):
            return _http_error(404, "session not found")
        data = self._session_manager.read_session_file(decoded_key)
        if data is None:
            return _http_error(404, "session not found")
        # Decorate persisted user messages with signed media URLs so the
        # client can render previews. The raw on-disk ``media`` paths are
        # stripped on the way out — they leak server filesystem layout and
        # the client never needs them once it has the signed fetch URL.
        self._augment_media_urls(data)
        return _http_json_response(data)

    def _augment_media_urls(self, payload: dict[str, Any]) -> None:
        """Mutate *payload* in place: each message's ``media`` path list is
        replaced by a parallel ``media_urls`` list of signed fetch URLs.

        Messages without media or with non-string path entries are left
        untouched. Paths that no longer live inside ``media_dir`` (e.g. the
        file was deleted, or the dir was relocated) are silently skipped;
        the client falls back to the historical-replay placeholder tile.
        """
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            media = msg.get("media")
            if not isinstance(media, list) or not media:
                continue
            urls: list[dict[str, str]] = []
            for entry in media:
                if not isinstance(entry, str) or not entry:
                    continue
                signed = self._sign_media_path(Path(entry))
                if signed is None:
                    continue
                urls.append({"url": signed, "name": Path(entry).name})
            if urls:
                msg["media_urls"] = urls
            # Always drop the raw paths from the wire payload.
            msg.pop("media", None)

    def _sign_media_path(self, abs_path: Path) -> str | None:
        """Return a ``/api/media/<sig>/<payload>`` URL for *abs_path*, or
        ``None`` when the path does not resolve inside the media root.

        The URL is self-authenticating: the signature binds the payload to
        this process's ``_media_secret``, so only paths we chose to sign can
        be fetched. The returned path is relative to the server origin; the
        client joins it against the existing webui base.
        """
        try:
            media_root = get_media_dir().resolve()
            rel = abs_path.resolve().relative_to(media_root)
        except (OSError, ValueError):
            return None
        payload = _b64url_encode(rel.as_posix().encode("utf-8"))
        mac = hmac.new(
            self._media_secret, payload.encode("ascii"), hashlib.sha256
        ).digest()[:16]
        return f"/api/media/{_b64url_encode(mac)}/{payload}"

    def _handle_media_fetch(self, sig: str, payload: str) -> Response:
        """Serve a single media file previously signed via
        :meth:`_sign_media_path`. Validates the signature, decodes the
        payload to a relative path, and streams the file bytes with a
        long-lived immutable cache header (the URL already encodes the
        file identity, so caches can be aggressive)."""
        try:
            provided_mac = _b64url_decode(sig)
        except (ValueError, binascii.Error):
            return _http_error(401, "invalid signature")
        expected_mac = hmac.new(
            self._media_secret, payload.encode("ascii"), hashlib.sha256
        ).digest()[:16]
        if not hmac.compare_digest(expected_mac, provided_mac):
            return _http_error(401, "invalid signature")
        try:
            rel_bytes = _b64url_decode(payload)
            rel_str = rel_bytes.decode("utf-8")
        except (ValueError, binascii.Error, UnicodeDecodeError):
            return _http_error(400, "invalid payload")
        # An attacker who somehow bypassed the HMAC check would still need
        # the resolved path to escape the media root; guard defensively.
        try:
            media_root = get_media_dir().resolve()
            candidate = (media_root / rel_str).resolve()
            candidate.relative_to(media_root)
        except (OSError, ValueError):
            return _http_error(404, "not found")
        if not candidate.is_file():
            return _http_error(404, "not found")
        try:
            body = candidate.read_bytes()
        except OSError:
            return _http_error(500, "read error")
        mime, _ = mimetypes.guess_type(candidate.name)
        if mime not in _MEDIA_ALLOWED_MIMES:
            mime = "application/octet-stream"
        return _http_response(
            body,
            content_type=mime,
            extra_headers=[
                ("Cache-Control", "private, max-age=31536000, immutable"),
                # Paired with the MIME whitelist above: prevents browsers from
                # MIME-sniffing an octet-stream fallback into executable HTML.
                ("X-Content-Type-Options", "nosniff"),
            ],
        )

    def _handle_session_delete(self, request: WsRequest, key: str) -> Response:
        if not self._check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self._session_manager is None:
            return _http_error(503, "session manager unavailable")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        # Same boundary as ``_handle_session_messages``: the webui may only
        # mutate websocket sessions, and deletion really does unlink the local
        # JSONL, so keep the blast radius narrow and explicit.
        if not self._is_webui_session_key(decoded_key):
            return _http_error(404, "session not found")
        deleted = self._session_manager.delete_session(decoded_key)
        return _http_json_response({"deleted": bool(deleted)})

    def _handle_session_pin(self, request: WsRequest, key: str) -> Response:
        """Toggle the ``pinned`` flag on a webui session's meta sidecar."""
        return self._handle_session_meta_toggle(request, key, field="pinned")

    def _handle_session_archive(self, request: WsRequest, key: str) -> Response:
        """Toggle the ``archived`` flag on a webui session's meta sidecar."""
        return self._handle_session_meta_toggle(request, key, field="archived")

    def _handle_session_meta_toggle(
        self, request: WsRequest, key: str, *, field: str,
    ) -> Response:
        """Flip *field* on the meta sidecar and return the new bool value.

        Mirrors :meth:`_handle_session_delete` for auth, key decoding, and
        webui-namespace gating. The session must already exist on disk;
        otherwise we'd create an orphan meta sidecar pointing at no JSONL.
        """
        if not self._check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self._session_manager is None:
            return _http_error(503, "session manager unavailable")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not self._is_webui_session_key(decoded_key):
            return _http_error(404, "session not found")
        if self._session_manager.read_session_file(decoded_key) is None:
            return _http_error(404, "session not found")
        meta = self._session_manager.read_meta(decoded_key)
        new_value = not bool(meta.get(field))
        merged = self._session_manager.write_meta(decoded_key, **{field: new_value})
        return _http_json_response({field: bool(merged.get(field))})

    def _handle_session_usage(self, request: WsRequest, key: str) -> Response:
        """Return ``{"used": int, "limit": int}`` for the WebUI usage pill.

        Mirrors :meth:`_handle_session_messages` for auth, session-namespace
        gating, and key decoding; delegates the count to
        :func:`pythinker.agent.usage.estimate_session_usage`.
        """
        if not self._check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self._session_manager is None or self._agent_defaults is None:
            return _http_error(503, "service unavailable")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not self._is_webui_session_key(decoded_key):
            return _http_error(404, "session not found")
        # Read-only path: ``read_session_file`` returns ``None`` for a missing
        # session, mirroring ``_handle_session_messages``. Using ``get_or_create``
        # here would silently resurrect deleted sessions and let any
        # authenticated caller mint empty session files by hammering arbitrary
        # ``websocket:<id>`` keys.
        data = self._session_manager.read_session_file(decoded_key)
        if data is None:
            return _http_error(404, "session not found")
        from pythinker.agent.usage import estimate_session_usage

        # ``estimate_session_usage`` only touches ``.messages`` on the input;
        # build a tiny shim around the raw dict so we don't have to reconstruct
        # a full ``Session`` from disk.
        class _SessionView:
            messages = data.get("messages", []) if isinstance(data, dict) else []

        usage = estimate_session_usage(_SessionView(), self._agent_defaults)  # type: ignore[arg-type]
        return _http_json_response(usage)

    def _handle_commands_list(self, request: WsRequest) -> Response:
        """Return the built-in slash-command rows for the WebUI palette.

        Read-only, derived from :data:`pythinker.command.metadata.BUILTIN_COMMAND_METADATA`
        so ``/help`` and the palette stay in lockstep. User-installed plugin
        commands are out of scope for Phase 3 (no plugin metadata surface
        exists yet).
        """
        if not self._check_api_token(request):
            return _http_error(401, "Unauthorized")
        from pythinker.command.metadata import BUILTIN_COMMAND_METADATA

        rows = [
            {"name": m.name, "summary": m.summary, "usage": m.usage}
            for m in BUILTIN_COMMAND_METADATA
        ]
        return _http_json_response({"commands": rows})

    def _handle_models_list(self, request: WsRequest) -> Response:
        """Return the WebUI model-switcher rows.

        Phase 3 same-provider scope: the dropdown shows the configured default
        model plus any entries listed under ``agents.defaults.alternate_models``.
        Cross-provider switching needs a ``ProviderPool`` and is deferred to a
        later phase, so the same-provider matcher in
        :meth:`Config._match_provider` (schema.py:278-294) is implicitly
        respected by trusting the user's curated alternate list.
        """
        if not self._check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self._agent_defaults is None:
            return _http_error(503, "agent defaults unavailable")
        default = self._agent_defaults.model
        rows: list[dict[str, Any]] = [{"name": default, "is_default": True}]
        seen = {default}
        for alt in self._agent_defaults.alternate_models:
            if alt and alt not in seen:
                rows.append({"name": alt, "is_default": False})
                seen.add(alt)
        return _http_json_response({"models": rows})

    def _handle_admin_payload(self, request: WsRequest, producer: Any) -> Response:
        if not self._check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self._admin_service is None:
            return _http_error(503, "admin service unavailable")
        try:
            return _http_json_response(producer())
        except Exception:
            logger.exception("websocket admin route failed")
            return _http_error(500, "admin route failed")

    def _handle_admin_overview(self, request: WsRequest) -> Response:
        return self._handle_admin_payload(request, lambda: self._admin_service.overview())

    def _handle_admin_sessions(self, request: WsRequest) -> Response:
        return self._handle_admin_payload(request, lambda: self._admin_service.sessions())

    def _handle_admin_models(self, request: WsRequest) -> Response:
        return self._handle_admin_payload(request, lambda: self._admin_service.models())

    def _handle_admin_usage(self, request: WsRequest) -> Response:
        return self._handle_admin_payload(request, lambda: self._admin_service.usage())

    def _handle_admin_surfaces(self, request: WsRequest) -> Response:
        return self._handle_admin_payload(request, lambda: self._admin_service.surfaces())

    def _handle_admin_config(self, request: WsRequest) -> Response:
        return self._handle_admin_payload(request, lambda: self._admin_service.config_payload())

    def _handle_admin_config_schema(self, request: WsRequest) -> Response:
        return self._handle_admin_payload(request, lambda: self._admin_service.config_schema())

    def _handle_admin_config_backups(self, request: WsRequest) -> Response:
        return self._handle_admin_payload(
            request,
            lambda: {"backups": self._admin_service.config_backups()},
        )

    # -- Admin mutations (token + custom-header gate) ----------------------
    #
    # The websockets HTTP parser is GET-only, so mutating verbs are folded
    # into the path (mirroring ``/api/sessions/<key>/pin|archive|delete``).
    # CSRF defense is the required ``X-Pythinker-Admin-Action: 1`` header —
    # cross-site browser tabs can fire GETs from <img>/<form> but cannot set
    # arbitrary request headers without a CORS preflight that the server
    # never answers, so the header alone defeats drive-by writes for the
    # localhost-only personal deployment posture this project targets.

    _ADMIN_ACTION_HEADER = "X-Pythinker-Admin-Action"

    def _check_admin_mutation(
        self, request: WsRequest, *, route: str, key: str
    ) -> Response | None:
        if not self._check_api_token(request):
            return _http_error(401, "Unauthorized")
        headers = getattr(request, "headers", None) or {}
        try:
            header_value = (headers.get(self._ADMIN_ACTION_HEADER) or "").strip()
        except AttributeError:
            header_value = ""
        if header_value != "1":
            return _http_error(403, "Missing admin action header")
        logger.info("admin_mutation route={} key={}", route, key)
        return None

    async def _handle_admin_session_stop(self, request: WsRequest, key: str) -> Response:
        rejection = self._check_admin_mutation(request, route="session_stop", key=key)
        if rejection is not None:
            return rejection
        if self._admin_service is None or self._admin_service.agent_loop is None:
            return _http_error(503, "agent loop unavailable")
        try:
            cancelled = await self._admin_service.agent_loop._cancel_active_tasks(key)
        except Exception:
            logger.exception("admin session_stop failed for key=%s", key)
            return _http_error(500, "stop failed")
        return _http_json_response({"cancelled": int(cancelled)})

    async def _handle_admin_session_restart(self, request: WsRequest, key: str) -> Response:
        rejection = self._check_admin_mutation(request, route="session_restart", key=key)
        if rejection is not None:
            return rejection
        if self._admin_service is None or self._admin_service.agent_loop is None:
            return _http_error(503, "agent loop unavailable")
        loop = self._admin_service.agent_loop
        sm = self._admin_service.session_manager
        try:
            cancelled = await loop._cancel_active_tasks(key)
        except Exception:
            logger.exception("admin session_restart cancel failed for key=%s", key)
            return _http_error(500, "restart failed")
        # Use load_existing — get_or_create would silently materialise a
        # blank session for a mistyped key and persist it on the next save.
        session = sm.load_existing(key) if sm is not None else None
        cleared = False
        if session is not None:
            try:
                loop._clear_runtime_checkpoint(session)
                loop._clear_pending_user_turn(session)
                sm.save(session)
                cleared = True
            except Exception:
                logger.exception("admin session_restart save failed for key=%s", key)
        return _http_json_response(
            {
                "cancelled": int(cancelled),
                "checkpoint_cleared": cleared,
                "found": session is not None,
            }
        )

    async def _handle_admin_subagent_cancel(self, request: WsRequest, task_id: str) -> Response:
        rejection = self._check_admin_mutation(request, route="subagent_cancel", key=task_id)
        if rejection is not None:
            return rejection
        if self._admin_service is None or self._admin_service.agent_loop is None:
            return _http_error(503, "agent loop unavailable")
        sub_mgr = getattr(self._admin_service.agent_loop, "subagents", None)
        if sub_mgr is None:
            return _http_error(503, "subagent manager unavailable")
        try:
            cancelled = await sub_mgr.cancel_task(task_id)
        except Exception:
            logger.exception("admin subagent_cancel failed for task_id=%s", task_id)
            return _http_error(500, "cancel failed")
        return _http_json_response({"cancelled": bool(cancelled)})

    def _handle_search(self, request: WsRequest) -> Response:
        """Cross-chat substring search; paginated ``{results, offset, limit, has_more}``."""
        if not self._check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self._session_manager is None:
            return _http_error(503, "session manager unavailable")
        query_params = _parse_query(request.path)
        q = (_query_first(query_params, "q") or "").strip()
        offset = _safe_int(_query_first(query_params, "offset"), default=0, lo=0)
        limit = _safe_int(
            _query_first(query_params, "limit"), default=50, lo=1, hi=200
        )

        if not q:
            return _http_json_response(
                {"results": [], "offset": offset, "limit": limit, "has_more": False}
            )

        from pythinker.agent.search import search_sessions

        # Pull matching hits + one extra so we can decide ``has_more`` without
        # a second pass.
        raw_hits = search_sessions(
            self._session_manager.iter_message_files_for_search(),
            query=q,
            limit=limit + 1,
            offset=offset,
        )
        has_more = len(raw_hits) > limit
        hits = raw_hits[:limit]
        # Decorate each hit with the chat title and archived flag so the
        # sidebar result row can render without a second roundtrip.
        decorated: list[dict[str, Any]] = []
        for hit in hits:
            meta = self._session_manager.read_meta(hit["session_key"])
            decorated.append({
                **hit,
                "title": meta.get("title", "") or "",
                "archived": bool(meta.get("archived")),
            })
        return _http_json_response(
            {
                "results": decorated,
                "offset": offset,
                "limit": limit,
                "has_more": has_more,
            }
        )

    def _serve_static(self, request_path: str) -> Response | None:
        """Resolve *request_path* against the built SPA directory; SPA fallback to index.html."""
        assert self._static_dist_path is not None
        rel = request_path.lstrip("/")
        if not rel:
            rel = "index.html"
        # Reject path-traversal attempts and absolute targets.
        if ".." in rel.split("/") or rel.startswith("/"):
            return _http_error(403, "Forbidden")
        candidate = (self._static_dist_path / rel).resolve()
        try:
            candidate.relative_to(self._static_dist_path)
        except ValueError:
            return _http_error(403, "Forbidden")
        if not candidate.is_file():
            # SPA history-mode fallback: unknown routes serve index.html so the
            # client-side router can render them.
            index = self._static_dist_path / "index.html"
            if index.is_file():
                candidate = index
            else:
                return None
        try:
            body = candidate.read_bytes()
        except OSError as e:
            logger.warning("websocket static: failed to read {}: {}", candidate, e)
            return _http_error(500, "Internal Server Error")
        ctype, _ = mimetypes.guess_type(candidate.name)
        if ctype is None:
            ctype = "application/octet-stream"
        if ctype.startswith("text/") or ctype in {"application/javascript", "application/json"}:
            ctype = f"{ctype}; charset=utf-8"
        # Hash-named build assets are cache-friendly; index.html must stay fresh.
        if candidate.name == "index.html":
            cache = "no-cache"
        else:
            cache = "public, max-age=31536000, immutable"
        return _http_response(
            body,
            status=200,
            content_type=ctype,
            extra_headers=[("Cache-Control", cache)],
        )

    def _authorize_websocket_handshake(self, connection: Any, query: dict[str, list[str]]) -> Any:
        supplied = _query_first(query, "token")
        static_token = self.config.token.strip()

        if static_token:
            if supplied and hmac.compare_digest(supplied, static_token):
                return None
            if supplied and self._take_issued_token_if_valid(supplied):
                return None
            return connection.respond(401, "Unauthorized")

        if self.config.websocket_requires_token:
            if supplied and self._take_issued_token_if_valid(supplied):
                return None
            return connection.respond(401, "Unauthorized")

        if supplied:
            self._take_issued_token_if_valid(supplied)
        return None

    async def start(self) -> None:
        self._running = True
        self._stop_event = asyncio.Event()

        ssl_context = self._build_ssl_context()
        scheme = "wss" if ssl_context else "ws"

        # Refuse to start on a network-reachable interface without TLS unless
        # the operator has explicitly opted in. Plaintext tokens over a LAN/VPN
        # are a credential-leak vulnerability waiting to happen.
        if (
            not _is_local_bind(self.config.host)
            and ssl_context is None
            and not self.config.allow_insecure_remote
        ):
            raise RuntimeError(
                "WebSocket channel refuses to bind on a non-loopback host "
                f"({self.config.host!r}) without TLS. Either set "
                "channels.websocket.ssl_certfile + ssl_keyfile to enable wss://, "
                "or explicitly set channels.websocket.allow_insecure_remote: true "
                "(LAN-only dev; tokens travel in plaintext)."
            )
        if not _is_local_bind(self.config.host) and ssl_context is None:
            logger.warning(
                "websocket: bound to {} without TLS — tokens travel in plaintext. "
                "Enable ssl_certfile/ssl_keyfile for any non-loopback exposure.",
                self.config.host,
            )

        # Refuse to expose the legacy token-issue endpoint on a non-loopback
        # host without a shared secret.  An open token-issue route on a public
        # interface lets anyone mint connection tokens, defeating the auth
        # surface entirely.
        if (
            self.config.token_issue_path
            and not _is_local_bind(self.config.host)
            and not self.config.token_issue_secret.strip()
        ):
            raise RuntimeError(
                "WebSocket channel refuses to expose token_issue_path on a "
                f"non-loopback host ({self.config.host!r}) without "
                "token_issue_secret set. Generate a strong secret with "
                "`pythinker token` and set channels.websocket.token_issue_secret."
            )

        async def process_request(
            connection: ServerConnection,
            request: WsRequest,
        ) -> Any:
            return await self._dispatch_http(connection, request)

        async def handler(connection: ServerConnection) -> None:
            await self._connection_loop(connection)

        logger.info(
            "WebSocket server listening on {}://{}:{}{}",
            scheme,
            self.config.host,
            self.config.port,
            self.config.path,
        )
        if self.config.token_issue_path:
            logger.info(
                "WebSocket token issue route: {}://{}:{}{}",
                scheme,
                self.config.host,
                self.config.port,
                _normalize_config_path(self.config.token_issue_path),
            )

        async def runner() -> None:
            async with serve(
                handler,
                self.config.host,
                self.config.port,
                process_request=process_request,
                max_size=self.config.max_message_bytes,
                ping_interval=self.config.ping_interval_s,
                ping_timeout=self.config.ping_timeout_s,
                ssl=ssl_context,
            ):
                assert self._stop_event is not None
                await self._stop_event.wait()

        self._server_task = asyncio.create_task(runner())
        await self._server_task

    async def _connection_loop(self, connection: Any) -> None:
        request = connection.request
        path_part = request.path if request else "/"
        _, query = _parse_request_path(path_part)
        supplied_token = _query_first(query, "token")
        if self._token_allows_admin(supplied_token):
            self._admin_connections.add(connection)
        client_id_raw = _query_first(query, "client_id")
        client_id = client_id_raw.strip() if client_id_raw else ""
        if not client_id:
            client_id = f"anon-{uuid.uuid4().hex[:12]}"
        elif len(client_id) > 128:
            logger.warning("websocket: client_id too long ({} chars), truncating", len(client_id))
            client_id = client_id[:128]

        default_chat_id = str(uuid.uuid4())

        try:
            await connection.send(
                json.dumps(
                    {
                        "event": "ready",
                        "chat_id": default_chat_id,
                        "client_id": client_id,
                    },
                    ensure_ascii=False,
                )
            )
            # Register only after ready is successfully sent to avoid out-of-order sends
            self._conn_default[connection] = default_chat_id
            self._attach(connection, default_chat_id)

            async for raw in connection:
                if isinstance(raw, bytes):
                    try:
                        raw = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        logger.warning("websocket: ignoring non-utf8 binary frame")
                        continue

                envelope = _parse_envelope(raw)
                if envelope is not None:
                    await self._dispatch_envelope(connection, client_id, envelope)
                    continue

                content = _parse_inbound_payload(raw)
                if content is None:
                    continue
                await self._handle_message(
                    sender_id=client_id,
                    chat_id=default_chat_id,
                    content=content,
                    metadata={"remote": getattr(connection, "remote_address", None)},
                )
        except Exception as e:
            logger.debug("websocket connection ended: {}", e)
        finally:
            self._cleanup_connection(connection)

    @staticmethod
    def _save_envelope_media(
        media: list[Any],
    ) -> tuple[list[str], str | None]:
        """Decode and persist ``media`` items from a ``message`` envelope.

        Returns ``(paths, None)`` on success or ``([], reason)`` on the first
        failure — the caller is expected to surface ``reason`` to the client
        and skip publishing so no half-formed message ever reaches the agent.
        On failure, any images already written to disk earlier in the same
        call are unlinked so partial ingress doesn't leak orphan files.
        ``reason`` is a short, stable token suitable for UI localization.

        Shape: ``list[{"data_url": str, "name"?: str | None}]``.
        """
        if len(media) > _MAX_IMAGES_PER_MESSAGE:
            return [], "too_many_images"
        media_dir = get_media_dir("websocket")
        paths: list[str] = []

        def _abort(reason: str) -> tuple[list[str], str]:
            for p in paths:
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning(
                        "websocket: failed to unlink partial media {}: {}", p, exc
                    )
            return [], reason

        for item in media:
            if not isinstance(item, dict):
                return _abort("malformed")
            data_url = item.get("data_url")
            if not isinstance(data_url, str) or not data_url:
                return _abort("malformed")
            mime = _extract_data_url_mime(data_url)
            if mime is None:
                return _abort("decode")
            if mime not in _IMAGE_MIME_ALLOWED:
                return _abort("mime")
            try:
                saved = save_base64_data_url(
                    data_url, media_dir, max_bytes=_MAX_IMAGE_BYTES,
                )
            except FileSizeExceeded:
                return _abort("size")
            except Exception as exc:
                logger.warning("websocket: media decode failed: {}", exc)
                return _abort("decode")
            if saved is None:
                return _abort("decode")
            paths.append(saved)
        return paths, None

    async def _dispatch_envelope(
        self,
        connection: Any,
        client_id: str,
        envelope: dict[str, Any],
    ) -> None:
        """Route one typed inbound envelope (``new_chat`` / ``attach`` / ``message``)."""
        t = envelope.get("type")
        if t == "new_chat":
            new_id = str(uuid.uuid4())
            self._attach(connection, new_id)
            await self._send_event(connection, "attached", chat_id=new_id)
            return
        if t == "attach":
            cid = envelope.get("chat_id")
            if not _is_valid_chat_id(cid):
                await self._send_event(connection, "error", detail="invalid chat_id")
                return
            self._attach(connection, cid)
            await self._send_event(connection, "attached", chat_id=cid)
            return
        if t == "message":
            cid = envelope.get("chat_id")
            content = envelope.get("content")
            if not _is_valid_chat_id(cid):
                await self._send_event(connection, "error", detail="invalid chat_id")
                return
            if not isinstance(content, str):
                await self._send_event(connection, "error", detail="missing content")
                return

            raw_media = envelope.get("media")
            media_paths: list[str] = []
            if raw_media is not None:
                if not isinstance(raw_media, list):
                    await self._send_event(
                        connection, "error",
                        detail="image_rejected", reason="malformed",
                    )
                    return
                media_paths, reason = self._save_envelope_media(raw_media)
                if reason is not None:
                    await self._send_event(
                        connection, "error",
                        detail="image_rejected", reason=reason,
                    )
                    return

            # Allow image-only turns (content may be empty when media is attached).
            if not content.strip() and not media_paths:
                await self._send_event(connection, "error", detail="missing content")
                return

            # Auto-attach on first use so clients can one-shot without a separate attach.
            self._attach(connection, cid)
            await self._handle_message(
                sender_id=client_id,
                chat_id=cid,
                content=content,
                media=media_paths or None,
                metadata={"remote": getattr(connection, "remote_address", None)},
            )
            return
        if t == "stop":
            cid = envelope.get("chat_id")
            if not _is_valid_chat_id(cid):
                await self._send_event(connection, "error", detail="invalid chat_id")
                return
            self._attach(connection, cid)
            # Route through the same path as a normal user message; the agent
            # loop's priority router catches "/stop" before turn dispatch.
            await self._handle_message(
                sender_id=client_id,
                chat_id=cid,
                content="/stop",
                media=None,
                metadata={
                    "remote": getattr(connection, "remote_address", None),
                },
            )
            return
        if t == "regenerate":
            cid = envelope.get("chat_id")
            if not _is_valid_chat_id(cid):
                await self._send_event(connection, "error", detail="invalid chat_id")
                return
            self._attach(connection, cid)
            # Delegate the truncation + republish to the agent loop's
            # priority command handler so it runs under the per-session lock.
            # The channel must NOT mutate session state directly — it would
            # race with an in-flight turn.
            await self._handle_message(
                sender_id=client_id,
                chat_id=cid,
                content="/regenerate",
                media=None,
                metadata={"remote": getattr(connection, "remote_address", None)},
            )
            return
        if t == "edit":
            cid = envelope.get("chat_id")
            user_msg_index = envelope.get("user_msg_index")
            new_content = envelope.get("content")
            if not _is_valid_chat_id(cid):
                await self._send_event(connection, "error", detail="invalid chat_id")
                return
            if not isinstance(user_msg_index, int) or not isinstance(new_content, str):
                await self._send_event(
                    connection, "error", detail="malformed edit envelope"
                )
                return
            if not new_content.strip():
                await self._send_event(
                    connection, "error", detail="empty edit content"
                )
                return
            self._attach(connection, cid)
            # Delegate the in-place rewrite + truncation + republish to the
            # agent loop's priority command handler so it runs under the
            # per-session lock. Edit metadata travels in the InboundMessage's
            # ``metadata`` so the priority handler can read it.
            await self._handle_message(
                sender_id=client_id,
                chat_id=cid,
                content="/edit",
                media=None,
                metadata={
                    "remote": getattr(connection, "remote_address", None),
                    "edit_user_msg_index": user_msg_index,
                    "edit_content": new_content,
                },
            )
            return
        if t == "set_model":
            cid = envelope.get("chat_id")
            model = envelope.get("model")
            if not _is_valid_chat_id(cid):
                await self._send_event(connection, "error", detail="invalid chat_id")
                return
            if not isinstance(model, str):
                await self._send_event(
                    connection, "error", detail="malformed set_model envelope"
                )
                return
            if self._session_manager is None:
                await self._send_event(
                    connection, "error", detail="session manager unavailable"
                )
                return
            session_key = f"websocket:{cid}"
            session = self._session_manager.get_or_create(session_key)
            normalized = model.strip()
            if normalized:
                session.metadata["model_override"] = normalized
            else:
                session.metadata.pop("model_override", None)
            self._session_manager.save(session)
            self._attach(connection, cid)
            await self._send_event(
                connection, "model_set", chat_id=cid, model=normalized,
            )
            return
        if t in {
            "admin_config_set",
            "admin_config_unset",
            "admin_config_replace_secret",
            "admin_config_restore_backup",
        }:
            await self._handle_admin_config_envelope(connection, envelope)
            return
        if t in {"admin_test_bind", "admin_test_channel", "admin_mcp_probe", "admin_browser_probe"}:
            await self._handle_admin_probe_envelope(connection, envelope)
            return
        if t == "transcribe":
            await self._handle_transcribe_envelope(connection, envelope)
            return
        await self._send_event(connection, "error", detail=f"unknown type: {t!r}")

    async def _handle_admin_config_envelope(
        self,
        connection: Any,
        envelope: dict[str, Any],
    ) -> None:
        request_id = envelope.get("request_id")
        path = envelope.get("path")
        if connection not in self._admin_connections:
            await self._send_event(
                connection,
                "admin_config_error",
                request_id=request_id,
                detail="admin token required",
            )
            return
        if self._admin_service is None:
            await self._send_event(
                connection,
                "admin_config_error",
                request_id=request_id,
                detail="admin service unavailable",
            )
            return
        t = envelope.get("type")
        if t == "admin_config_restore_backup":
            backup_id = envelope.get("backup_id")
            if not isinstance(backup_id, str) or not backup_id.strip():
                await self._send_event(
                    connection,
                    "admin_config_error",
                    request_id=request_id,
                    detail="missing backup id",
                )
                return
            try:
                self._admin_service.restore_config_backup(backup_id)
            except Exception as exc:
                await self._send_event(
                    connection,
                    "admin_config_error",
                    request_id=request_id,
                    detail=str(exc),
                )
                return
            await self._send_event(
                connection,
                "admin_config_saved",
                request_id=request_id,
                path="config.backup",
                restart_required=True,
            )
            return
        if not isinstance(path, str) or not path.strip():
            await self._send_event(
                connection,
                "admin_config_error",
                request_id=request_id,
                detail="missing config path",
            )
            return
        try:
            if t == "admin_config_set":
                if "value" not in envelope:
                    raise ValueError("missing config value")
                self._admin_service.set_config(path, envelope.get("value"))
            elif t == "admin_config_unset":
                self._admin_service.unset_config(path)
            elif t == "admin_config_replace_secret":
                self._admin_service.replace_secret(path, envelope.get("value"))
            else:
                raise ValueError(f"unknown admin config operation {t!r}")
        except Exception as exc:
            await self._send_event(
                connection,
                "admin_config_error",
                request_id=request_id,
                path=path,
                detail=str(exc),
            )
            return
        await self._send_event(
            connection,
            "admin_config_saved",
            request_id=request_id,
            path=path,
            restart_required=True,
        )

    async def _handle_admin_probe_envelope(
        self,
        connection: Any,
        envelope: dict[str, Any],
    ) -> None:
        request_id = envelope.get("request_id")
        if connection not in self._admin_connections:
            await self._send_event(
                connection,
                "admin_config_error",
                request_id=request_id,
                detail="admin token required",
            )
            return
        if self._admin_service is None:
            await self._send_event(
                connection,
                "admin_config_error",
                request_id=request_id,
                detail="admin service unavailable",
            )
            return
        t = envelope.get("type")
        try:
            if t == "admin_test_bind":
                result = await self._admin_test_bind(connection, envelope)
                event = "admin_test_bind_result"
            elif t == "admin_test_channel":
                name = envelope.get("name")
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("missing channel name")
                result = await self._admin_service.test_channel(name.strip())
                event = "admin_test_channel_result"
            elif t == "admin_mcp_probe":
                server = envelope.get("server")
                if not isinstance(server, str) or not server.strip():
                    raise ValueError("missing mcp server")
                result = await self._admin_service.mcp_probe(server.strip())
                event = "admin_mcp_probe_result"
            elif t == "admin_browser_probe":
                result = await self._admin_service.browser_probe()
                event = "admin_browser_probe_result"
            else:
                raise ValueError(f"unknown admin probe operation {t!r}")
        except Exception as exc:
            await self._send_event(
                connection,
                "admin_config_error",
                request_id=request_id,
                detail=str(exc),
            )
            return
        await self._send_event(connection, event, request_id=request_id, result=result)

    async def _admin_test_bind(
        self,
        connection: Any,
        envelope: dict[str, Any],
    ) -> dict[str, object]:
        service = self._admin_service
        if service is None:
            raise ValueError("admin service unavailable")
        now = time.monotonic()
        window = [stamp for stamp in self._admin_bind_attempts.get(connection, []) if now - stamp < 60]
        if len(window) >= 5:
            self._admin_bind_attempts[connection] = window
            return {
                "ok": False,
                "errno": "ERATELIMIT",
                "message": "Bind test rate limit exceeded",
            }
        host = envelope.get("host")
        port = envelope.get("port")
        if not isinstance(host, str) or not host.strip():
            raise ValueError("missing bind host")
        if not isinstance(port, int):
            raise ValueError("missing bind port")
        window.append(now)
        self._admin_bind_attempts[connection] = window
        return await service.test_bind(host.strip(), port)

    # Cap on decoded audio bytes accepted by the ``transcribe`` envelope.
    # 10 MiB at typical Opus/AAC bitrates buys roughly 10 minutes of speech,
    # which is more than enough for a chat dictation session and well under
    # the WS frame limit (``max_message_bytes`` defaults to 36 MB before
    # base64 overhead is accounted for).
    _MAX_TRANSCRIBE_BYTES = 10 * 1024 * 1024
    _TRANSCRIBE_FORMATS = {"webm", "mp4", "wav"}

    async def _handle_transcribe_envelope(
        self,
        connection: Any,
        envelope: dict[str, Any],
    ) -> None:
        """Decode a base64 audio blob, run it through the channel's transcription
        provider, and emit ``transcription_result`` (or ``error``) back to the
        originating connection.

        ``request_id`` is echoed on every emitted event so the frontend can
        correlate the response with the in-flight recording.
        """
        request_id = envelope.get("request_id")
        # Provider must be wired by ChannelManager before voice rides this path.
        if not self.transcription_provider or not self.transcription_api_key:
            await self._send_event(
                connection,
                "error",
                detail="voice transcription not configured",
                request_id=request_id,
            )
            return

        audio_b64 = envelope.get("audio_base64")
        if not isinstance(audio_b64, str) or not audio_b64:
            await self._send_event(
                connection,
                "error",
                detail="missing audio_base64",
                request_id=request_id,
            )
            return

        fmt = envelope.get("format")
        if not isinstance(fmt, str) or fmt not in self._TRANSCRIBE_FORMATS:
            await self._send_event(
                connection,
                "error",
                detail="unsupported format",
                request_id=request_id,
            )
            return

        try:
            audio_bytes = base64.b64decode(audio_b64, validate=True)
        except (binascii.Error, ValueError):
            await self._send_event(
                connection,
                "error",
                detail="malformed audio_base64",
                request_id=request_id,
            )
            return

        if len(audio_bytes) > self._MAX_TRANSCRIBE_BYTES:
            await self._send_event(
                connection,
                "error",
                detail="audio too large",
                request_id=request_id,
            )
            return

        # NamedTemporaryFile(delete=False) so the provider can re-open the path.
        # Manual unlink in finally guarantees cleanup even on provider failure.
        tmp = tempfile.NamedTemporaryFile(
            suffix=f".{fmt}", delete=False
        )
        tmp_path = Path(tmp.name)
        try:
            try:
                tmp.write(audio_bytes)
            finally:
                tmp.close()
            text = await self.transcribe_audio(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

        if not text:
            # transcribe_audio swallows provider exceptions and returns ""; the
            # frontend can't usefully render empty text, so surface a typed
            # error event instead.
            await self._send_event(
                connection,
                "error",
                detail="transcription_failed",
                request_id=request_id,
            )
            return

        await self._send_event(
            connection,
            "transcription_result",
            text=text,
            request_id=request_id,
        )

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._stop_event:
            self._stop_event.set()
        if self._server_task:
            try:
                await self._server_task
            except Exception as e:
                logger.warning("websocket: server task error during shutdown: {}", e)
            self._server_task = None
        self._subs.clear()
        self._conn_chats.clear()
        self._conn_default.clear()
        self._issued_tokens.clear()
        self._api_tokens.clear()

    async def _safe_send_to(self, connection: Any, raw: str, *, label: str = "") -> None:
        """Send a raw frame to one connection, cleaning up on ConnectionClosed."""
        try:
            await connection.send(raw)
        except ConnectionClosed:
            self._cleanup_connection(connection)
            logger.warning("websocket{}connection gone", label)
        except Exception as e:
            logger.error("websocket{}send failed: {}", label, e)
            raise

    async def send(self, msg: OutboundMessage) -> None:
        # Snapshot the subscriber set so ConnectionClosed cleanups mid-iteration are safe.
        conns = list(self._subs.get(msg.chat_id, ()))
        if not conns:
            logger.warning("websocket: no active subscribers for chat_id={}", msg.chat_id)
            return
        payload: dict[str, Any] = {
            "event": "message",
            "chat_id": msg.chat_id,
            "text": msg.content,
        }
        if msg.media:
            payload["media"] = msg.media
        if msg.reply_to:
            payload["reply_to"] = msg.reply_to
        # Mark intermediate agent breadcrumbs (tool-call hints, generic
        # progress strings) so WS clients can render them as subordinate
        # trace rows rather than conversational replies.
        if msg.metadata.get("_tool_hint"):
            payload["kind"] = "tool_hint"
        elif msg.metadata.get("_progress"):
            payload["kind"] = "progress"
        raw = json.dumps(payload, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" ")

    async def send_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        meta = metadata or {}
        if meta.get("_stream_end"):
            body: dict[str, Any] = {"event": "stream_end", "chat_id": chat_id}
        else:
            body = {
                "event": "delta",
                "chat_id": chat_id,
                "text": delta,
            }
        if meta.get("_stream_id") is not None:
            body["stream_id"] = meta["_stream_id"]
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" stream ")
