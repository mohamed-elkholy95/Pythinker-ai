"""CLI commands for pythinker."""

import asyncio
import contextlib
import os
import select
import signal
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any

# Force UTF-8 encoding for Windows console
if sys.platform == "win32":
    if sys.stdout.encoding != "utf-8":
        os.environ["PYTHONIOENCODING"] = "utf-8"
        # Re-open stdout/stderr with UTF-8 encoding
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# Install a safe-default loguru sink BEFORE the first ``from loguru import
# logger`` lands. This is the import that runs both for ``python -m
# pythinker`` (via __main__.py) AND for the installed ``pythinker``
# console-script (which routes through entry-points and skips __main__),
# so the bootstrap has to live here to cover both invocation paths.
# Per-command callbacks call ``configure_logging(level=cli_level, config=cfg)``
# afterwards to honor --verbose / --quiet / config-file overrides.
from pythinker.utils.log import configure_logging_early  # noqa: E402

configure_logging_early()

import typer  # noqa: E402
from loguru import logger  # noqa: E402
from prompt_toolkit import PromptSession, print_formatted_text  # noqa: E402
from prompt_toolkit.application import run_in_terminal  # noqa: E402
from prompt_toolkit.formatted_text import ANSI, HTML  # noqa: E402
from prompt_toolkit.history import FileHistory  # noqa: E402
from prompt_toolkit.patch_stdout import patch_stdout  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.markdown import Markdown  # noqa: E402
from rich.table import Table  # noqa: E402
from rich.text import Text  # noqa: E402

from pythinker import __logo__, __version__  # noqa: E402
from pythinker.cli.star_prompt import maybe_prompt_github_star  # noqa: E402
from pythinker.cli.stream import StreamRenderer, ThinkingSpinner  # noqa: E402
from pythinker.config.paths import get_update_dir, is_default_workspace  # noqa: E402
from pythinker.config.schema import Config  # noqa: E402
from pythinker.utils.helpers import sync_workspace_templates  # noqa: E402
from pythinker.utils.restart import (  # noqa: E402
    consume_restart_notice_from_env,
    format_restart_completed_message,
    set_restart_notice_to_env,
    should_show_cli_restart_notice,
)
from pythinker.utils.update import (  # noqa: E402
    InstallMethod,
    UpdateInfo,
    check_for_update_sync,
    format_banner,
    mark_notified,
    suggested_target_command,
    suggested_upgrade_command,
    target_install_command,
    upgrade_command,
)


class SafeFileHistory(FileHistory):
    """FileHistory subclass that sanitizes surrogate characters on write.

    On Windows, special Unicode input (emoji, mixed-script) can produce
    surrogate characters that crash prompt_toolkit's file write.
    See issue #2846.
    """

    def store_string(self, string: str) -> None:
        safe = string.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")
        super().store_string(safe)

app = typer.Typer(
    name="pythinker",
    context_settings={"help_option_names": ["-h", "--help"]},
    help=f"{__logo__} pythinker - Personal AI Assistant",
    no_args_is_help=False,
)

# Sub-apps for grouped commands (`pythinker auth ...`, `pythinker channels ...`).
# Modeled on the pythinker CLI taxonomy. Kept narrow on purpose — only commands
# that surface state the existing `pythinker status` output doesn't (per-provider
# OAuth token state, per-channel enabled+configured rows). The legacy `status`
# command stays as the at-a-glance summary; these are for "show me everything
# about X" workflows.
auth_app = typer.Typer(help="Provider authentication state.", no_args_is_help=True)
app.add_typer(auth_app, name="auth")

# NOTE: ``channels_app`` is intentionally declared further down (next to its
# pre-existing status / login commands) rather than here. A duplicate
# declaration at this top-of-file slot used to silently shadow that one,
# orphaning the ``no_args_is_help=True`` flag and producing a phantom
# entry in the typer command tree. See code-reviewer report 2026-04-29.

config_app = typer.Typer(help="Get / set / unset config fields.", no_args_is_help=True)
app.add_typer(config_app, name="config")

restart_app = typer.Typer(help="Restart a running pythinker service.", no_args_is_help=True)
app.add_typer(restart_app, name="restart")

backup_app = typer.Typer(help="Snapshot / verify / restore config.json.", no_args_is_help=True)
app.add_typer(backup_app, name="backup")

cleanup_app = typer.Typer(help="Plan / run destructive cleanups.", no_args_is_help=True)
app.add_typer(cleanup_app, name="cleanup")

# Release-readiness checks (PEP 440 version, __init__ fallback equality,
# CHANGELOG section, optional git-tag/build/twine-check). Same orchestrator
# is imported by .github/workflows/publish.yml so CI and a maintainer's
# laptop run the identical gate.
release_app = typer.Typer(
    help="Pre-tag release-readiness checks.",
    no_args_is_help=True,
)
app.add_typer(release_app, name="release")

console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}

# ---------------------------------------------------------------------------
# CLI input: prompt_toolkit for editing, paste, history, and display
# ---------------------------------------------------------------------------

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None  # original termios settings, restored on exit


