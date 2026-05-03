# Install and Quick Start

## Install

```bash
uv tool install pythinker-ai
```

This installs Pythinker into an isolated environment and puts the `pythinker` binary on your `PATH`. [uv](https://docs.astral.sh/uv/) handles Python version, PATH, and upgrades automatically.

### Don't have `uv`?

```bash
# macOS / Linux / WSL:
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows:
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then re-run `uv tool install pythinker-ai`.

### Alternative installers

| Tool | Command | Notes |
|---|---|---|
| `pipx` | `pipx install pythinker-ai` | Equivalent to `uv tool install`, slower. Requires `pipx` from your OS package manager (`sudo dnf install pipx`, `sudo apt install pipx`, `brew install pipx`). |
| `pip --user` | `pip install --user pythinker-ai` | Not isolated. May need `~/.local/bin` on `PATH` manually. |
| Source | `git clone … && uv sync --all-extras` | For contributors — editable install. |

> [!WARNING]
> Don't run `pip install pythinker-ai` into system Python on Fedora 38+, Debian 12+, Ubuntu 23.04+, or Homebrew macOS. [PEP 668](https://peps.python.org/pep-0668/) blocks it by default, and the workarounds tend to break your system Python. Use `uv tool install` instead.

### Update to latest version

```bash
uv tool upgrade pythinker-ai     # or: pipx upgrade pythinker-ai
pythinker --version
```

**Using WhatsApp?** Rebuild the local bridge after upgrading:

```bash
rm -rf ~/.pythinker/bridge
pythinker channels login whatsapp
```

### Verify install

```bash
pythinker doctor
```

`doctor` checks Python version, install path + `PATH` membership, config validity, workspace writability, and OAuth token presence for your default provider. If anything is wrong, it prints the exact command to fix it. Run this first whenever something doesn't work.

## Quick Start

```bash
pythinker onboard                           # writes ~/.pythinker/config.json
pythinker provider login openai-codex       # OAuth sign-in — opens your browser
pythinker agent                             # interactive chat
```

That's it. `pythinker onboard` ships a config preconfigured for **OpenAI Codex via ChatGPT OAuth** — no API key needed; you just sign in via `provider login`.

### Using a different provider or model?

Edit `~/.pythinker/config.json`:

```json
{
  "agents": {
    "defaults": {
      "model": "openai-codex/gpt-5.5"
    }
  }
}
```

Provider is auto-detected from the model prefix (`openai-codex/…`, `anthropic/…`, `openrouter/…`, `deepseek/…`, etc.). For the full catalog of 25+ providers, required API keys, and model-specific options, see [`configuration.md`](./configuration.md). For web search, see the [web-search section](./configuration.md#web-search).

### Full-screen TUI chat

For a richer interactive experience, run `pythinker tui` (alias
`pythinker chat`). It opens a full-screen chat with slash-command
pickers for sessions, models, providers, and themes. The CLI
`pythinker agent` remains the right tool for one-shot prompts and
scripts.

### Troubleshooting

- **`pythinker` command not found** — run `python -m pythinker doctor` for a diagnosis; usually `~/.local/bin` isn't on your `PATH`. `uv tool update-shell` fixes it.
- **Anything else broken** — `pythinker doctor` is the one-stop diagnostic. Paste its output in a GitHub issue if you need help.
