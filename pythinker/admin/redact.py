"""Redaction helpers for the admin dashboard.

Pure functions that operate on already-redacted config payloads (the
``redacted_config`` output from :mod:`pythinker.config.editing`) and on
arbitrary URL strings surfaced to the WebUI. Extracted from
``pythinker/admin/service.py`` per
``.agents/plans/2026-05-04-simplification-alignment.md`` §A4 so they can
be unit-tested in isolation.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit


def _path_value(payload: dict[str, Any], path: str) -> Any:
    from pydantic.alias_generators import to_camel

    current: Any = payload
    for segment in path.split("."):
        if not isinstance(current, dict):
            return None
        if segment in current:
            current = current[segment]
            continue
        alias = to_camel(segment)
        if alias in current:
            current = current[alias]
            continue
        return None
    return current


def _redacted_path_is_set(payload: dict[str, Any], path: str) -> bool:
    value = _path_value(payload, path)
    return value not in (None, "", [], {})


def _redact_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