def _flush_pending_tty_input() -> None:
    """Drop unread keypresses typed while the model was generating output."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    try:
        import termios

        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except Exception:
        pass

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except Exception:
        return


def _restore_terminal() -> None:
    """Restore terminal to its original state (echo, line buffering, etc.)."""
    if _SAVED_TERM_ATTRS is None:
        return
    try:
        import termios

        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except Exception:
        pass


def _init_prompt_session() -> None:
    """Create the prompt_toolkit session with persistent file history."""
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    # Save terminal state so we can restore it on exit
    try:
        import termios

        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    from pythinker.config.paths import get_cli_history_path

    history_file = get_cli_history_path()
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=SafeFileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,  # Enter submits (single line mode)
    )


def _make_console() -> Console:
    return Console(file=sys.stdout)


def _render_interactive_ansi(render_fn) -> str:
    """Render Rich output to ANSI so prompt_toolkit can print it safely."""
    ansi_console = Console(
        force_terminal=sys.stdout.isatty(),
        color_system=console.color_system or "standard",
        width=console.width,
    )
    with ansi_console.capture() as capture:
        render_fn(ansi_console)
    return capture.get()


def _print_agent_response(
    response: str,
    render_markdown: bool,
    metadata: dict | None = None,
) -> None:
    """Render assistant response with consistent terminal styling."""
    console = _make_console()
    content = response or ""
    body = _response_renderable(content, render_markdown, metadata)
    console.print()
    console.print(f"[cyan]{__logo__} pythinker[/cyan]")
    console.print(body)
    console.print()


def _response_renderable(content: str, render_markdown: bool, metadata: dict | None = None):
    """Render plain-text command output without markdown collapsing newlines."""
    if not render_markdown:
        return Text(content)
    if (metadata or {}).get("render_as") == "text":
        return Text(content)
    return Markdown(content)


async def _print_interactive_line(text: str) -> None:
    """Print async interactive updates with prompt_toolkit-safe Rich styling."""
    def _write() -> None:
        ansi = _render_interactive_ansi(
            lambda c: c.print(f"[dim]  ↳ {text}[/dim]")
        )
        print_formatted_text(ANSI(ansi), end="")

    await run_in_terminal(_write)


async def _print_interactive_response(
    response: str,
    render_markdown: bool,
    metadata: dict | None = None,
) -> None:
    """Print async interactive replies with prompt_toolkit-safe Rich styling."""
    def _write() -> None:
        content = response or ""
        ansi = _render_interactive_ansi(
            lambda c: (
                c.print(),
                c.print(f"[cyan]{__logo__} pythinker[/cyan]"),
                c.print(_response_renderable(content, render_markdown, metadata)),
                c.print(),
            )
        )
        print_formatted_text(ANSI(ansi), end="")

    await run_in_terminal(_write)


def _print_cli_progress_line(text: str, thinking: ThinkingSpinner | None) -> None:
    """Print a CLI progress line, pausing the spinner if needed."""
    with thinking.pause() if thinking else nullcontext():
        console.print(f"[dim]  ↳ {text}[/dim]")


async def _print_interactive_progress_line(text: str, thinking: ThinkingSpinner | None) -> None:
    """Print an interactive progress line, pausing the spinner if needed."""
    with thinking.pause() if thinking else nullcontext():
        await _print_interactive_line(text)


def _is_exit_command(command: str) -> bool:
    """Return True when input should end interactive chat."""
    return command.lower() in EXIT_COMMANDS


def _updates_enabled(config: Config | None = None) -> bool:
    if os.environ.get("PYTHINKER_NO_UPDATE_CHECK") == "1":
        return False
    if config is None:
        return True
    try:
        return bool(config.updates.check)
    except AttributeError:
        return True


def _maybe_emit_update_banner(config: Config | None = None) -> None:
    """Print a one-line update banner in interactive CLI sessions, throttled by cache.

    When stdin is a TTY, also offer an interactive yes/no prompt to run the
    upgrade right now.  Decline / non-TTY just prints the suggested command.
    """
    if not _updates_enabled(config):
        return
    try:
        info = check_for_update_sync()
    except Exception:
        return  # silent — never break startup over an update check
    if not info.update_available and not info.is_yanked:
        return
    suppress_for_already_notified = (
        info.latest
        and info.latest == info.last_notified
        and not info.is_yanked
    )
    if suppress_for_already_notified:
        return
    line = format_banner(info)
    if not line:
        return
    console.print(f"[dim]{line}[/dim]")
    if info.latest:
        try:
            mark_notified(info.latest)
        except Exception:
            pass

    # Interactive upgrade prompt. Only when:
    #  • stdin is a TTY
    #  • we have a safe upgrade command for this install method
    #  • not running with --no-update-check
    if not sys.stdin.isatty():
        return
    cmd = upgrade_command(info.install_method)
    if cmd is None:
        return
    try:
        prompt = f"Upgrade pythinker to {info.latest} now? [y/N] "
        answer = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return
    if answer not in {"y", "yes"}:
        return
    _run_in_place_upgrade(info, cmd)


def _run_in_place_upgrade(info: UpdateInfo, cmd: list[str]) -> None:
    """Run the upgrade subprocess under a filelock and re-exec on success."""
    import subprocess

    from filelock import FileLock
    from filelock import Timeout as FileLockTimeout

    lock_path = get_update_dir() / ".lock"
    try:
        with FileLock(str(lock_path)).acquire(blocking=False):
            console.print(f"Running: [cyan]{' '.join(cmd)}[/cyan]")
            try:
                proc = subprocess.run(cmd, check=False)
            except FileNotFoundError:
                console.print(
                    f"[red]Could not find {cmd[0]} on PATH.[/red] "
                    f"Run manually: [cyan]{suggested_upgrade_command(info.install_method)}[/cyan]"
                )
                return
    except FileLockTimeout as e:
        console.print(
            f"[yellow]Another upgrade is in progress (lock: {e.lock_file}).[/yellow]"
        )
        return

    if proc.returncode != 0:
        console.print(f"[red]Upgrade failed (exit {proc.returncode}).[/red]")
        return

    console.print("[green]✓ Upgrade complete. Restarting pythinker...[/green]")
    if sys.platform != "win32":
        set_restart_notice_to_env(channel="cli", chat_id="upgrade", reason="upgrade")
        os.execv(sys.executable, [sys.executable, "-m", "pythinker"] + sys.argv[1:])
    else:
        console.print("Please restart pythinker to use the new version.")


def _maybe_log_update_status(config: Config | None = None) -> None:
    """Log a one-line update status from the gateway boot path (no console banner)."""
    if not _updates_enabled(config):
        return
    try:
        info = check_for_update_sync()
    except Exception:
        return
    if info.is_yanked and info.latest:
        logger.warning(
            "Your pythinker {} was yanked from PyPI. Upgrade to {}.",
            info.current,
            info.latest,
        )
        return
    if info.update_available and info.latest:
        logger.info(
            "pythinker {} is available (you have {}). Run: {}",
            info.latest,
            info.current,
            suggested_upgrade_command(info.install_method),
        )


async def _read_interactive_input_async() -> str:
    """Read user input using prompt_toolkit (handles paste, history, display).

    prompt_toolkit natively handles:
    - Multiline paste (bracketed paste mode)
    - History navigation (up/down arrows)
    - Clean display (no ghost characters or artifacts)
    """
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc


def _preflight_port_or_die(host: str, port: int, *, label: str = "Service") -> None:
    """Bail out with a clear, actionable message if ``host:port`` is already bound.

    Without this, the gateway/serve startup logs "✓ Cron / ✓ Heartbeat / Agent loop
    started" several seconds before crashing with a raw asyncio EADDRINUSE traceback,
    which is confusing. We probe the bind upfront and raise typer.Exit(1) on failure.
    """
    import errno
    import socket

    bind_host = host or "127.0.0.1"
    fam = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
    sock = socket.socket(fam, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((bind_host, port))
    except OSError as exc:
        sock.close()
        if exc.errno not in (errno.EADDRINUSE, errno.EACCES):
            raise
        kind = "already in use" if exc.errno == errno.EADDRINUSE else "permission denied"
        console.print(
            f"[red]Error:[/red] {label} cannot bind {bind_host}:{port} — {kind}."
        )
        if sys.platform == "win32":
            console.print(
                f"  Find the process: [cyan]netstat -ano | findstr :{port}[/cyan]"
            )
        else:
            console.print(
                f"  Find the process: [cyan]ss -ltnp 'sport = :{port}'[/cyan]  "
                f"or  [cyan]lsof -iTCP:{port} -sTCP:LISTEN -P[/cyan]"
            )
        if exc.errno == errno.EADDRINUSE:
            console.print(
                "  Then either stop the existing process ([cyan]kill <pid>[/cyan]) "
                "or rerun with a different port ([cyan]--port <N>[/cyan])."
            )
        raise typer.Exit(1)
    else:
        sock.close()


def _get_websocket_channel(channels: Any) -> Any | None:
    get_channel = getattr(channels, "get_channel", None)
    if callable(get_channel):
        return get_channel("websocket")
    channel_map = getattr(channels, "channels", None)
    if isinstance(channel_map, dict):
        return channel_map.get("websocket")
    return None


def _webui_url_from_channel(channel: Any) -> str:
    cfg = getattr(channel, "config", {})

    def _cfg(name: str, default: Any) -> Any:
        if isinstance(cfg, dict):
            return cfg.get(name, default)
        return getattr(cfg, name, default)

    host = str(_cfg("host", "127.0.0.1") or "127.0.0.1").strip()
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    elif ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = int(_cfg("port", 8765))
    ssl_cert = str(_cfg("ssl_certfile", "") or "").strip()
    ssl_key = str(_cfg("ssl_keyfile", "") or "").strip()
    scheme = "https" if ssl_cert and ssl_key else "http"
    return f"{scheme}://{host}:{port}/"


def _print_webui_startup_status(websocket_channel: Any | None) -> None:
    if websocket_channel is not None:
        console.print(f"[green]✓[/green] WebUI: {_webui_url_from_channel(websocket_channel)}")
        return
    console.print(
        "[dim]WebUI: disabled "
        "(add channels.websocket.enabled=true to serve http://127.0.0.1:8765/)[/dim]"
    )


def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} pythinker v{__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """pythinker - Personal AI Assistant."""
    if ctx.invoked_subcommand is not None:
        return
    from pythinker.config.loader import get_config_path

    if not get_config_path().exists():
        # Pass every kwarg explicitly: when typer's `Option` default object
        # leaks through (because we're calling onboard() as a plain Python
        # function, not via dispatcher), it evaluates truthy and trips
        # `if print_required_flags:` / `if open_webui:` branches.
        onboard(
            workspace=None,
            config=None,
            non_interactive=False,
            flow=None,
            auth=None,
            auth_method=None,
            yes_security=False,
            start_gateway=False,
            skip_gateway=False,
            open_webui=False,
            reset=None,
            print_required_flags=False,
        )
    else:
        typer.echo(ctx.get_help())


# ============================================================================
# Onboard / Setup
# ============================================================================


@app.command()
def onboard(
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    non_interactive: bool = typer.Option(False, "--non-interactive"),
    flow: str | None = typer.Option(None, "--flow", help="quickstart | manual"),
    auth: str | None = typer.Option(None, "--auth", help="provider name or 'skip'"),
    auth_method: str | None = typer.Option(
        None,
        "--auth-method",
        help="auth method id (defaults to first in spec.auth_methods)",
    ),
    yes_security: bool = typer.Option(False, "--yes-security"),
    start_gateway: bool = typer.Option(False, "--start-gateway"),
    skip_gateway: bool = typer.Option(False, "--skip-gateway"),
    open_webui: bool = typer.Option(False, "--open-webui", help="Open WebUI in browser after gateway starts"),
    reset: str | None = typer.Option(
        None, "--reset", help="config | credentials | sessions | full"
    ),
    print_required_flags: bool = typer.Option(
        False,
        "--print-required-flags",
        help=(
            "Print the flag set needed for a zero-prompt --non-interactive "
            "run from the current config state, then exit. Useful for CI."
        ),
    ),
):
    """Run the onboarding wizard."""
    from pythinker.cli.onboard import required_headless_flags, run_onboard
    from pythinker.config.loader import get_config_path, load_config, save_config, set_config_path
    from pythinker.config.schema import Config as _Config

    if config:
        set_config_path(Path(config).expanduser().resolve())

    cfg_path = get_config_path()
    cfg = load_config(cfg_path) if cfg_path.exists() else _Config()

    if print_required_flags:
        flags = required_headless_flags(cfg)
        print(" ".join(flags))
        raise typer.Exit(0)

    try:
        result = run_onboard(
            cfg,
            non_interactive=non_interactive,
            flow=flow,
            yes_security=yes_security,
            auth=auth,
            auth_method=auth_method,
            start_gateway=start_gateway if start_gateway else None,
            skip_gateway=skip_gateway,
            reset=reset,
            workspace=workspace,
            open_webui=open_webui,
        )
    except Exception as e:
        console.print(f"[red]✗[/red] Error during configuration: {e}")
        console.print(
            "[yellow]Please run 'pythinker onboard' again to complete setup.[/yellow]"
        )
        raise typer.Exit(1)

    if result.should_save:
        cfg = result.config
        save_config(cfg, cfg_path)
        console.print(f"[green]✓[/green] Config saved at {cfg_path}")

    # Inject channel defaults into the on-disk config (idempotent).
    # Runs regardless of should_save so an existing config file gets
    # missing channel fields backfilled even when the wizard was discarded.
    if cfg_path.exists():
        _onboard_plugins(cfg_path)

    if result.should_save:
        agent_cmd = 'pythinker agent -m "Hello!"'
        gateway_cmd = "pythinker gateway"
        if config:
            agent_cmd += f" --config {cfg_path}"
            gateway_cmd += f" --config {cfg_path}"

        console.print(f"\n{__logo__} pythinker is ready!")
        console.print("\nNext steps:")
        console.print(f"  1. Chat: [cyan]{agent_cmd}[/cyan]")
        console.print(f"  2. Start gateway: [cyan]{gateway_cmd}[/cyan]")
    else:
        console.print("[yellow]Configuration discarded. No changes were saved.[/yellow]")


def _merge_missing_defaults(existing: Any, defaults: Any) -> Any:
    """Recursively fill in missing values from defaults without overwriting user config."""
    if not isinstance(existing, dict) or not isinstance(defaults, dict):
        return existing

    merged = dict(existing)
    for key, value in defaults.items():
        if key not in merged:
            merged[key] = value
        else:
            merged[key] = _merge_missing_defaults(merged[key], value)
    return merged


def _onboard_plugins(config_path: Path) -> None:
    """Inject default config for all discovered channels (built-in + plugins)."""
    import json

    from pythinker.channels.registry import discover_all

    all_channels = discover_all()
    if not all_channels:
        return
    # Skip silently when no config file exists yet — happens on wizard-discard
    # paths where the user aborted before the bare config was written.
    if not config_path.exists():
        return

    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)

    channels = data.setdefault("channels", {})
    for name, cls in all_channels.items():
        if name not in channels:
            channels[name] = cls.default_config()
        else:
            channels[name] = _merge_missing_defaults(channels[name], cls.default_config())

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _make_provider(config: Config):
    """CLI-facing provider builder.

    Delegates to the canonical `providers.factory.make_provider` and
    translates the factory's ValueError into Rich-formatted messages plus
    `typer.Exit(1)`. Validation logic lives in the factory; the CLI only
    chooses how to surface the result.
    """
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

    from pythinker.config.loader import get_config_path

    path = config_path or get_config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    if "memoryWindow" in raw.get("agents", {}).get("defaults", {}):
        console.print("[dim]Hint: `memoryWindow` in your config is no longer used "
            "and can be safely removed.[/dim]")


def _migrate_cron_store(config: "Config") -> None:
    """One-time migration: move legacy global cron store into the workspace."""
    from pythinker.config.paths import get_cron_dir

    legacy_path = get_cron_dir() / "jobs.json"
    new_path = config.workspace_path / "cron" / "jobs.json"
    if legacy_path.is_file() and not new_path.exists():
        new_path.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.move(str(legacy_path), str(new_path))


# ============================================================================
# OpenAI-Compatible API Server
# ============================================================================


@app.command()
def serve(
    port: int | None = typer.Option(None, "--port", "-p", help="API server port"),
    host: str | None = typer.Option(None, "--host", "-H", help="Bind address"),
    timeout: float | None = typer.Option(None, "--timeout", "-t", help="Per-request timeout (seconds)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="DEBUG-level logs"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="WARNING-level logs"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Start the OpenAI-compatible API server (/v1/chat/completions)."""
    try:
        maybe_prompt_github_star()
    except Exception:  # noqa: BLE001
        pass  # Non-fatal: star prompt must never block launch.
    try:
        from aiohttp import web  # noqa: F401
    except ImportError:
        console.print("[red]aiohttp is required. Install with: pip install 'pythinker-ai[api]'[/red]")
        raise typer.Exit(1)

    from loguru import logger

    from pythinker.agent.loop import AgentLoop
    from pythinker.api.server import create_app
    from pythinker.bus.queue import MessageBus
    from pythinker.session.manager import SessionManager
    from pythinker.utils.log import configure_logging

    runtime_config = _load_runtime_config(config, workspace)
    cli_level = "DEBUG" if verbose else ("WARNING" if quiet else None)
    configure_logging(level=cli_level, config=runtime_config)
    api_cfg = runtime_config.api
    host = host if host is not None else api_cfg.host
    port = port if port is not None else api_cfg.port
    timeout = timeout if timeout is not None else api_cfg.timeout
    _preflight_port_or_die(host, port, label="API server")
    sync_workspace_templates(runtime_config.workspace_path)
    bus = MessageBus()
    provider = _make_provider(runtime_config)
    # Honour the operator's runtime.sessionCacheMax bound. AgentLoop only
    # applies session_cache_max when it constructs the SessionManager itself;
    # passing a prebuilt manager bypasses that path, so cap it here.
    session_manager = SessionManager(
        runtime_config.workspace_path,
        cache_max=runtime_config.runtime.session_cache_max,
    )

    from pythinker.runtime._bootstrap import build_policy, install_telemetry_sink

    install_telemetry_sink(runtime_config)
    policy = build_policy(runtime_config)

    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=runtime_config.workspace_path,
        model=runtime_config.agents.defaults.model,
        max_iterations=runtime_config.agents.defaults.max_tool_iterations,
        context_window_tokens=runtime_config.agents.defaults.context_window_tokens,
        context_block_limit=runtime_config.agents.defaults.context_block_limit,
        max_tool_result_chars=runtime_config.agents.defaults.max_tool_result_chars,
        provider_retry_mode=runtime_config.agents.defaults.provider_retry_mode,
        web_config=runtime_config.tools.web,
        exec_config=runtime_config.tools.exec,
        restrict_to_workspace=runtime_config.tools.restrict_to_workspace,
        session_manager=session_manager,
        mcp_servers=runtime_config.tools.mcp_servers,
        channels_config=runtime_config.channels,
        timezone=runtime_config.agents.defaults.timezone,
        unified_session=runtime_config.agents.defaults.unified_session,
        disabled_skills=runtime_config.agents.defaults.disabled_skills,
        session_ttl_minutes=runtime_config.agents.defaults.session_ttl_minutes,
        tools_config=runtime_config.tools,
        runtime_config=runtime_config.runtime,
        policy=policy,
        browser_config_loader=_load_browser_config,
        session_cache_max=runtime_config.runtime.session_cache_max,
    )

    model_name = runtime_config.agents.defaults.model
    console.print(f"{__logo__} Starting OpenAI-compatible API server")
    console.print(f"  {'[cyan]Endpoint[/cyan]'} : http://{host}:{port}/v1/chat/completions")
    console.print(f"  {'[cyan]Model[/cyan]'}    : {model_name}")
    console.print(f"  {'[cyan]Session[/cyan]'}  : api:default")
    console.print(f"  {'[cyan]Timeout[/cyan]'}  : {timeout}s")
    if host in {"0.0.0.0", "::"}:
        console.print("[yellow]Warning: API is bound to all interfaces. "
            "Only do this behind a trusted network boundary, firewall, or reverse proxy.[/yellow]")
    console.print()

    api_app = create_app(agent_loop, model_name=model_name, request_timeout=timeout)

    async def on_startup(_app):
        await agent_loop._connect_mcp()

    async def on_cleanup(_app):
        await agent_loop.close_mcp()
        await agent_loop.close_browser()

    api_app.on_startup.append(on_startup)
    api_app.on_cleanup.append(on_cleanup)

    web.run_app(api_app, host=host, port=port, print=lambda msg: logger.info(msg))


