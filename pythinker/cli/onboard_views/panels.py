"""Reusable note-panel content blocks for the onboarding wizard."""

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
    "Docs: docs/security.md",
]

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
