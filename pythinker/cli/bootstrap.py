"""Provider / runtime construction helpers for the CLI.

Carved out of ``pythinker/cli/commands.py`` per the §E1 simplification plan.
The Typer command callbacks (``serve``, ``gateway``, ``agent``) live in
``commands.py``; this module owns the shared "build a provider" /
"resolve a runtime config" / "load the browser tool config" plumbing they
all call into.

The CLI ``_make_provider`` here adds Rich-formatted error printing plus
``typer.Exit(1)`` on validation failure. The SDK facade in
``pythinker/pythinker.py:_make_provider`` is the parallel wrapper; both
delegate to ``pythinker.providers.factory.make_provider`` and differ only
in error-reporting style. Keep them separate (the SDK must not import
``typer``/``rich``).
"""

from __future__ import annotations

from pathlib import Path

import typer

from pythinker.config.schema import Config


def _make_provider(config: Config):
    """CLI-facing provider builder.

    Delegates to the canonical `providers.factory.make_provider` and
    translates the factory's ValueError into Rich-formatted messages plus
    `typer.Exit(1)`. Validation logic lives in the factory; the CLI only
    chooses how to surface the result.
    """
    from pythinker.cli.commands import console
    from pythinker.providers.factory import make_provider

    try:
        return make_provider(config)
    except ValueError as exc:
        msg = str(exc)
        # Surface the canonical hints alongside the factory's reason so
        # users see both "what" and "where to fix it" without the CLI
        # re-implementing validation.
        if "Azure OpenAI" in msg:
            console.print(f"[red]Error: {msg}[/red]")
            console.print("Set api_key and api_base in ~/.pythinker/config.json under providers.azure_openai")
            console.print("Use the model field to specify the deployment name.")
        elif "No API key configured" in msg:
            console.print(f"[red]Error: {msg}[/red]")
            console.print("Set one in ~/.pythinker/config.json under providers section")
        else:
            console.print(f"[red]Error: {msg}[/red]")
        raise typer.Exit(1) from exc


def _load_runtime_config(config: str | None = None, workspace: str | None = None) -> Config:
    """Load config and optionally override the active workspace."""
    from pythinker.cli.commands import console
    from pythinker.config.loader import load_config, resolve_config_env_vars, set_config_path

    config_path = None
    if config:
        config_path = Path(config).expanduser().resolve()
        if not config_path.exists():
            console.print(f"[red]Error: Config file not found: {config_path}[/red]")
            raise typer.Exit(1)
        set_config_path(config_path)
        console.print(f"[dim]Using config: {config_path}[/dim]")

    try:
        loaded = resolve_config_env_vars(load_config(config_path))
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    _warn_deprecated_config_keys(config_path)
    if workspace:
        loaded.agents.defaults.workspace = workspace
    return loaded


def _load_browser_config():
    """Load the latest browser tool config for turn-boundary hot reload."""
    from pythinker.config.loader import load_config, resolve_config_env_vars

    return resolve_config_env_vars(load_config()).tools.web.browser


def _warn_deprecated_config_keys(config_path: Path | None) -> None:
    """Hint users to remove obsolete keys from their config file."""
    import json

    from pythinker.cli.commands import console
    from pythinker.config.loader import get_config_path

    path = config_path or get_config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    if "memoryWindow" in raw.get("agents", {}).get("defaults", {}):
        console.print("[dim]Hint: `memoryWindow` in your config is no longer used "
            "and can be safely removed.[/dim]")
