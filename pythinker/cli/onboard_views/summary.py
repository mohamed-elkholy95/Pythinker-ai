"""Summary panels for the onboarding wizard."""

from __future__ import annotations

from typing import Any

from pythinker.cli.onboard_views import clack
from pythinker.config.schema import Config

_SECRET_HINTS = (
    "key",
    "token",
    "secret",
    "password",
    "passphrase",
    "authorization",
    "bearer",
    "credential",
    "auth",
)


def _is_secret_path(path: str) -> bool:
    """True when **any** segment of ``path`` looks like it carries a secret.

    We scan every dotted segment, not just the last, because user-controlled
    dicts (``providers.*.extra_headers``, ``providers.*.extra_body``, MCP
    ``headers`` / ``env``) inject arbitrary keys as the final segment — e.g.
    ``providers.anthropic.extra_headers.Authorization``. Checking only the
    last segment would let header names like ``Authorization`` or env var
    names like ``CREDENTIAL`` slip through with their plaintext bearer token
    in the pre-save diff.
    """
    segments = path.lower().split(".")
    return any(hint in seg for seg in segments for hint in _SECRET_HINTS)


def _format_value(value: Any, *, masked: bool) -> str:
    """Stringify a leaf value for the diff renderer. Secrets become ``***``
    so a copy-pasted screenshot of the wizard never leaks credentials."""
    if masked and value not in (None, "", []):
        return "***"
    if value is None:
        return "(none)"
    if value == "":
        return "(empty)"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        return f"[{len(value)} items]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        return f"{{{len(value)} keys}}"
    s = str(value)
    if len(s) > 60:
        s = s[:57] + "…"
    return s


def _walk(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten a pydantic ``model_dump()`` (or any nested dict) into
    ``{dotted.path: leaf_value}``. List/tuple values stop recursion — they
    render as a count, not item-by-item, to keep diffs readable."""
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for key, val in obj.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(val, dict):
                out.update(_walk(val, child))
            else:
                out[child] = val
    else:
        out[prefix] = obj
    return out


def render_pre_save_diff(old: Config | None, new: Config) -> None:
    """Render a colored diff panel between the on-disk config and the
    about-to-be-saved one.

    The "Changes since last save" panel. When ``old`` is None this is a
    fresh install and we just announce that without iterating fields.

    Output shape (no ANSI; clack's note panel renders monochrome):

        + agents.defaults.model: openai-codex/gpt-5.5-mini
        ~ gateway.port: 18790  →  18792
        - channels.telegram.token: ***  →  (removed)

    Secrets (``api_key``, ``token``, ``secret``, ``password``) are masked
    with ``***`` regardless of the actual value, so the rendered panel is
    safe to screenshot.
    """
    if old is None:
        clack.note(
            "Changes since last save",
            ["(fresh install — no prior config to diff against)"],
        )
        clack.bar_break()
        return

    old_flat = _walk(old.model_dump(by_alias=False, exclude_none=False))
    new_flat = _walk(new.model_dump(by_alias=False, exclude_none=False))
    keys = sorted(set(old_flat) | set(new_flat))

    lines: list[str] = []
    for key in keys:
        in_old = key in old_flat
        in_new = key in new_flat
        old_val = old_flat.get(key)
        new_val = new_flat.get(key)
        if in_old and in_new:
            if old_val == new_val:
                continue
            masked = _is_secret_path(key)
            lines.append(
                f"~ {key}: {_format_value(old_val, masked=masked)}  →  "
                f"{_format_value(new_val, masked=masked)}"
            )
        elif in_new:
            masked = _is_secret_path(key)
            lines.append(f"+ {key}: {_format_value(new_val, masked=masked)}")
        else:
            masked = _is_secret_path(key)
            lines.append(f"- {key}: {_format_value(old_val, masked=masked)}")

    if not lines:
        lines = ["(no changes)"]

    clack.note("Changes since last save", lines)
    clack.bar_break()


def render_existing_summary(cfg: Config) -> None:
    """Print the `◇ Existing config ─╮ … ╯` panel for step 4."""
    from pythinker.providers.registry import PROVIDERS
    default_provider = None
    for spec in PROVIDERS:
        provider_name = spec.name
        provider_cfg = getattr(cfg.providers, provider_name, None)
        if provider_cfg and getattr(provider_cfg, "api_key", ""):
            default_provider = provider_name
            break
    if not default_provider:
        default_provider = "(none)"

    body = [
        "Path:        ~/.pythinker/config.json",
        f"Provider:    {default_provider}",
        f"Model:       {cfg.agents.defaults.model or '(none)'}",
    ]
    clack.note("Existing config", body)
    clack.bar_break()


def render_pre_save(cfg: Config) -> None:
    """Step 14 — Ready to save panel (full diff vs. defaults)."""
    from pythinker.providers.registry import PROVIDERS
    body = [
        "~/.pythinker/config.json",
        "",
    ]
    # Resolve current provider/model/workspace defensively.
    provider = "(none)"
    for spec in PROVIDERS:
        spec_name = spec.name
        pc = getattr(cfg.providers, spec_name, None)
        if pc is not None and getattr(pc, "api_key", None):
            provider = spec_name.replace("_", "-")
            break

    model = cfg.agents.defaults.model or "(none)"
    workspace = cfg.agents.defaults.workspace or "(default)"
    body.append(f"Provider:    {provider}")
    body.append(f"Model:       {model}")
    body.append(f"Workspace:   {workspace}")

    enabled_channels = []
    for name in ("telegram", "discord", "slack", "matrix", "whatsapp", "websocket"):
        cc = getattr(cfg.channels, name, None)
        enabled = (
            bool(cc.get("enabled", False))
            if isinstance(cc, dict)
            else bool(getattr(cc, "enabled", False))
        )
        if cc is not None and enabled:
            enabled_channels.append(name)
    body.append(f"Channels:    {', '.join(enabled_channels) or '(none)'}")
    clack.note("Ready to save", body)
    clack.bar_break()
