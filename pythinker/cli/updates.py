"""Update-banner and in-place-upgrade plumbing for the CLI.

Carved out of ``pythinker/cli/commands.py`` per the §E1 simplification plan.
Pure code-move: no behavior change. The Typer command callbacks in
``commands.py`` remain the public CLI entry points; this module owns the
banner-emit / interactive-upgrade / log-status helpers they call.
"""

from __future__ import annotations

import os
import sys

from loguru import logger

from pythinker.config.paths import get_update_dir
from pythinker.config.schema import Config
from pythinker.utils.restart import set_restart_notice_to_env
from pythinker.utils.update import (
    UpdateInfo,
    check_for_update_sync,
    format_banner,
    mark_notified,
    suggested_upgrade_command,
    upgrade_command,
)


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
    from pythinker.cli.commands import console

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

    from pythinker.cli.commands import console

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
