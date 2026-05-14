"""Reusable note-panel content blocks for the onboarding wizard."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rich import box
from rich.align import Align
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from rich.console import Console


SETUP_STEPS = [
    ("Welcome", "Security model + setup map"),
    ("Mode", "QuickStart defaults or full manual control"),
    ("Model/Auth", "Provider, credential path, and default model"),
    ("Workspace", "Agent files, memory, and local tools"),
    ("Channels", "Optional chat-platform integrations"),
    ("Review", "Redacted diff before writing config"),
    ("Health", "Workspace, model/auth, and gateway preflight"),
    ("Launch", "Next commands or start gateway"),
]

SECURITY_DISCLAIMER = [
    "Pythinker is alpha software. Expect rough edges.",
    "By default, Pythinker is a personal agent: one trusted operator boundary.",
    "It can read files and run shell commands if tools are enabled.",
    "A bad prompt can trick it into doing unsafe things.",
    "",
    "Pythinker is not a hostile multi-tenant boundary by default.",
    "If multiple users can message one tool-enabled agent,",
    "they share that delegated tool authority.",
    "",
    "Recommended baseline:",
    "- Pairing/allowlists + mention gating.",
    "- Sandbox + least-privilege tools.",
    "- Keep secrets out of the agent's reachable filesystem.",
    "",
    "Only enable tools/channels when you trust the people and inputs that can reach them.",
    "Docs: docs/security.md",
]


def _display_path(value: str | Path | None) -> str:
    if value is None:
        return "(default)"
    raw = str(value)
    try:
        home = str(Path.home())
        if raw.startswith(home):
            return "~" + raw[len(home):]
    except RuntimeError:
        pass
    return raw


def render_welcome_panel(
    console: "Console",
    *,
    version: str,
    config_path: str | Path,
    workspace: str | Path | None,
    flow: str,
    non_interactive: bool,
) -> None:
    """Render the first-run terminal graphic before the clack timeline starts.

    Inspired by OpenClaw's first-run setup surface: a compact brand mark,
    one-sentence security framing, and a visible map of every step before
    the wizard asks for input. Kept as a Rich panel so non-interactive runs
    and narrow terminals still degrade to plain terminal output.
    """
    title = Text("🐍 Pythinker", style="bold cyan")
    title.append(f"  {version}", style="dim")

    steps = Table.grid(padding=(0, 1))
    steps.add_column(justify="center", width=3)
    steps.add_column(no_wrap=True)
    steps.add_column(style="dim")
    for index, (name, hint) in enumerate(SETUP_STEPS, 1):
        steps.add_row(f"{index}", name, hint)

    details = Table.grid(padding=(0, 2))
    details.add_column(style="bold")
    details.add_column()
    mode_label = f"{flow or 'interactive'}{' (non-interactive)' if non_interactive else ''}"
    details.add_row("Mode", mode_label)
    details.add_row("Config", _display_path(config_path))
    details.add_row("Workspace", _display_path(workspace))

    body = Table.grid(expand=True)
    body.add_row(Align.center(title))
    body.add_row("")
    body.add_row(
        Align.center(
            Text(
                "Guided setup for one assistant across local tools, APIs, and chat platforms.",
                style="white",
            )
        )
    )
    body.add_row("")
    body.add_row(details)
    body.add_row("")
    body.add_row(steps)
    body.add_row("")
    body.add_row(
        Text(
            "Security: treat connected channels and tools as delegated authority.",
            style="yellow",
        )
    )

    console.print(
        Panel(
            body,
            title="setup",
            subtitle="Ctrl-C cancels before save",
            border_style="cyan",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )


CHANNELS_INTRO = [
    "Pythinker can listen on chat platforms.",
    "Each channel needs its own bot/account credential.",
    "You can enable more later by re-running onboard.",
    "Docs: docs/chat-apps.md",
]

CHANNEL_INSTRUCTIONS = {
    "telegram": [
        "1) Open Telegram and chat with @BotFather",
        "2) Run /newbot (or /mybots) and copy the token (123456:ABC...)",
        "3) Set 'enabled' = true and paste the token below",
        "Tip: you can also set TELEGRAM_BOT_TOKEN in env and use ${TELEGRAM_BOT_TOKEN}.",
    ],
    "discord": [
        "1) Visit https://discord.com/developers/applications",
        "2) Create an application, then a Bot under it",
        "3) Copy the Bot token; enable the Message Content intent",
        "Tip: env var DISCORD_BOT_TOKEN works too.",
    ],
    "slack": [
        "1) Visit https://api.slack.com/apps and create a new app",
        "2) Enable Socket Mode and create an App-level token (xapp-...)",
        "3) Add a Bot user, install to workspace, copy the Bot token (xoxb-...)",
    ],
    "email": [
        "1) Generate an IMAP/SMTP app password for your provider",
        "2) Set imap_host/imap_port + smtp_host/smtp_port (defaults are common ones)",
        "3) Set username + password (use ${EMAIL_PASSWORD} env-var indirection)",
    ],
    "matrix": [
        "1) Pick a homeserver (matrix.org or self-hosted)",
        "2) Get an access token: curl -XPOST -d '{\"type\":\"m.login.password\",...}'",
        "   to /_matrix/client/r0/login, or use Element 'View Source' settings",
        "3) Note: needs the 'matrix' extra installed (libolm-dev on Linux)",
    ],
    "msteams": [
        "1) Register a Bot in https://dev.botframework.com/bots/new",
        "2) Create an Azure AD app reg; copy Application ID + client secret",
        "3) Set tenant_id, app_id, app_password (use ${MSTEAMS_APP_PASSWORD})",
    ],
    "whatsapp": [
        "1) WhatsApp uses the Node Baileys bridge (bundled in pythinker/bridge/)",
        "2) Run: cd bridge && npm install && npm run build (once)",
        "3) Set bridge_port; first run prints a QR code — scan it from your phone",
    ],
    "websocket": [
        "WebSocket runs as part of `pythinker gateway` (no separate creds).",
        "Set 'enabled' = true and (optionally) 'token' to require auth from clients.",
        "Use `pythinker token` to generate a strong random token.",
    ],
}
