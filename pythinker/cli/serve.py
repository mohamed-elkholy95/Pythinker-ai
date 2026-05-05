"""Server / gateway preflight + WebUI URL helpers.

Carved out of ``pythinker/cli/commands.py`` per the §E1 simplification plan.
The Typer ``serve`` and ``gateway`` callbacks live in ``commands.py``; the
preflight, WebUI URL resolution, and startup-status helpers they share are
hosted here.
"""

from __future__ import annotations

import sys
from typing import Any

import typer


def _preflight_port_or_die(host: str, port: int, *, label: str = "Service") -> None:
    """Bail out with a clear, actionable message if ``host:port`` is already bound.

    Without this, the gateway/serve startup logs "✓ Cron / ✓ Heartbeat / Agent loop
    started" several seconds before crashing with a raw asyncio EADDRINUSE traceback,
    which is confusing. We probe the bind upfront and raise typer.Exit(1) on failure.
    """
    import errno
    import socket

    from pythinker.cli.commands import console

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
    from pythinker.cli.commands import console

    if websocket_channel is not None:
        console.print(f"[green]✓[/green] WebUI: {_webui_url_from_channel(websocket_channel)}")
        return
    console.print(
        "[dim]WebUI: disabled "
        "(add channels.websocket.enabled=true to serve http://127.0.0.1:8765/)[/dim]"
    )
