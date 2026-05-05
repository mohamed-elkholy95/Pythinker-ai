"""Pure helpers for Telegram media-type and extension inference.

Extracted from ``pythinker/channels/telegram.py`` so the small bag of
file-extension / media-kind logic doesn't sit in the middle of the channel
class.

The download path itself (``_download_message_media``) and the outbound
media-send loop stay in ``telegram.py`` because the project's tests patch
``pythinker.channels.telegram.get_media_dir`` and
``pythinker.channels.telegram.validate_url_target`` — those lookups must
remain on the original module's globals, per the import/patch
compatibility rule in ``.agents/plans/2026-05-04-simplification-alignment.md``
§6.
"""

from __future__ import annotations

from pathlib import Path


def _get_media_type(path: str) -> str:
    """Guess media type from file extension."""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext in ("jpg", "jpeg", "png", "gif", "webp"):
        return "photo"
    if ext == "ogg":
        return "voice"
    if ext in ("mp3", "m4a", "wav", "aac"):
        return "audio"
    return "document"


def _is_remote_media_url(path: str) -> bool:
    return path.startswith(("http://", "https://"))


def _get_extension(
    media_type: str,
    mime_type: str | None,
    filename: str | None = None,
) -> str:
    """Get file extension based on media type or original filename."""
    if mime_type:
        ext_map = {
            "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
            "audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
        }
        if mime_type in ext_map:
            return ext_map[mime_type]

    type_map = {"image": ".jpg", "voice": ".ogg", "audio": ".mp3", "file": ""}
    if ext := type_map.get(media_type, ""):
        return ext

    if filename:
        return "".join(Path(filename).suffixes)

    return ""