# ============================================================================
# Gateway / Server
# ============================================================================


@app.command()
def gateway(
    port: int | None = typer.Option(None, "--port", "-p", help="Gateway port"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="DEBUG-level logs"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="WARNING-level logs"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Start the pythinker gateway."""
    try:
        maybe_prompt_github_star()
    except Exception:  # noqa: BLE001
        pass  # Non-fatal: star prompt must never block launch.
    from pythinker.utils.log import configure_logging

    cfg = _load_runtime_config(config, workspace)
    cli_level = "DEBUG" if verbose else ("WARNING" if quiet else None)
    configure_logging(level=cli_level, config=cfg)
    _run_gateway(cfg, port=port)


def _run_gateway(
    config: Config,
    *,
    port: int | None = None,
    open_browser_url: str | None = None,
) -> None:
    """Shared gateway runtime; ``open_browser_url`` opens a tab once channels are up."""
    from pythinker.agent.loop import AgentLoop
    from pythinker.bus.queue import MessageBus
    from pythinker.channels.manager import ChannelManager
    from pythinker.cron.service import CronService
    from pythinker.cron.types import CronJob
    from pythinker.heartbeat.service import HeartbeatService
    from pythinker.session.manager import SessionManager

    port = port if port is not None else config.gateway.port

    _preflight_port_or_die(config.gateway.host, port, label="Gateway")

    console.print(f"{__logo__} Starting pythinker gateway version {__version__} on port {port}...")
    _maybe_log_update_status(config)
    sync_workspace_templates(config.workspace_path)
    bus = MessageBus()
    provider = _make_provider(config)
    # Honour the operator's runtime.sessionCacheMax bound. AgentLoop only
    # applies session_cache_max when it constructs the SessionManager itself;
    # passing a prebuilt manager bypasses that path, so cap it here.
    session_manager = SessionManager(
        config.workspace_path,
        cache_max=config.runtime.session_cache_max,
    )

    # Preserve existing single-workspace installs, but keep custom workspaces clean.
    if is_default_workspace(config.workspace_path):
        _migrate_cron_store(config)

    # Create cron service with workspace-scoped store
    cron_store_path = config.workspace_path / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    from pythinker.runtime._bootstrap import build_policy, install_telemetry_sink

    install_telemetry_sink(config)
    policy = build_policy(config)

    # Create agent with cron service
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        context_window_tokens=config.agents.defaults.context_window_tokens,
        web_config=config.tools.web,
        context_block_limit=config.agents.defaults.context_block_limit,
        max_tool_result_chars=config.agents.defaults.max_tool_result_chars,
        provider_retry_mode=config.agents.defaults.provider_retry_mode,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        session_manager=session_manager,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
        timezone=config.agents.defaults.timezone,
        unified_session=config.agents.defaults.unified_session,
        disabled_skills=config.agents.defaults.disabled_skills,
        session_ttl_minutes=config.agents.defaults.session_ttl_minutes,
        tools_config=config.tools,
        policy=policy,
        runtime_config=config.runtime,
        browser_config_loader=_load_browser_config,
        session_cache_max=config.runtime.session_cache_max,
    )

    # Set cron callback (needs agent)
    async def on_cron_job(job: CronJob) -> str | None:
        """Execute a cron job through the agent."""
        # Dream is an internal job — run directly, not through the agent loop.
        if job.name == "dream":
            try:
                # Dream runs as the named system agent so its tool calls flow
                # through the egress gateway under the system_dream policy
                # exemption (read_file / edit_file / write_file). The plan's
                # "no traffic bypasses controls" invariant rules out the old
                # direct-tool-execute path.
                ctx = agent._normalize_context_for_cron(
                    job_id="dream", session_key="cron:dream",
                )
                ctx = ctx.with_agent_id(
                    "system_dream",
                    policy_version=agent.policy.policy_version,
                )
                await agent.dream.run(request_context=ctx, egress=agent.egress)
                logger.info("Dream cron job completed")
            except Exception:
                logger.exception("Dream cron job failed")
            return None

        from pythinker.agent.tools.cron import CronTool
        from pythinker.agent.tools.message import MessageTool
        from pythinker.utils.evaluator import evaluate_response

        reminder_note = (
            "[Scheduled Task] Timer finished.\n\n"
            f"Task '{job.name}' has been triggered.\n"
            f"Scheduled instruction: {job.payload.message}"
        )

        cron_tool = agent.tools.get("cron")
        cron_token = None
        if isinstance(cron_tool, CronTool):
            cron_token = cron_tool.set_cron_context(True)

        async def _silent(*_args, **_kwargs):
            pass

        try:
            resp = await agent.process_direct(
                reminder_note,
                session_key=f"cron:{job.id}",
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to or "direct",
                on_progress=_silent,
            )
        finally:
            if isinstance(cron_tool, CronTool) and cron_token is not None:
                cron_tool.reset_cron_context(cron_token)

        response = resp.content if resp else ""

        message_tool = agent.tools.get("message")
        if job.payload.deliver and isinstance(message_tool, MessageTool) and message_tool._sent_in_turn:
            return response

        if job.payload.deliver and job.payload.to and response:
            should_notify = await evaluate_response(
                response, reminder_note, provider, agent.model,
            )
            if should_notify:
                from pythinker.bus.events import OutboundMessage
                await bus.publish_outbound(OutboundMessage(
                    channel=job.payload.channel or "cli",
                    chat_id=job.payload.to,
                    content=response,
                ))
        return response

    cron.on_job = on_cron_job

    # Create channel manager (forwards SessionManager so the WebSocket channel
    # can serve the embedded webui's REST surface).
    channels = ChannelManager(config, bus, session_manager=session_manager)
    websocket_channel = _get_websocket_channel(channels)
    if websocket_channel is not None:
        from pythinker.admin.service import AdminService
        from pythinker.config.loader import get_config_path

        websocket_channel._admin_service = AdminService(  # noqa: SLF001
            config=config,
            config_path=get_config_path(),
            session_manager=session_manager,
            agent_loop=agent,
            channel_manager=channels,
        )

    def _pick_heartbeat_target() -> tuple[str, str]:
        """Pick a routable channel/chat target for heartbeat-triggered messages."""
        enabled = set(channels.enabled_channels)
        # Prefer the most recently updated non-internal session on an enabled channel.
        for item in session_manager.list_sessions():
            key = item.get("key") or ""
            if ":" not in key:
                continue
            channel, chat_id = key.split(":", 1)
            if channel in {"cli", "system"}:
                continue
            if channel in enabled and chat_id:
                return channel, chat_id
        # Fallback keeps prior behavior but remains explicit.
        return "cli", "direct"

    # Create heartbeat service
    async def on_heartbeat_execute(tasks: str) -> str:
        """Phase 2: execute heartbeat tasks through the full agent loop."""
        channel, chat_id = _pick_heartbeat_target()

        async def _silent(*_args, **_kwargs):
            pass

        resp = await agent.process_direct(
            tasks,
            session_key="heartbeat",
            channel=channel,
            chat_id=chat_id,
            on_progress=_silent,
        )

        # Keep a small tail of heartbeat history so the loop stays bounded
        # without losing all short-term context between runs.
        session = agent.sessions.get_or_create("heartbeat")
        session.retain_recent_legal_suffix(hb_cfg.keep_recent_messages)
        agent.sessions.save(session)

        return resp.content if resp else ""

    async def on_heartbeat_notify(response: str) -> None:
        """Deliver a heartbeat response to the user's channel."""
        from pythinker.bus.events import OutboundMessage
        channel, chat_id = _pick_heartbeat_target()
        if channel == "cli":
            return  # No external channel available to deliver to
        await bus.publish_outbound(OutboundMessage(channel=channel, chat_id=chat_id, content=response))

    hb_cfg = config.gateway.heartbeat
    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        provider=provider,
        model=agent.model,
        on_execute=on_heartbeat_execute,
        on_notify=on_heartbeat_notify,
        interval_s=hb_cfg.interval_s,
        enabled=hb_cfg.enabled,
        timezone=config.agents.defaults.timezone,
    )

    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")
    _print_webui_startup_status(websocket_channel)

    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")

    console.print(f"[green]✓[/green] Heartbeat: every {hb_cfg.interval_s}s")

    async def _health_server(host: str, health_port: int):
        """Lightweight HTTP health endpoint on the gateway port."""
        import json as _json

        async def handle(reader, writer):
            try:
                data = await asyncio.wait_for(reader.read(4096), timeout=5)
            except (asyncio.TimeoutError, ConnectionError):
                writer.close()
                return

            request_line = data.split(b"\r\n", 1)[0].decode("utf-8", errors="replace")
            method, path = "", ""
            parts = request_line.split(" ")
            if len(parts) >= 2:
                method, path = parts[0], parts[1]

            if method == "GET" and path == "/health":
                body = _json.dumps({"status": "ok"})
                resp = (
                    f"HTTP/1.0 200 OK\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    f"\r\n{body}"
                )
            else:
                body = "Not Found"
                resp = (
                    f"HTTP/1.0 404 Not Found\r\n"
                    f"Content-Type: text/plain\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    f"\r\n{body}"
                )

            writer.write(resp.encode())
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle, host, health_port)
        console.print(f"[green]✓[/green] Health endpoint: http://{host}:{health_port}/health")
        async with server:
            await server.serve_forever()
    # Register Dream system job (always-on, idempotent on restart)
    dream_cfg = config.agents.defaults.dream
    if dream_cfg.model_override:
        agent.dream.model = dream_cfg.model_override
    agent.dream.max_batch_size = dream_cfg.max_batch_size
    agent.dream.max_iterations = dream_cfg.max_iterations
    agent.dream.annotate_line_ages = dream_cfg.annotate_line_ages
    from pythinker.cron.types import CronJob, CronPayload
    cron.register_system_job(CronJob(
        id="dream",
        name="dream",
        schedule=dream_cfg.build_schedule(config.agents.defaults.timezone),
        payload=CronPayload(kind="system_event"),
    ))
    console.print(f"[green]✓[/green] Dream: {dream_cfg.describe_schedule()}")

    async def _open_browser_when_ready() -> None:
        """Wait for the gateway to bind, then point the user's browser at the webui."""
        if not open_browser_url:
            return
        import webbrowser
        # Channels start asynchronously; a short poll lets us avoid racing the bind.
        for _ in range(40):  # ~4s max
            try:
                reader, writer = await asyncio.open_connection(
                    config.gateway.host or "127.0.0.1", port
                )
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                break
            except OSError:
                await asyncio.sleep(0.1)
        try:
            webbrowser.open(open_browser_url)
            console.print(f"[green]✓[/green] Opened browser at {open_browser_url}")
        except Exception as e:
            console.print(f"[yellow]Could not open browser ({e}); visit {open_browser_url}[/yellow]")

    async def run():
        try:
            await cron.start()
            await heartbeat.start()
            tasks = [
                agent.run(),
                channels.start_all(),
                _health_server(config.gateway.host, port),
            ]
            if open_browser_url:
                tasks.append(_open_browser_when_ready())
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            console.print("\nShutting down...")
        except OSError as exc:
            import errno
            if exc.errno in (errno.EADDRINUSE, errno.EACCES):
                kind = "already in use" if exc.errno == errno.EADDRINUSE else (
                    "permission denied"
                )
                bind_host = config.gateway.host or "127.0.0.1"
                console.print(
                    f"[red]\nError:[/red] Gateway could not bind "
                    f"{bind_host}:{port} — {kind}."
                )
                if sys.platform != "win32":
                    console.print(
                        f"  Find the process: [cyan]ss -ltnp 'sport = :{port}'[/cyan]"
                        f"  or  [cyan]lsof -iTCP:{port} -sTCP:LISTEN -P[/cyan]"
                    )
                if exc.errno == errno.EADDRINUSE:
                    console.print(
                        "  Then [cyan]kill <pid>[/cyan] the existing gateway "
                        "or rerun with [cyan]--port <N>[/cyan]."
                    )
            else:
                import traceback

                console.print("[red]\nError: Gateway crashed unexpectedly[/red]")
                console.print(traceback.format_exc())
        except Exception:
            import traceback

            console.print("[red]\nError: Gateway crashed unexpectedly[/red]")
            console.print(traceback.format_exc())
        finally:
            await agent.close_mcp()
            await agent.close_browser()
            heartbeat.stop()
            cron.stop()
            agent.stop()
            await channels.stop_all()
            # Flush all cached sessions to durable storage before exit.
            # This prevents data loss on filesystems with write-back
            # caching (rclone VFS, NFS, FUSE mounts, etc.).
            flushed = agent.sessions.flush_all()
            if flushed:
                logger.info("Shutdown: flushed {} session(s) to disk", flushed)

    asyncio.run(run())


