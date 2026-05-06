"""``pythinker agents`` subcommand — manage the multi-agent layout.

Phase 2 PR-2 of `.agents/plans/2026-05-05-onboard-phase-2-multi-agent.md`.
This module owns:

  * ``agents list`` — show every per-agent dir under ``~/.pythinker/agents/``,
    plus the legacy single-config row when no per-agent dir exists.
  * ``agents create <id> [--from <other>]`` — scaffold a new agent dir
    with ``config.json`` + ``workspace/``. Refuses to overwrite an
    existing agent.
  * ``agents switch <id>`` — write the ``current-agent`` marker file.
    Refuses ids that don't have a config.
  * ``agents delete <id> --confirm <id>`` — remove the agent dir. Refuses
    to delete the currently-active agent or ``default``.

OAuth tokens stay shared at ``~/.local/share/`` per the plan; this module
never touches them.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from pythinker.config.paths import (
    agent_config_path,
    agent_dir,
    current_agent_id,
)

app = typer.Typer(
    help="Manage local Pythinker agents (~/.pythinker/agents/<id>/).",
    no_args_is_help=True,
)

console = Console()

_RESERVED_NAMES = {"default"}


def _agents_root() -> Path:
    return Path.home() / ".pythinker" / "agents"


def _marker_path() -> Path:
    return Path.home() / ".pythinker" / "current-agent"


def _read_agent_model(config_path: Path) -> str:
    """Best-effort lookup of ``agents.defaults.model`` from a config file.

    Returns the model id when present and parseable; the empty string when
    the file is missing, malformed, or doesn't have the field set. Used
    only for the ``list`` table — never gates behavior on this lookup.
    """
    if not config_path.is_file():
        return ""
    try:
        import json

        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return (
        data.get("agents", {}).get("defaults", {}).get("model")
        or data.get("agents", {}).get("defaults", {}).get("modelName")
        or ""
    )


@app.command("list")
def agents_list() -> None:
    """List every agent under ``~/.pythinker/agents/`` plus the legacy default."""
    active = current_agent_id()
    table = Table(title="Pythinker agents")
    table.add_column("Active", style="green")
    table.add_column("Id", style="cyan")
    table.add_column("Config")
    table.add_column("Default model")

    rows: list[tuple[str, str, str, str]] = []
    root = _agents_root()
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            cfg = child / "config.json"
            model = _read_agent_model(cfg)
            mark = "✓" if child.name == active else ""
            rows.append((mark, child.name, str(cfg), model or "(unset)"))

    legacy = Path.home() / ".pythinker" / "config.json"
    if not rows and legacy.is_file():
        # No per-agent dirs yet — surface the single-config user as the
        # "default" agent so ``agents list`` is never empty when a config
        # exists.
        model = _read_agent_model(legacy)
        rows.append(("✓", "default (legacy)", str(legacy), model or "(unset)"))

    if not rows:
        console.print("[yellow]No agents found. Run `pythinker onboard` to create one.[/yellow]")
        return

    for r in rows:
        table.add_row(*r)
    console.print(table)


@app.command("create")
def agents_create(
    agent_id: str = typer.Argument(..., help="New agent id (filesystem-safe)."),
    from_id: str | None = typer.Option(
        None, "--from", help="Copy config + workspace from another agent id."
    ),
) -> None:
    """Scaffold ``~/.pythinker/agents/<id>/{config.json, workspace/}``."""
    _validate_id(agent_id)

    target = agent_dir(agent_id)
    if target.exists():
        console.print(f"[red]Agent {agent_id!r} already exists at {target}[/red]")
        raise typer.Exit(1)

    target.mkdir(parents=True)
    workspace = target / "workspace"
    workspace.mkdir()

    cfg_path = target / "config.json"

    if from_id:
        source_cfg = agent_config_path(from_id)
        if not source_cfg.is_file():
            console.print(
                f"[red]Source config not found for agent {from_id!r} ({source_cfg})[/red]"
            )
            shutil.rmtree(target, ignore_errors=True)
            raise typer.Exit(1)
        shutil.copy2(source_cfg, cfg_path)
        # Best-effort copy of memory artifacts; not fatal if absent.
        source_ws = agent_dir(from_id) / "workspace"
        if source_ws.is_dir():
            for marker in ("MEMORY.md", "SOUL.md", "USER.md"):
                src = source_ws / marker
                if src.is_file():
                    shutil.copy2(src, workspace / marker)
    else:
        cfg_path.write_text("{}\n", encoding="utf-8")

    console.print(
        f"[green]✓[/green] Created agent {agent_id!r} at {target}\n"
        f"  Config: {cfg_path}\n"
        f"  Workspace: {workspace}\n"
        f"  Switch: pythinker agents switch {agent_id}"
    )


@app.command("switch")
def agents_switch(
    agent_id: str = typer.Argument(..., help="Agent id to make active."),
) -> None:
    """Write ``~/.pythinker/current-agent`` so subsequent commands use ``<id>``."""
    _validate_id(agent_id)

    if agent_id == "default":
        # Special-case: writing "default" is functionally a clear-marker. Fine,
        # but warn the user so they don't think a per-agent dir was created.
        marker = _marker_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("default\n", encoding="utf-8")
        console.print(
            f"[green]✓[/green] Active agent set to {agent_id!r} "
            f"(legacy ~/.pythinker/config.json will be used until "
            f"~/.pythinker/agents/default/ exists)."
        )
        return

    cfg = agent_config_path(agent_id)
    target = agent_dir(agent_id)
    if not target.is_dir() or not cfg.is_file() or cfg.parent != target:
        # cfg.parent != target catches the legacy fallback: agent_config_path
        # returns ~/.pythinker/config.json when the agent dir is missing.
        console.print(
            f"[red]Agent {agent_id!r} has no config at {target / 'config.json'}.\n"
            f"Create it with: pythinker agents create {agent_id}[/red]"
        )
        raise typer.Exit(1)

    marker = _marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{agent_id}\n", encoding="utf-8")
    console.print(
        f"[green]✓[/green] Active agent set to {agent_id!r}. "
        f"Pythinker will use {cfg} for this and future invocations."
    )


@app.command("delete")
def agents_delete(
    agent_id: str = typer.Argument(..., help="Agent id to delete."),
    confirm: str | None = typer.Option(
        None,
        "--confirm",
        help="Pass the same agent id again to confirm deletion.",
    ),
) -> None:
    """Remove ``~/.pythinker/agents/<id>/`` (config + workspace)."""
    _validate_id(agent_id)

    if agent_id in _RESERVED_NAMES:
        console.print(
            f"[red]Refusing to delete reserved agent {agent_id!r}. "
            f"This is the legacy fallback and is not stored under "
            f"~/.pythinker/agents/.[/red]"
        )
        raise typer.Exit(1)

    if agent_id == current_agent_id():
        console.print(
            f"[red]Refusing to delete the currently-active agent {agent_id!r}. "
            f"Switch first: pythinker agents switch <other-id>[/red]"
        )
        raise typer.Exit(1)

    target = agent_dir(agent_id)
    if not target.is_dir():
        console.print(f"[red]Agent {agent_id!r} not found at {target}[/red]")
        raise typer.Exit(1)

    if confirm != agent_id:
        console.print(
            f"[yellow]Refusing to delete without --confirm. Re-run as:\n"
            f"  pythinker agents delete {agent_id} --confirm {agent_id}[/yellow]"
        )
        raise typer.Exit(1)

    shutil.rmtree(target)
    console.print(f"[green]✓[/green] Deleted agent {agent_id!r} ({target})")


def _validate_id(agent_id: str) -> None:
    """Refuse ids that contain path separators or empty segments."""
    if not agent_id or "/" in agent_id or "\\" in agent_id or agent_id in {".", ".."}:
        console.print(f"[red]Invalid agent id: {agent_id!r}[/red]")
        raise typer.Exit(1)
