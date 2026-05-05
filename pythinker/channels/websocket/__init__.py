"""WebSocket server channel: pythinker acts as a WebSocket server and serves connected clients.

The channel was originally a single 2k-LOC ``websocket.py``; it now lives as
a small package so the topical surfaces (config, auth, multiplex, REST,
media) can be read in isolation. Public imports continue to resolve at
``pythinker.channels.websocket`` for back-compat.

Tests reach past the public class re-exports for a fistful of unit-level
helpers — every name they import or ``monkeypatch`` is re-exported here so
moving the implementation files does not force a parallel test rewrite.
"""

from __future__ import annotations

# Path / config helpers also surfaced through the legacy module path so the
# webui static handler and registry-discovery introspection keep working.
from pythinker.channels.websocket.auth import (
    _LOCALHOSTS,
    _b64url_decode,
    _b64url_encode,
    _bearer_token,
    _is_local_bind,
    _is_localhost,
    _is_websocket_upgrade,
    _issue_route_secret_matches,
)

# Public surface used by ChannelManager + ``pythinker.channels.registry``.
from pythinker.channels.websocket.channel import WebSocketChannel
from pythinker.channels.websocket.config import (
    CONFIG_FIELDS,
    WebSocketConfig,
    _normalize_config_path,
    _strip_trailing_slash,
)
from pythinker.channels.websocket.media import (
    _DATA_URL_MIME_RE,
    _IMAGE_MIME_ALLOWED,
    _MAX_IMAGE_BYTES,
    _MAX_IMAGES_PER_MESSAGE,
    _MEDIA_ALLOWED_MIMES,
    _extract_data_url_mime,
)
from pythinker.channels.websocket.multiplex import (
    _CHAT_ID_RE,
    _is_valid_chat_id,
    _parse_envelope,
    _parse_inbound_payload,
)
from pythinker.channels.websocket.rest import (
    _API_KEY_RE,
    _decode_api_key,
    _http_error,
    _http_json_response,
    _http_response,
    _normalize_http_path,
    _parse_query,
    _parse_request_path,
    _query_first,
    _read_webui_model_name,
    _safe_int,
)

# Exposed at the package root so existing test patches of the form
# ``patch("pythinker.channels.websocket.get_media_dir", ...)`` keep landing
# on the binding the channel actually consults at call time.
from pythinker.config.paths import get_media_dir

# Re-exports below are deliberately exhaustive: every helper a test imports
# or ``monkeypatch``-es from ``pythinker.channels.websocket`` must keep
# resolving here. Listing them in ``__all__`` doubles as F401 suppression.
__all__ = [
    "CONFIG_FIELDS",
    "WebSocketChannel",
    "WebSocketConfig",
    "_API_KEY_RE",
    "_CHAT_ID_RE",
    "_DATA_URL_MIME_RE",
    "_IMAGE_MIME_ALLOWED",
    "_LOCALHOSTS",
    "_MAX_IMAGE_BYTES",
    "_MAX_IMAGES_PER_MESSAGE",
    "_MEDIA_ALLOWED_MIMES",
    "_b64url_decode",
    "_b64url_encode",
    "_bearer_token",
    "_decode_api_key",
    "_extract_data_url_mime",
    "_http_error",
    "_http_json_response",
    "_http_response",
    "_is_local_bind",
    "_is_localhost",
    "_is_valid_chat_id",
    "_is_websocket_upgrade",
    "_issue_route_secret_matches",
    "_normalize_config_path",
    "_normalize_http_path",
    "_parse_envelope",
    "_parse_inbound_payload",
    "_parse_query",
    "_parse_request_path",
    "_query_first",
    "_read_webui_model_name",
    "_safe_int",
    "_strip_trailing_slash",
    "get_media_dir",
]