# ============================================================================
# Agent Commands
# ============================================================================


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:direct", "--session", "-s", help="Session ID"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown", help="Render assistant output as Markdown"),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show pythinker runtime logs during chat"),
):
    """Interact with the agent directly."""
    try:
        maybe_prompt_github_star()
    except Exception:  # noqa: BLE001
        pass  # Non-fatal: star prompt must never block launch.
    from pythinker.agent.loop import AgentLoop
    from pythinker.bus.queue import MessageBus
    from pythinker.cron.service import CronService
    from pythinker.utils.log import configure_logging

    config = _load_runtime_config(config, workspace)
    # --logs respects the configured level (default INFO); --no-logs forces
    # WARNING so chat output isn't crowded with runtime breadcrumbs.
    configure_logging(level=None if logs else "WARNING", config=config)
    sync_workspace_templates(config.workspace_path)

    bus = MessageBus()
    provider = _make_provider(config)

    # Preserve existing single-workspace installs, but keep custom workspaces clean.
    if is_default_workspace(config.workspace_path):
        _migrate_cron_store(config)

    # Create cron service with workspace-scoped store
    cron_store_path = config.workspace_path / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    if logs:
        logger.enable("pythinker")
    else:
        logger.disable("pythinker")

    from pythinker.runtime._bootstrap import build_policy, install_telemetry_sink

    install_telemetry_sink(config)
    policy = build_policy(config)

    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        context_window_tokens=config.agents.defaults.context_window_tokens,
        web_config=config.tools.web,
        context_block_limit=config.agents.defaults.context_block_limit,
        max_tool_result_chars=config.agents.defaults.max_tool_result_chars,
        provider_retry_mode=config.agents.defaults.provider_retry_mode,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
        timezone=config.agents.defaults.timezone,
        unified_session=config.agents.defaults.unified_session,
        disabled_skills=config.agents.defaults.disabled_skills,
        session_ttl_minutes=config.agents.defaults.session_ttl_minutes,
        tools_config=config.tools,
        runtime_config=config.runtime,
        policy=policy,
        browser_config_loader=_load_browser_config,
        session_cache_max=config.runtime.session_cache_max,
    )
    restart_notice = consume_restart_notice_from_env()
    if restart_notice and should_show_cli_restart_notice(restart_notice, session_id):
        _print_agent_response(
            format_restart_completed_message(restart_notice),
            render_markdown=False,
        )

    _maybe_emit_update_banner()

    # Shared reference for progress callbacks
    _thinking: ThinkingSpinner | None = None

    async def _cli_progress(content: str, *, tool_hint: bool = False) -> None:
        ch = agent_loop.channels_config
        if ch and tool_hint and not ch.send_tool_hints:
            return
        if ch and not tool_hint and not ch.send_progress:
            return
        _print_cli_progress_line(content, _thinking)

    if message:
        # Single message mode — direct call, no bus needed
        async def run_once():
            renderer = StreamRenderer(render_markdown=markdown)
            response = await agent_loop.process_direct(
                message, session_id,
                channel="cli",
                on_progress=_cli_progress,
                on_stream=renderer.on_delta,
                on_stream_end=renderer.on_end,
            )
            if not renderer.streamed:
                await renderer.close()
                _print_agent_response(
                    response.content if response else "",
                    render_markdown=markdown,
                    metadata=response.metadata if response else None,
                )
            await agent_loop.close_mcp()
            await agent_loop.close_browser()

        asyncio.run(run_once())
    else:
        # Interactive mode — route through bus like other channels
        from pythinker.bus.events import InboundMessage
        _init_prompt_session()
        console.print(
            f"{__logo__} Interactive mode [bold cyan]({config.agents.defaults.model})[/bold cyan] "
            "— type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit\n"
        )

        if ":" in session_id:
            cli_channel, cli_chat_id = session_id.split(":", 1)
        else:
            cli_channel, cli_chat_id = "cli", session_id

        def _handle_signal(signum, frame):
            sig_name = signal.Signals(signum).name
            _restore_terminal()
            console.print(f"\nReceived {sig_name}, goodbye!")
            sys.exit(0)

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
        # SIGHUP is not available on Windows
        if hasattr(signal, 'SIGHUP'):
            signal.signal(signal.SIGHUP, _handle_signal)
        # Ignore SIGPIPE to prevent silent process termination when writing to closed pipes
        # SIGPIPE is not available on Windows
        if hasattr(signal, 'SIGPIPE'):
            signal.signal(signal.SIGPIPE, signal.SIG_IGN)

        async def run_interactive():
            bus_task = asyncio.create_task(agent_loop.run())
            turn_done = asyncio.Event()
            turn_done.set()
            turn_response: list[tuple[str, dict]] = []
            renderer: StreamRenderer | None = None

            async def _consume_outbound():
                while True:
                    try:
                        msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)

                        if msg.metadata.get("_stream_delta"):
                            if renderer:
                                await renderer.on_delta(msg.content)
                            continue
                        if msg.metadata.get("_stream_end"):
                            if renderer:
                                await renderer.on_end(
                                    resuming=msg.metadata.get("_resuming", False),
                                )
                            continue
                        if msg.metadata.get("_streamed"):
                            turn_done.set()
                            continue

                        if msg.metadata.get("_progress"):
                            is_tool_hint = msg.metadata.get("_tool_hint", False)
                            ch = agent_loop.channels_config
                            if ch and is_tool_hint and not ch.send_tool_hints:
                                pass
                            elif ch and not is_tool_hint and not ch.send_progress:
                                pass
                            else:
                                await _print_interactive_progress_line(msg.content, _thinking)
                            continue

                        if not turn_done.is_set():
                            if msg.content:
                                turn_response.append((msg.content, dict(msg.metadata or {})))
                            turn_done.set()
                        elif msg.content:
                            await _print_interactive_response(
                                msg.content,
                                render_markdown=markdown,
                                metadata=msg.metadata,
                            )

                    except asyncio.TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        break

            outbound_task = asyncio.create_task(_consume_outbound())

            try:
                while True:
                    try:
                        _flush_pending_tty_input()
                        # Stop spinner before user input to avoid prompt_toolkit conflicts
                        if renderer:
                            renderer.stop_for_input()
                        user_input = await _read_interactive_input_async()
                        command = user_input.strip()
                        if not command:
                            continue

                        if _is_exit_command(command):
                            _restore_terminal()
                            console.print("\nGoodbye!")
                            break

                        turn_done.clear()
                        turn_response.clear()
                        renderer = StreamRenderer(render_markdown=markdown)

                        await bus.publish_inbound(InboundMessage(
                            channel=cli_channel,
                            sender_id="user",
                            chat_id=cli_chat_id,
                            content=user_input,
                            metadata={"_wants_stream": True},
                        ))

                        await turn_done.wait()

                        if turn_response:
                            content, meta = turn_response[0]
                            if content and not meta.get("_streamed"):
                                if renderer:
                                    await renderer.close()
                                _print_agent_response(
                                    content, render_markdown=markdown, metadata=meta,
                                )
                        elif renderer and not renderer.streamed:
                            await renderer.close()
                    except KeyboardInterrupt:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
                    except EOFError:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
            finally:
                agent_loop.stop()
                outbound_task.cancel()
                await asyncio.gather(bus_task, outbound_task, return_exceptions=True)
                await agent_loop.close_mcp()
                await agent_loop.close_browser()

        asyncio.run(run_interactive())


# ============================================================================
# TUI Commands
# ============================================================================


@app.command()
def tui(
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    session: str = typer.Option("cli:tui", "--session", "-s", help="Session key"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
    theme: str | None = typer.Option(None, "--theme", help="Override TUI theme for this run"),
    logs: str | None = typer.Option(None, "--logs", help="Log file (default: ~/.pythinker/logs/tui-<pid>.log)"),
):
    """Open the full-screen TUI chat."""
    from pythinker.cli.tui import TuiOptions, run_tui
    rc = asyncio.run(run_tui(TuiOptions(
        workspace=workspace,
        session_key=session,
        config_path=config,
        theme=theme,
        log_file=logs,
    )))
    raise typer.Exit(code=rc)


@app.command(name="chat")
def chat_alias(
    workspace: str | None = typer.Option(None, "--workspace", "-w"),
    session: str = typer.Option("cli:tui", "--session", "-s"),
    config: str | None = typer.Option(None, "--config", "-c"),
    theme: str | None = typer.Option(None, "--theme"),
    logs: str | None = typer.Option(None, "--logs"),
):
    """Alias for `pythinker tui`."""
    tui(workspace=workspace, session=session, config=config, theme=theme, logs=logs)


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(
    help="Channel adapter state and lifecycle.", no_args_is_help=True
)
app.add_typer(channels_app, name="channels")


@channels_app.command("status")
def channels_status(
    config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Show channel status."""
    from pythinker.channels.registry import discover_all
    from pythinker.config.loader import load_config, set_config_path

    resolved_config_path = Path(config_path).expanduser().resolve() if config_path else None
    if resolved_config_path is not None:
        set_config_path(resolved_config_path)

    config = load_config(resolved_config_path)

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled")

    for name, cls in sorted(discover_all().items()):
        section = getattr(config.channels, name, None)
        if section is None:
            enabled = False
        elif isinstance(section, dict):
            enabled = section.get("enabled", False)
        else:
            enabled = getattr(section, "enabled", False)
        table.add_row(
            cls.display_name,
            "[green]\u2713[/green]" if enabled else "[dim]\u2717[/dim]",
        )

    console.print(table)


def _get_bridge_dir() -> Path:
    """Get the bridge directory, setting it up if needed."""
    import shutil
    import subprocess

    # User's bridge location
    from pythinker.config.paths import get_bridge_install_dir

    user_bridge = get_bridge_install_dir()

    # Check if already built
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge

    # Check for npm
    npm_path = shutil.which("npm")
    if not npm_path:
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)

    # Find source bridge: first check package data, then source dir
    pkg_bridge = Path(__file__).parent.parent / "bridge"  # pythinker/bridge (installed)
    src_bridge = Path(__file__).parent.parent.parent / "bridge"  # repo root/bridge (dev)

    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge

    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall pythinker")
        raise typer.Exit(1)

    console.print(f"{__logo__} Setting up bridge...")

    # Copy to user directory
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))

    # Install and build
    try:
        console.print("  Installing dependencies...")
        subprocess.run([npm_path, "install"], cwd=user_bridge, check=True, capture_output=True)

        console.print("  Building...")
        subprocess.run([npm_path, "run", "build"], cwd=user_bridge, check=True, capture_output=True)

        console.print("[green]✓[/green] Bridge ready\n")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e}[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1)

    return user_bridge


@channels_app.command("login")
def channels_login(
    channel_name: str = typer.Argument(..., help="Channel name (e.g. whatsapp)"),
    force: bool = typer.Option(False, "--force", "-f", help="Force re-authentication even if already logged in"),
    config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Authenticate with a channel via QR code or other interactive login."""
    from pythinker.channels.registry import discover_all
    from pythinker.config.loader import load_config, set_config_path

    resolved_config_path = Path(config_path).expanduser().resolve() if config_path else None
    if resolved_config_path is not None:
        set_config_path(resolved_config_path)

    config = load_config(resolved_config_path)
    channel_cfg = getattr(config.channels, channel_name, None) or {}

    # Validate channel exists
    all_channels = discover_all()
    if channel_name not in all_channels:
        available = ", ".join(all_channels.keys())
        console.print(f"{f'[red]Unknown channel: {channel_name}[/red]'}  Available: {available}")
        raise typer.Exit(1)

    console.print(f"{__logo__} {all_channels[channel_name].display_name} Login\n")

    channel_cls = all_channels[channel_name]
    channel = channel_cls(channel_cfg, bus=None)

    success = asyncio.run(channel.login(force=force))

    if not success:
        raise typer.Exit(1)


# ============================================================================
# Plugin Commands
# ============================================================================

plugins_app = typer.Typer(help="Manage channel plugins")
app.add_typer(plugins_app, name="plugins")


@plugins_app.command("list")
def plugins_list():
    """List all discovered channels (built-in and plugins)."""
    from pythinker.channels.registry import discover_all, discover_channel_names
    from pythinker.config.loader import load_config

    config = load_config()
    builtin_names = set(discover_channel_names())
    all_channels = discover_all()

    table = Table(title="Channel Plugins")
    table.add_column("Name", style="cyan")
    table.add_column("Source", style="magenta")
    table.add_column("Enabled")

    for name in sorted(all_channels):
        cls = all_channels[name]
        source = "builtin" if name in builtin_names else "plugin"
        section = getattr(config.channels, name, None)
        if section is None:
            enabled = False
        elif isinstance(section, dict):
            enabled = section.get("enabled", False)
        else:
            enabled = getattr(section, "enabled", False)
        table.add_row(
            cls.display_name,
            source,
            "[green]yes[/green]" if enabled else "[dim]no[/dim]",
        )

    console.print(table)


# ============================================================================
# Status / Doctor Commands
# ============================================================================


@app.command()
def doctor(
    non_interactive: bool = typer.Option(
        False, "--non-interactive", help="Terse output suitable for CI / scripting."
    ),
):
    """Diagnose install, config, and authentication state."""
    from pythinker.cli.doctor import run as run_doctor

    raise typer.Exit(run_doctor(non_interactive=non_interactive))


@app.command()
def status():
    """Show pythinker status."""
    from pythinker.config.loader import get_config_path, load_config

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} pythinker Status\n")

    _ok = "[green]✓[/green]"
    _fail = "[red]✗[/red]"
    _not_set = "[dim]not set[/dim]"
    console.print(f"Config: {config_path} {_ok if config_path.exists() else _fail}")
    console.print(f"Workspace: {workspace} {_ok if workspace.exists() else _fail}")

    if config_path.exists():
        from pythinker.providers.registry import PROVIDERS

        console.print(f"Model: {config.agents.defaults.model}")

        # Check API keys from registry
        for spec in PROVIDERS:
            p = getattr(config.providers, spec.name, None)
            if p is None:
                continue
            if spec.is_oauth:
                console.print(f"{spec.label}: [green]✓ (OAuth)[/green]")
            elif spec.is_local:
                # Local deployments show api_base instead of api_key
                if p.api_base:
                    console.print(f"{spec.label}: [green]✓ {p.api_base}[/green]")
                else:
                    console.print(f"{spec.label}: {_not_set}")
            else:
                has_key = bool(p.api_key)
                console.print(f"{spec.label}: {_ok if has_key else _not_set}")


@app.command()
def update(
    check: bool = typer.Option(False, "--check", help="Check only; don't install."),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip confirmation."),
    restart: bool = typer.Option(
        False,
        "--restart",
        help="POSIX only: re-exec pythinker after a successful upgrade.",
    ),
    prerelease: bool = typer.Option(
        False,
        "--prerelease",
        help="Include pre-releases when picking the latest version.",
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        help=(
            "Install this exact version (e.g. '2.0.0'). Refused on editable / "
            "container / unknown installs. Major-version jumps require this "
            "flag — `pythinker upgrade` only ever moves to the latest stable."
        ),
        metavar="VERSION",
    ),
):
    """Check for and install pythinker updates from PyPI."""
    import subprocess

    from filelock import FileLock
    from filelock import Timeout as FileLockTimeout
    from packaging.version import InvalidVersion, Version

    if target is not None:
        try:
            Version(target)
        except InvalidVersion:
            console.print(
                f"[red]✗ '{target}' is not a valid PEP 440 version "
                f"(e.g. '2.0.0', '2.0.0a1').[/red]"
            )
            raise typer.Exit(2)

    info = check_for_update_sync(include_prereleases=prerelease, force_refresh=True)

    if not info.checked_ok and info.error_kind != "no-acceptable-release":
        # Network errors aren't fatal for the --target path: we already have a
        # specific version to install and don't need PyPI's "what's latest" reply.
        if target is None:
            console.print(
                f"[yellow]Could not reach PyPI[/yellow]: {info.error_message or info.error_kind}"
            )
            raise typer.Exit(2)
        console.print(
            f"[yellow]PyPI metadata fetch failed ({info.error_kind}); proceeding with --target {target}.[/yellow]"
        )

    if target is not None:
        # Exact-version path. Reuses install-method detection but does not gate
        # on update_available; the user is asking to switch to a specific
        # version (could be pinning, downgrading, or major-jumping).
        if info.current == target:
            console.print(
                f"pythinker is already at {target} — nothing to do."
            )
            raise typer.Exit(0)
        suggested = suggested_target_command(info.install_method, target)
        console.print(
            f"Target install: pythinker [bold]{target}[/bold] (you have [bold]{info.current}[/bold])"
        )
        console.print(f"Install method: {info.install_method.value}")
        console.print(f"Suggested command: [cyan]{suggested}[/cyan]")
        if check:
            raise typer.Exit(0)
        cmd = target_install_command(info.install_method, target)
        if cmd is None:
            console.print(
                f"[yellow]Exact-version installs are not safe for "
                f"{info.install_method.value}.[/yellow]"
            )
            console.print(f"Run manually: [cyan]{suggested}[/cyan]")
            raise typer.Exit(2)
    else:
        if info.is_yanked and not info.update_available:
            console.print(
                f"[red]⚠  Your version {info.current} was yanked from PyPI but no replacement is available.[/red]"
            )
            raise typer.Exit(1)

        if not info.update_available:
            console.print(
                f"pythinker {info.current} is up to date (PyPI: {info.latest or info.current})."
            )
            raise typer.Exit(0)

        console.print(
            f"Update available: pythinker [bold]{info.latest}[/bold] (you have [bold]{info.current}[/bold])"
        )
        suggested = suggested_upgrade_command(info.install_method)
        console.print(f"Install method: {info.install_method.value}")
        console.print(f"Suggested command: [cyan]{suggested}[/cyan]")

        if check:
            raise typer.Exit(0)

        # Don't auto-jump a major. The user has to opt in via --target.
        try:
            current_major = Version(info.current).major
            latest_major = Version(info.latest or info.current).major
        except InvalidVersion:
            current_major = latest_major = 0
        if latest_major > current_major:
            console.print(
                f"[yellow]Refusing to auto-upgrade across a major version "
                f"({info.current} → {info.latest}). Re-run with "
                f"`--target {info.latest}` to opt in explicitly.[/yellow]"
            )
            raise typer.Exit(2)

        cmd = upgrade_command(info.install_method)
        if cmd is None:
            console.print(
                f"[yellow]Auto-upgrade is not safe for {info.install_method.value} installs.[/yellow]"
            )
            console.print(f"Run manually: [cyan]{suggested}[/cyan]")
            raise typer.Exit(2)

    if not yes:
        try:
            confirmed = typer.confirm("Run the upgrade now?", default=True)
        except typer.Abort:
            confirmed = False
        if not confirmed:
            console.print("Aborted.")
            raise typer.Exit(0)

    lock_path = get_update_dir() / ".lock"

    def _do_upgrade() -> int:
        console.print(f"Running: [cyan]{' '.join(cmd)}[/cyan]")
        try:
            proc = subprocess.run(cmd, check=False)
        except FileNotFoundError:
            console.print(
                f"[red]Could not find {cmd[0]} on PATH.[/red] Run manually: [cyan]{suggested}[/cyan]"
            )
            return 127
        return proc.returncode

    try:
        with FileLock(str(lock_path)).acquire(blocking=False):
            rc = _do_upgrade()
    except FileLockTimeout as e:
        console.print(
            f"[yellow]Another `pythinker update` is in progress (lock: {e.lock_file}).[/yellow]"
        )
        raise typer.Exit(2)

    if rc != 0:
        console.print(f"[red]Upgrade command exited with status {rc}.[/red]")
        raise typer.Exit(rc)

    if info.install_method is InstallMethod.UV_TOOL:
        # uv tool upgrade preserves install-time version constraints. If the
        # user pinned a range that excludes ``info.latest``, the upgrade is a
        # no-op. Surface that explicitly so they aren't confused.
        try:
            installed = subprocess.run(
                ["pythinker", "--version"], capture_output=True, text=True, check=False
            )
            stdout = (installed.stdout or "") + (installed.stderr or "")
            if info.latest and info.latest not in stdout:
                console.print(
                    "[yellow]uv preserved your original version constraint.[/yellow] "
                    "To force a clean upgrade ignoring the pin: "
                    f"[cyan]uv tool install {suggested.split()[-1]}[/cyan]"
                )
        except Exception:
            pass

    console.print("[green]✓ Upgrade complete.[/green]")

    if restart:
        if sys.platform == "win32":
            console.print(
                "[yellow]--restart is POSIX-only.[/yellow] Restart pythinker manually."
            )
            raise typer.Exit(0)
        console.print("Restarting pythinker...")
        set_restart_notice_to_env(channel="cli", chat_id="update", reason="upgrade")
        os.execv(sys.executable, [sys.executable, "-m", "pythinker"] + sys.argv[1:])

    raise typer.Exit(0)


@app.command()
def token(
    bytes_: int = typer.Option(
        32,
        "--bytes",
        "-b",
        min=16,
        max=64,
        help="Byte length of the token before url-safe encoding (default: 32 = 256 bits).",
    ),
):
    """Generate a strong random token for channels.websocket.token / token_issue_secret.

    Uses ``secrets.token_urlsafe`` — cryptographically secure, URL-safe, and
    suitable for query-string usage on a WebSocket handshake.
    """
    import secrets

    console.print(secrets.token_urlsafe(bytes_))


# ============================================================================
# Provider — OAuth login subcommands
# ============================================================================
#
# OAuth providers (openai-codex, github-copilot) intentionally do NOT appear
# in the `pythinker onboard` "[P] LLM Provider" picker because that flow
# prompts for an API key — which OAuth providers don't have. Without a
# separate entry point, users hit a dead end. `pythinker provider login
# <name>` is the dedicated surface and triggers each provider's OAuth
# flow (browser-based for Codex, device-code for Copilot).

provider_app = typer.Typer(help="Manage providers")
app.add_typer(provider_app, name="provider")


_LOGIN_HANDLERS: dict[str, Any] = {}


def _register_login(name: str):
    def decorator(fn):
        _LOGIN_HANDLERS[name] = fn
        return fn
    return decorator


@provider_app.command("login")
def provider_login(
    provider: str = typer.Argument(
        ..., help="OAuth provider to log in to (e.g. 'openai-codex', 'github-copilot')."
    ),
):
    """Authenticate with an OAuth-based LLM provider.

    Examples:
        pythinker provider login openai-codex
        pythinker provider login github-copilot
    """
    from pythinker.providers.registry import PROVIDERS

    key = provider.replace("-", "_")
    spec = next((s for s in PROVIDERS if s.name == key and s.is_oauth), None)
    if not spec:
        names = ", ".join(s.name.replace("_", "-") for s in PROVIDERS if s.is_oauth)
        console.print(f"[red]Unknown OAuth provider: {provider}[/red]  Supported: {names}")
        raise typer.Exit(1)

    handler = _LOGIN_HANDLERS.get(spec.name)
    if not handler:
        console.print(f"[red]Login not implemented for {spec.label}[/red]")
        raise typer.Exit(1)

    console.print(f"{__logo__} OAuth Login - {spec.label}\n")
    handler()


@_register_login("openai_codex")
def _login_openai_codex() -> None:
    try:
        from oauth_cli_kit import get_token, login_oauth_interactive
    except ImportError:
        console.print("[red]oauth_cli_kit not installed. Run: pip install oauth-cli-kit[/red]")
        raise typer.Exit(1)

    from pythinker.auth.oauth_remote import run_oauth_with_hint

    token = None
    try:
        token = get_token()
    except Exception:
        pass
    if not (token and token.access):
        console.print("[cyan]Starting interactive OAuth login...[/cyan]\n")
        token = run_oauth_with_hint(
            login_oauth_interactive,
            print_fn=lambda s: console.print(s),
            prompt_fn=lambda s: typer.prompt(s),
        )
    if not (token and token.access):
        console.print("[red]✗ Authentication failed[/red]")
        raise typer.Exit(1)
    account = getattr(token, "account_id", None) or "OpenAI"
    console.print(f"[green]✓ Authenticated with OpenAI Codex[/green]  [dim]{account}[/dim]")


@_register_login("github_copilot")
def _login_github_copilot() -> None:
    try:
        from pythinker.providers.github_copilot_provider import login_github_copilot
    except ImportError as exc:
        console.print(f"[red]Cannot start GitHub Copilot login: {exc}[/red]")
        raise typer.Exit(1)

    from pythinker.auth.oauth_remote import run_oauth_with_hint

    console.print("[cyan]Starting GitHub Copilot device flow...[/cyan]\n")
    try:
        token = run_oauth_with_hint(
            login_github_copilot,
            print_fn=lambda s: console.print(s),
            prompt_fn=lambda s: typer.prompt(s),
            ssh_hint=(
                "Device-flow tip: open the URL printed below on any device "
                "and enter the displayed code there."
            ),
        )
    except Exception as exc:
        console.print(f"[red]Authentication error: {exc}[/red]")
        raise typer.Exit(1)
    account = getattr(token, "account_id", None) or "GitHub"
    console.print(f"[green]✓ Authenticated with GitHub Copilot[/green]  [dim]{account}[/dim]")


@app.command()
def upgrade(
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip confirmation."),
    no_restart: bool = typer.Option(
        False, "--no-restart", help="Don't re-exec pythinker after upgrading."
    ),
    prerelease: bool = typer.Option(
        False, "--prerelease", help="Include pre-releases when picking the latest version."
    ),
):
    """Download and install the latest pythinker release from PyPI.

    Convenience alias of ``pythinker update -y --restart``: by default this
    upgrades and restarts in one step.  Pass ``--no-restart`` to skip the
    re-exec.
    """
    # Delegate by re-invoking the `update` command logic via the same Typer app.
    # We don't simply call update() because Typer wraps it to raise typer.Exit;
    # forwarding sys.argv-style args keeps single-source-of-truth in update().
    args: list[str] = ["update", "-y"]
    if not no_restart:
        args.append("--restart")
    if prerelease:
        args.append("--prerelease")
    _ = yes  # absorbed into the implicit -y above; kept for CLI symmetry.
    # Run the update command in-process by invoking the typer app callable.
    # ``app(args, standalone_mode=False)`` raises typer.Exit on success or click
    # exceptions on user errors. Translate to process exit codes here.
    import click

    try:
        app(args, standalone_mode=False)
    except click.exceptions.Exit as e:
        raise typer.Exit(e.exit_code)
    except click.ClickException as e:
        e.show()
        raise typer.Exit(e.exit_code)


# ============================================================================
# auth — provider authentication state
# ============================================================================


def _auth_state(spec, provider_cfg) -> tuple[str, str]:
    """Return ``(state_label, detail)`` for one provider.

    state_label values: ``"AUTHENTICATED" | "MISSING" | "ERROR" | "NOT-CONFIGURED"``.
    detail is provider-specific human-readable context (account_id, env-var
    indirection, api_base override, etc.). Designed for tabular display, so
    both fields are always non-None even if empty-string.
    """
    if spec.is_oauth:
        # We must not trigger an interactive login from a status command.
        # ``oauth_cli_kit.get_token`` reads stored tokens without prompting;
        # any failure (missing file, parse error) is treated as MISSING so the
        # command stays read-only.
        try:
            if spec.name == "openai_codex":
                from oauth_cli_kit import get_token as _get_codex_token
                tok = _get_codex_token()
            elif spec.name == "github_copilot":
                from pythinker.providers.github_copilot_provider import (
                    get_github_copilot_login_status as _get_copilot_token,
                )
                tok = _get_copilot_token()
            else:
                return "ERROR", f"unknown OAuth provider {spec.name!r}"
        except Exception as exc:  # noqa: BLE001
            return "MISSING", f"no stored token ({type(exc).__name__})"
        if not tok or not getattr(tok, "access", None):
            return "MISSING", "no stored token"
        account = getattr(tok, "account_id", None) or "(no account_id)"
        return "AUTHENTICATED", account

    if spec.is_local:
        base = getattr(provider_cfg, "api_base", "") or ""
        if base:
            return "AUTHENTICATED", base
        return "NOT-CONFIGURED", "set api_base in config"

    if spec.is_direct:
        # User supplies everything; nothing to validate without making a call.
        return "AUTHENTICATED", "direct (no auth check)"

    key = getattr(provider_cfg, "api_key", "") or ""
    if not key:
        return "MISSING", f"set {spec.env_key} or paste a key in config"
    if key.startswith("${") and key.endswith("}"):
        return "AUTHENTICATED", f"env var {key[2:-1]}"
    return "AUTHENTICATED", f"key set ({len(key)} chars)"


@auth_app.command("list")
def auth_list(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Show authentication state for every provider in the registry.

    Read-only: never triggers an OAuth flow. Tokens are inspected from
    on-disk storage only. Use ``pythinker provider login <name>`` to
    refresh missing or expired credentials.
    """
    from rich.table import Table

    from pythinker.config.loader import load_config
    from pythinker.providers.registry import PROVIDERS

    cfg = load_config()
    table = Table(show_header=True, header_style="bold cyan", show_lines=False)
    table.add_column("Provider", min_width=14)
    table.add_column("Type", min_width=8)
    table.add_column("State", min_width=14)
    table.add_column("Detail", overflow="fold")

    style = {
        "AUTHENTICATED": "[green]AUTH[/green]",
        "MISSING": "[yellow]MISSING[/yellow]",
        "ERROR": "[red]ERROR[/red]",
        "NOT-CONFIGURED": "[dim]NOT SET[/dim]",
    }

    def _kind(spec) -> str:
        if spec.is_oauth:
            return "oauth"
        if spec.is_local:
            return "local"
        if spec.is_gateway:
            return "gateway"
        if spec.is_direct:
            return "direct"
        return "api-key"

    for spec in PROVIDERS:
        provider_cfg = getattr(cfg.providers, spec.name, None)
        if provider_cfg is None:
            continue
        state, detail = _auth_state(spec, provider_cfg)
        table.add_row(spec.label, _kind(spec), style.get(state, state), detail)

    console.print(table)


# ============================================================================
# channels — channel adapter state
# ============================================================================


def _channel_state(channel_cfg) -> tuple[str, str]:
    """Return ``(state_label, detail)`` for one channel config block.

    Static inspection only — no live gateway probe, no socket open. The
    detail field surfaces whichever credential or endpoint is the
    "configured" signal for that channel (token, webhook, base URL).
    """
    if channel_cfg is None:
        return "OFF", "no config block"

    def _get(name: str):
        if isinstance(channel_cfg, dict):
            return channel_cfg.get(name) or channel_cfg.get(_to_camel(name))
        return getattr(channel_cfg, name, None)

    enabled = bool(_get("enabled"))
    if not enabled:
        return "OFF", ""

    # Best-effort credential-presence check across the known channel shapes.
    # Anything truthy in any of these slots counts as "configured"; we don't
    # parse the value because ${VAR} env-refs are valid configurations.
    for cred_field in ("token", "bot_token", "webhook_url", "base_url", "homeserver"):
        val = _get(cred_field)
        if val:
            redacted = val if val.startswith("${") else f"{cred_field}:set"
            return "ON", redacted

    # Enabled but nothing that looks like a credential — flag it so the user
    # knows the channel will start but probably won't authenticate.
    return "ON", "no credential field set"


def _to_camel(snake: str) -> str:
    parts = snake.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


@channels_app.command("list")
def channels_list(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Show enabled / configured state for every channel adapter.

    Inspects ``~/.pythinker/config.json`` only — does not touch a running
    gateway. Useful for "did my last edit save?" sanity checks before
    restarting the gateway.
    """
    from rich.table import Table

    from pythinker.channels.registry import discover_all
    from pythinker.config.loader import load_config

    cfg = load_config()
    table = Table(show_header=True, header_style="bold cyan", show_lines=False)
    table.add_column("Channel", min_width=12)
    table.add_column("State", min_width=6)
    table.add_column("Detail", overflow="fold")

    style = {
        "ON": "[green]ON[/green]",
        "OFF": "[dim]OFF[/dim]",
    }

    for name, cls in sorted(discover_all().items()):
        channel_cfg = getattr(cfg.channels, name, None)
        state, detail = _channel_state(channel_cfg)
        label = getattr(cls, "display_name", name)
        table.add_row(label, style.get(state, state), detail)

    console.print(table)


# ============================================================================
# config — non-interactive get / set / unset of single fields
# ============================================================================


def _walk_config_path(root, dotted: str):
    """Walk ``dotted`` (e.g. ``agents.defaults.model``) through a Pydantic
    model + dict tree, returning ``(parent, last_segment)``.

    Snake-case and camelCase are both accepted at every segment so the CLI
    matches both the Python attribute names and the on-disk JSON keys.
    Raises ``KeyError`` with a precise "what failed where" message on
    unknown segments — bare AttributeError leaks Python internals.
    """
    from pydantic.alias_generators import to_camel

    parts = dotted.split(".")
    cursor = root
    for i, raw in enumerate(parts[:-1]):
        cursor = _resolve_segment(cursor, raw, dotted=dotted, depth=i, to_camel=to_camel)
    return cursor, parts[-1]


def _model_field_lookup(cursor, candidates: tuple[str, ...]) -> str | None:
    """Resolve the first matching declared Pydantic field name for any of
    ``candidates``. Gating on ``model_fields`` (not bare ``hasattr``) is
    a security boundary: ``hasattr`` would happily resolve ``__class__``,
    ``__dict__``, ``model_config``, etc., letting ``config get __class__``
    surface a Python class repr or worse permit ``config set`` to mutate
    framework internals. Only declared fields are addressable."""
    field_names = type(cursor).model_fields
    aliases = {info.alias: name for name, info in field_names.items() if info.alias}
    for cand in candidates:
        if cand in field_names:
            return cand
        if cand in aliases:
            return aliases[cand]
    return None


def _resolve_segment(cursor, raw: str, *, dotted: str, depth: int, to_camel) -> object:
    from pydantic import BaseModel

    snake, camel = raw, to_camel(raw)
    if isinstance(cursor, BaseModel):
        attr = _model_field_lookup(cursor, (snake, camel))
        if attr is not None:
            return getattr(cursor, attr)
    if isinstance(cursor, dict):
        for cand in (snake, camel):
            if cand in cursor:
                return cursor[cand]
    raise KeyError(
        f"unknown config segment {raw!r} at position {depth} of {dotted!r}"
    )


def _read_config_value(cursor, leaf: str):
    from pydantic import BaseModel
    from pydantic.alias_generators import to_camel

    snake, camel = leaf, to_camel(leaf)
    if isinstance(cursor, BaseModel):
        attr = _model_field_lookup(cursor, (snake, camel))
        if attr is not None:
            return getattr(cursor, attr)
    if isinstance(cursor, dict):
        for cand in (snake, camel):
            if cand in cursor:
                return cursor[cand]
    raise KeyError(f"unknown leaf field {leaf!r}")


def _coerce_value(raw: str):
    """Best-effort string→typed conversion so ``config set foo true`` writes a
    real bool, not the string ``"true"``. JSON parsing handles all primitive
    types; non-JSON inputs fall back to the raw string."""
    import json

    if raw == "":
        return ""
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


@config_app.command("get")
def config_get(
    path: str = typer.Argument(..., help="Dotted path, e.g. agents.defaults.model"),
):
    """Print one config value. Read-only."""
    from pythinker.config.editing import ConfigEditError, read_config_value
    from pythinker.config.loader import load_config

    cfg = load_config()
    try:
        value = read_config_value(cfg, path)
    except (ConfigEditError, KeyError) as exc:
        console.print(f"[red]Error:[/red] {exc.args[0]}")
        raise typer.Exit(1)
    # Round-trip through json.dumps so the output is parseable + unambiguous
    # (booleans render as `true`, strings get quoted, None as `null`).
    import json
    print(json.dumps(value, default=str))


@config_app.command("set")
def config_set(
    path: str = typer.Argument(..., help="Dotted path, e.g. agents.defaults.model"),
    value: str = typer.Argument(..., help="New value (JSON or plain string)"),
):
    """Write one config value back to ~/.pythinker/config.json.

    The value is JSON-parsed when possible so booleans and integers don't
    end up as strings. The full config is re-validated through the schema
    after the edit, so type errors surface here, not at the next gateway
    boot. Prints a hint to restart the gateway since config is loaded once
    at startup.
    """
    from pythinker.config.editing import (
        ConfigEditError,
        save_config_with_backup,
        set_config_value,
    )
    from pythinker.config.loader import get_config_path, load_config

    cfg = load_config()
    try:
        result = set_config_value(cfg, path, value)
    except (ConfigEditError, KeyError) as exc:
        console.print(f"[red]Error:[/red] {exc.args[0]}")
        raise typer.Exit(1)

    save_config_with_backup(cfg, get_config_path())
    console.print(f"[green]✓[/green] {path} = {result.value!r}")
    console.print(
        "[dim]Restart the gateway/api for the change to take effect "
        "(config is loaded once at startup).[/dim]"
    )


@config_app.command("unset")
def config_unset(
    path: str = typer.Argument(..., help="Dotted path to reset to schema default"),
):
    """Reset one field to its schema default and save the config.

    Only works on Pydantic-model fields (the schema knows the default);
    dict entries are removed entirely instead. Same restart-required hint
    as ``config set``.
    """
    from pythinker.config.editing import (
        ConfigEditError,
        save_config_with_backup,
        unset_config_value,
    )
    from pythinker.config.loader import get_config_path, load_config

    cfg = load_config()
    try:
        unset_config_value(cfg, path)
    except (ConfigEditError, KeyError) as exc:
        console.print(f"[red]Error:[/red] {exc.args[0]}")
        raise typer.Exit(1)

    save_config_with_backup(cfg, get_config_path())
    console.print(f"[green]✓[/green] {path} reset to default")
    console.print(
        "[dim]Restart the gateway/api for the change to take effect.[/dim]"
    )


# ============================================================================
# restart — stop a running service and exec a fresh one in foreground
# ============================================================================


def _pids_listening_on(port: int) -> list[int]:
    """Return PIDs holding a TCP LISTEN socket on ``port``.

    Uses ``ss -ltnpH`` because it's already required by the existing
    preflight error message, doesn't need root, and avoids a psutil
    dependency. Empty list when nothing is bound or when ss isn't on PATH.
    """
    import re
    import shutil
    import subprocess

    if shutil.which("ss") is None:
        return []
    try:
        out = subprocess.run(
            ["ss", "-ltnpH", f"sport = :{port}"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    pids: list[int] = []
    for line in out.stdout.splitlines():
        # users:(("pythinker",pid=12345,fd=6))
        for match in re.finditer(r"pid=(\d+)", line):
            pids.append(int(match.group(1)))
    return pids


def _stop_service(port: int, label: str, *, timeout: float = 8.0) -> bool:
    """SIGTERM every PID listening on ``port``, escalate to SIGKILL after
    ``timeout``. Returns True iff the port is free at the end."""
    import os
    import signal
    import time

    pids = _pids_listening_on(port)
    if not pids:
        console.print(f"[dim]No {label} listening on :{port}[/dim]")
        return True

    console.print(f"Stopping {label} (pids: {', '.join(map(str, pids))})...")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pids_listening_on(port):
            return True
        time.sleep(0.25)

    # Escalate: anything still bound gets SIGKILL.
    leftover = _pids_listening_on(port)
    for pid in leftover:
        console.print(f"[yellow]SIGTERM ignored, escalating SIGKILL on {pid}[/yellow]")
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    time.sleep(0.5)
    return not _pids_listening_on(port)


@restart_app.command("gateway")
def restart_gateway(
    port: int | None = typer.Option(None, "--port", "-p", help="Override gateway port"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    no_start: bool = typer.Option(False, "--no-start", help="Stop only — don't restart"),
):
    """Stop the running gateway and re-exec a fresh one in the foreground."""
    import os
    import sys

    from pythinker.config.loader import load_config

    cfg = load_config(config and __import__("pathlib").Path(config))
    target_port = port or cfg.gateway.port
    if not _stop_service(target_port, "gateway"):
        console.print(f"[red]Error:[/red] could not free port {target_port}")
        raise typer.Exit(1)

    if no_start:
        return

    argv = [sys.executable, "-m", "pythinker", "gateway"]
    if port is not None:
        argv.extend(["--port", str(port)])
    if config is not None:
        argv.extend(["--config", config])
    console.print(f"Starting fresh gateway on :{target_port} — Ctrl-C to stop")
    os.execvp(sys.executable, argv)


@restart_app.command("api")
def restart_api(
    port: int | None = typer.Option(None, "--port", "-p", help="Override API port"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    no_start: bool = typer.Option(False, "--no-start", help="Stop only — don't restart"),
):
    """Stop the running API server and re-exec a fresh one in the foreground."""
    import os
    import sys

    from pythinker.config.loader import load_config

    cfg = load_config(config and __import__("pathlib").Path(config))
    target_port = port or cfg.api.port
    if not _stop_service(target_port, "api"):
        console.print(f"[red]Error:[/red] could not free port {target_port}")
        raise typer.Exit(1)

    if no_start:
        return

    argv = [sys.executable, "-m", "pythinker", "serve"]
    if port is not None:
        argv.extend(["--port", str(port)])
    if config is not None:
        argv.extend(["--config", config])
    console.print(f"Starting fresh API server on :{target_port} — Ctrl-C to stop")
    os.execvp(sys.executable, argv)


# ============================================================================
# auth logout — delete stored OAuth tokens
# ============================================================================


def _oauth_token_path(spec) -> "Path | None":
    """Return the on-disk token file for an OAuth provider, or None.

    For openai_codex we resolve oauth_cli_kit's storage path; for
    github_copilot we use the FileTokenStorage shape declared in the
    provider module. Other OAuth providers return None — caller treats
    that as "unsupported".
    """
    from pathlib import Path

    if spec.name == "openai_codex":
        try:
            from oauth_cli_kit import FileTokenStorage
            return Path(FileTokenStorage().path)
        except Exception:  # noqa: BLE001
            return None
    if spec.name == "github_copilot":
        try:
            from pythinker.providers.github_copilot_provider import _storage
            return Path(_storage().path)
        except Exception:  # noqa: BLE001
            return None
    return None


@auth_app.command("logout")
def auth_logout(
    provider: str = typer.Argument(..., help="Provider name, e.g. openai_codex, github_copilot"),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip confirmation"),
):
    """Delete the stored OAuth token for ``provider``.

    Read-only providers (API key, gateway, local) have nothing to log out
    of — exits with a hint to ``config unset`` the api_key field instead.
    Always confirms before unlinking the token file unless ``-y`` is given.
    """
    from pythinker.providers.registry import find_by_name

    spec = find_by_name(provider)
    if spec is None:
        console.print(f"[red]Error:[/red] unknown provider {provider!r}")
        raise typer.Exit(1)
    if not spec.is_oauth:
        console.print(
            f"[yellow]{spec.label} is not an OAuth provider.[/yellow] To clear the API key, "
            f"run: [cyan]pythinker config unset providers.{spec.name}.api_key[/cyan]"
        )
        raise typer.Exit(1)

    path = _oauth_token_path(spec)
    if path is None:
        console.print(f"[red]Error:[/red] don't know where {spec.label} stores its token")
        raise typer.Exit(1)

    if not path.exists():
        console.print(f"[dim]No stored {spec.label} token at {path} — nothing to do.[/dim]")
        return

    if not yes:
        confirm = typer.confirm(f"Delete stored {spec.label} token at {path}?", default=False)
        if not confirm:
            console.print("Cancelled.")
            raise typer.Exit(0)

    try:
        path.unlink()
    except OSError as exc:
        console.print(f"[red]Error:[/red] could not unlink {path}: {exc}")
        raise typer.Exit(1)
    console.print(f"[green]✓[/green] {spec.label} logged out — re-run [cyan]pythinker provider login {spec.name}[/cyan] when needed.")


# ============================================================================
# backup — snapshot / verify / restore ~/.pythinker/config.json
# ============================================================================


def _backup_dir() -> "Path":
    """Where backups live. Sibling of config.json so a single ~/.pythinker
    backup directory captures the canonical store, and so the existing
    wizard ``--reset`` rename pattern (``config.json.bak.<ts>``) stays
    discoverable next to it."""
    from pythinker.config.loader import get_config_path
    return get_config_path().parent / "backups"


def _backup_filename(label: str | None = None) -> str:
    """Timestamped backup filename. Sortable lexicographically so
    ``backup list`` doesn't need to parse names to order them."""
    from datetime import datetime
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if label:
        # Restrict label to a safe subset so the filename stays portable.
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)[:32]
        return f"config.{stamp}.{safe}.json"
    return f"config.{stamp}.json"


@backup_app.command("create")
def backup_create(
    label: str | None = typer.Option(None, "--label", "-l", help="Optional tag (alnum/underscore/dash)"),
):
    """Atomically copy ~/.pythinker/config.json to a timestamped backup file.

    Uses ``shutil.copy2`` so mtime is preserved — useful when scripting a
    "snapshot before edit, restore on rollback" flow. Refuses if there's
    no config to back up.
    """
    import shutil

    from pythinker.config.loader import get_config_path

    cfg_path = get_config_path()
    if not cfg_path.exists():
        console.print(f"[red]Error:[/red] no config at {cfg_path} — nothing to back up.")
        raise typer.Exit(1)

    bkdir = _backup_dir()
    bkdir.mkdir(parents=True, exist_ok=True)
    dest = bkdir / _backup_filename(label)
    try:
        shutil.copy2(cfg_path, dest)
    except OSError as exc:
        console.print(f"[red]Error:[/red] copy failed: {exc}")
        raise typer.Exit(1)

    import hashlib
    sha = hashlib.sha256(dest.read_bytes()).hexdigest()[:12]
    console.print(f"[green]✓[/green] {dest} (sha256 {sha})")


@backup_app.command("list")
def backup_list():
    """Show all on-disk backups, oldest first.

    Includes wizard-generated ``config.json.bak.<ts>`` files in the parent
    dir so the user sees one consolidated view, not two separate stores.
    """
    from rich.table import Table

    from pythinker.config.loader import get_config_path

    bkdir = _backup_dir()
    cfg_dir = get_config_path().parent

    sources: list[Path] = []
    if bkdir.exists():
        sources.extend(sorted(bkdir.glob("config.*.json")))
    # Wizard's reset path uses config.json.bak.<ts>; surface both so
    # users don't have to know which command produced which file.
    sources.extend(sorted(cfg_dir.glob("config.json.bak.*")))

    if not sources:
        console.print(f"[dim]No backups in {bkdir} or {cfg_dir}/config.json.bak.*[/dim]")
        return

    import hashlib
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Path", overflow="fold")
    table.add_column("Size", justify="right")
    table.add_column("sha256")
    for path in sources:
        try:
            data = path.read_bytes()
            sha = hashlib.sha256(data).hexdigest()[:12]
            table.add_row(str(path), f"{len(data):,}", sha)
        except OSError:
            table.add_row(str(path), "[red]unreadable[/red]", "")
    console.print(table)


@backup_app.command("verify")
def backup_verify(
    path: str = typer.Argument(..., help="Path to backup file to verify"),
):
    """Round-trip a backup through the schema to prove it'd load cleanly.

    Catches: corrupt JSON, schema-incompatible legacy fields, missing
    required keys. Uses the same code path as ``load_config``, so a
    backup that verifies here will boot the gateway/serve.
    """
    import json

    import pydantic

    from pythinker.config.loader import _migrate_config
    from pythinker.config.schema import Config

    p = Path(path)
    if not p.exists():
        console.print(f"[red]Error:[/red] no such file: {p}")
        raise typer.Exit(1)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        console.print(f"[red]Error:[/red] not valid JSON: {exc}")
        raise typer.Exit(1)
    try:
        Config.model_validate(_migrate_config(data))
    except pydantic.ValidationError as exc:
        console.print("[red]Error:[/red] schema rejected backup:")
        for err in exc.errors()[:5]:
            loc = ".".join(str(x) for x in err.get("loc", ()))
            console.print(f"  {loc}: {err.get('msg')}")
        raise typer.Exit(1)
    console.print(f"[green]✓[/green] {p} loads + validates cleanly")


@backup_app.command("restore")
def backup_restore(
    path: str = typer.Argument(..., help="Path to backup file to restore"),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip confirmation"),
):
    """Restore a backup to ~/.pythinker/config.json.

    Always backs up the *current* config first to ``config.pre-restore.<ts>.json``
    in the backups dir — restoring is itself reversible. Verifies the
    target backup before swapping; refuses if it'd produce an invalid
    config.
    """
    import json
    import shutil
    from datetime import datetime

    import pydantic

    from pythinker.config.loader import _migrate_config, get_config_path
    from pythinker.config.schema import Config

    src = Path(path)
    if not src.exists():
        console.print(f"[red]Error:[/red] no such file: {src}")
        raise typer.Exit(1)
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
        Config.model_validate(_migrate_config(data))
    except (json.JSONDecodeError, pydantic.ValidationError) as exc:
        console.print(f"[red]Error:[/red] {src} would not load: {exc}")
        console.print("[dim]Refusing to restore an invalid backup.[/dim]")
        raise typer.Exit(1)

    cfg_path = get_config_path()
    if not yes:
        confirm = typer.confirm(
            f"Restore {src} → {cfg_path}? (current config will be safety-backed-up first)",
            default=False,
        )
        if not confirm:
            console.print("Cancelled.")
            raise typer.Exit(0)

    if cfg_path.exists():
        bkdir = _backup_dir()
        bkdir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safety = bkdir / f"config.pre-restore.{stamp}.json"
        try:
            shutil.copy2(cfg_path, safety)
            console.print(f"Safety backup of current config: {safety}")
        except OSError as exc:
            console.print(f"[red]Error:[/red] could not safety-backup current config: {exc}")
            raise typer.Exit(1)

    # Atomic write: copy to a sibling temp file in the same directory, then
    # ``os.replace`` it onto cfg_path. POSIX guarantees ``os.replace`` is
    # atomic on the same filesystem; on NT it's atomic for files but not
    # directories. A crash mid-copy leaves the .tmp file behind without
    # touching the live config; the safety backup written above provides
    # the second recovery layer if the rename itself ever fails.
    tmp_path = cfg_path.with_suffix(cfg_path.suffix + ".restore-tmp")
    try:
        shutil.copy2(src, tmp_path)
        os.replace(tmp_path, cfg_path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        console.print(f"[red]Error:[/red] restore failed: {exc}")
        raise typer.Exit(1)
    console.print(f"[green]✓[/green] {cfg_path} restored from {src}")
    console.print("[dim]Restart the gateway/api for the change to take effect.[/dim]")


# ============================================================================
# cleanup — plan / run destructive resets
# ============================================================================


def _cleanup_targets(scope: str) -> list["Path"]:
    """Return the paths that ``cleanup run --scope <scope>`` would delete.

    Mirrors ``onboard_views.reset.apply_immediate`` plus the config rename
    that the wizard defers to its summary step. Used by both ``plan``
    (to display) and ``run`` (to delete) so the dry-run can never lie
    about what the run will do.
    """
    from pythinker.cli.onboard_views.reset import (
        api_workspace_dir,
        oauth_cli_kit_token_paths,
        sessions_dir,
    )
    from pythinker.config.loader import get_config_path

    targets: list[Path] = []
    if scope in ("config", "credentials", "sessions", "full"):
        cfg = get_config_path()
        if cfg.exists():
            targets.append(cfg)
    if scope in ("credentials", "sessions", "full"):
        for tok in oauth_cli_kit_token_paths():
            if tok.exists():
                targets.append(tok)
    if scope in ("sessions", "full"):
        sd = sessions_dir()
        if sd.exists():
            targets.append(sd)
    if scope == "full":
        ws = api_workspace_dir()
        if ws.exists():
            targets.append(ws)
    return targets


def _format_size(path: "Path") -> str:
    """Return a human-readable size for a file or recursive directory."""
    try:
        if path.is_file():
            n = path.stat().st_size
        else:
            n = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    except OSError:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


_VALID_CLEANUP_SCOPES = ("config", "credentials", "sessions", "full")


@cleanup_app.command("plan")
def cleanup_plan(
    scope: str = typer.Option(
        "config", "--scope", "-s",
        help="config | credentials | sessions | full",
    ),
):
    """Dry-run: list every file/dir that ``cleanup run --scope <scope>`` would delete."""
    from rich.table import Table

    if scope not in _VALID_CLEANUP_SCOPES:
        console.print(
            f"[red]Error:[/red] invalid scope {scope!r}; "
            f"choose one of: {', '.join(_VALID_CLEANUP_SCOPES)}"
        )
        raise typer.Exit(1)

    targets = _cleanup_targets(scope)
    if not targets:
        console.print(f"[dim]Nothing to clean up at scope={scope!r}.[/dim]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Target", overflow="fold")
    table.add_column("Kind", min_width=4)
    table.add_column("Size", justify="right")
    for path in targets:
        kind = "dir" if path.is_dir() else "file"
        table.add_row(str(path), kind, _format_size(path))
    console.print(table)
    console.print(
        f"[yellow]These {len(targets)} item(s) would be deleted by[/yellow] "
        f"[cyan]pythinker cleanup run --scope {scope}[/cyan]"
    )


@cleanup_app.command("run")
def cleanup_run(
    scope: str = typer.Option("config", "--scope", "-s", help="config | credentials | sessions | full"),
    confirm: str = typer.Option("", "--confirm", help="Type the literal string 'reset' to authorize"),
    backup: bool = typer.Option(True, "--backup/--no-backup", help="Snapshot config before deleting"),
):
    """Execute the cleanup. Requires ``--confirm reset`` to authorize.

    Same typed-consent shape as the wizard's ``--reset`` flow — ``y/N``
    isn't enough for an irreversible op. ``--backup`` (default on)
    snapshots the current config to ``backups/config.<ts>.pre-cleanup.json``
    before deletion so the destructive op stays undoable for at least the
    config-only scope.
    """
    import shutil
    from datetime import datetime

    from pythinker.config.loader import get_config_path

    if scope not in _VALID_CLEANUP_SCOPES:
        console.print(f"[red]Error:[/red] invalid scope {scope!r}")
        raise typer.Exit(1)
    if confirm != "reset":
        # Exact-match required: ``--confirm RESET`` / ``--confirm reset``
        # / ``--confirm " reset "`` are all rejected. Help text and the
        # consent UX both say "the literal string 'reset'"; the check
        # must match that contract — a typed-consent gate that quietly
        # accepts variants is a worse foot-gun than a strict one.
        console.print(
            "[red]Refusing to run without consent.[/red] "
            "Re-run with [cyan]--confirm reset[/cyan] (literal lowercase) to authorize."
        )
        raise typer.Exit(1)

    targets = _cleanup_targets(scope)
    if not targets:
        console.print(f"[dim]Nothing to clean at scope={scope!r}.[/dim]")
        return

    # Safety backup of current config — only meaningful when config is
    # part of the scope (every scope includes it, so always-on).
    if backup:
        cfg_path = get_config_path()
        if cfg_path.exists():
            bkdir = _backup_dir()
            bkdir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            safety = bkdir / f"config.{stamp}.pre-cleanup.json"
            try:
                shutil.copy2(cfg_path, safety)
                console.print(f"Safety backup: {safety}")
            except OSError as exc:
                console.print(f"[red]Error:[/red] safety backup failed: {exc}")
                console.print("[dim]Aborting cleanup so the config stays recoverable.[/dim]")
                raise typer.Exit(1)

    deleted = 0
    failed = 0
    for path in targets:
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            console.print(f"  removed {path}")
            deleted += 1
        except OSError as exc:
            console.print(f"  [red]failed[/red] {path}: {exc}")
            failed += 1

    console.print(
        f"[green]✓[/green] cleanup scope={scope!r}: {deleted} removed, {failed} failed"
    )
    if failed:
        raise typer.Exit(1)


# ============================================================================
# Release-readiness checks (`pythinker release check`)
# ============================================================================
#
# Cheap checks (PEP 440, __init__ fallback equality, CHANGELOG section, git
# tag) always run. Heavy checks (`python -m build`, `twine check dist/*`,
# wheel filename verification) only run when the user passes `--build`. The
# same orchestrator is imported by the publish workflow so CI and a
# maintainer's laptop see the identical pass/fail report.

_RELEASE_STATUS_GLYPH = {
    "ok": ("[green]✓[/green]", "ok"),
    "warn": ("[yellow]⚠[/yellow]", "warn"),
    "fail": ("[red]✗[/red]", "fail"),
    "skip": ("[dim]·[/dim]", "skip"),
}


@release_app.command("check")
def release_check(
    build: bool = typer.Option(
        False,
        "--build",
        help="Also run the heavy checks: python -m build, twine check dist/*, wheel filename.",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Treat warnings as failures (CI flag).",
    ),
) -> None:
    """Run release-readiness checks against the current working tree.

    Exit codes:
      0 — all checks passed (warnings allowed unless --strict).
      1 — at least one check failed (or --strict promoted a warning).
    """
    from pythinker.release.checks import run_checks

    repo_root = Path(__file__).resolve().parents[2]
    report = run_checks(repo_root, build=build)

    title = "Release readiness"
    if report.version:
        title = f"{title} — pythinker-ai {report.version}"
    console.print()
    console.print(f"[bold]{title}[/bold]")
    console.print()

    for r in report.results:
        glyph, _label = _RELEASE_STATUS_GLYPH[r.status]
        console.print(f"  {glyph}  [bold]{r.name}[/bold]: {r.message}")
        if r.detail:
            console.print(f"     [dim]{r.detail}[/dim]")

    has_failure = bool(report.failures)
    has_warning = bool(report.warnings)

    console.print()
    if has_failure:
        console.print(
            f"[red]✗[/red] {len(report.failures)} blocking issue(s); release MUST NOT proceed."
        )
        raise typer.Exit(1)
    if strict and has_warning:
        console.print(
            f"[yellow]⚠[/yellow] {len(report.warnings)} warning(s); --strict treats warnings as failures."
        )
        raise typer.Exit(1)
    if has_warning:
        console.print(
            f"[yellow]⚠[/yellow] {len(report.warnings)} non-blocking warning(s); review before tagging."
        )
    console.print(
        "[green]✓[/green] release-readiness checks passed."
    )


if __name__ == "__main__":
    app()
