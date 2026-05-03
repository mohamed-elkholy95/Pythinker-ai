# CLI Reference

| Command | Description |
|---------|-------------|
| `pythinker onboard` | Initialize config & workspace at `~/.pythinker/` (interactive) |
| `pythinker onboard --flow quickstart` | Non-interactive QuickStart flow |
| `pythinker onboard --flow manual` | Manual provider & auth selection flow |
| `pythinker onboard --non-interactive` | Non-interactive mode (with other flags) |
| `pythinker onboard --flow manual --auth --auth-method service_account` | Select a provider, auth method, and default model |
| `pythinker onboard -c <config> -w <workspace>` | Initialize or refresh a specific instance config and workspace |
| `pythinker agent -m "..."` | Chat with the agent |
| `pythinker agent -w <workspace>` | Chat against a specific workspace |
| `pythinker agent -w <workspace> -c <config>` | Chat against a specific workspace/config |
| `pythinker agent` | Interactive chat mode |
| `pythinker agent --no-markdown` | Show plain-text replies |
| `pythinker agent --logs` | Show runtime logs during chat |
| `pythinker tui` (alias `pythinker chat`) | Full-screen interactive chat |
| `pythinker serve` | Start the OpenAI-compatible API |
| `pythinker gateway` | Start the gateway |
| `pythinker status` | Show status |
| `pythinker doctor` | Diagnose install, config, and authentication state |
| `pythinker update` | Check for and install pythinker updates from PyPI |
| `pythinker upgrade` | Convenience alias of `pythinker update -y --restart` |
| `pythinker token` | Generate a strong random token (e.g. for the WebSocket channel) |
| `pythinker provider login openai-codex` | OAuth login for providers |
| `pythinker channels login <channel>` | Authenticate a channel interactively |
| `pythinker channels status` | Show channel state (registry view) |
| `pythinker channels list` | Show enabled / configured state for every channel adapter |
| `pythinker auth list` | Show authentication state for every provider in the registry |
| `pythinker auth logout <provider>` | Delete the stored OAuth token for an OAuth provider |
| `pythinker config get <path>` | Print one config value (dotted path) |
| `pythinker config set <path> <value>` | Write one config value back to `~/.pythinker/config.json` |
| `pythinker config unset <path>` | Reset one field to its schema default |
| `pythinker restart gateway` | Stop the running gateway and re-exec a fresh one |
| `pythinker restart api` | Stop the running API server and re-exec a fresh one |
| `pythinker backup create` | Atomically copy `config.json` to a timestamped backup |
| `pythinker backup list` | Show all on-disk backups |
| `pythinker backup verify <path>` | Round-trip a backup through the schema |
| `pythinker backup restore <path>` | Restore a backup to `~/.pythinker/config.json` |
| `pythinker cleanup plan` | Dry-run: list every file/dir a cleanup would delete |
| `pythinker cleanup run` | Execute the cleanup (requires `--confirm reset`) |

Interactive mode exits: `exit`, `quit`, `/exit`, `/quit`, `:q`, or `Ctrl+D`.

## `pythinker tui` (alias `pythinker chat`)

Open the full-screen TUI chat against the configured agent loop.

```
pythinker tui [--workspace DIR] [--session KEY] [--config PATH]
              [--theme NAME] [--logs FILE]
```

| Flag | Default | Notes |
|---|---|---|
| `--workspace`, `-w` | from config | Workspace root |
| `--session`, `-s` | `cli:tui` | Session key |
| `--config`, `-c` | `~/.pythinker/config.json` | Config file |
| `--theme` | `cli.tui.theme` (`default`) | Override TUI theme for this run only |
| `--logs` | `~/.pythinker/logs/tui-<pid>.log` | Where loguru records go for the TUI's lifetime |

`pythinker tui` is interactive-only; for one-shot scripted use, run
`pythinker agent -m "..."`.

**Slash commands:** `/help`, `/exit` (alias `/quit`), `/clear`,
`/new`, `/sessions` (alias `/session`), `/model` (alias `/models`),
`/provider` (alias `/providers`), `/theme` (alias `/themes`),
`/status`, `/mcp`, `/stop`, `/restart`. List-style commands open a fuzzy
filterable picker overlay.

`/mcp` shows configured/connected MCP servers and registered MCP
capabilities. It refreshes MCP server config from disk before opening so
edits to `tools.mcpServers` are visible in the running TUI. `/mcp reconnect`
closes existing MCP sessions, unregisters old MCP capabilities, and reconnects
from the current disk config.

Built-in web search providers are separate from MCP. For example,
`tools.web.search.provider = "tavily"` enables the built-in `web_search` tool;
Tavily appears in `/mcp` only if you also configure a Tavily MCP server under
`tools.mcpServers`.

`/login`, `/onboard`, `/agents`, `/mcps`, `/skills`, and
`/timeline`/`/stash`/`/subagent` are not supported inside the TUI; run
the equivalent `pythinker` subcommand from the host shell instead.

**Editor keys:** `Enter` submits the current message. If a turn is already
running, `Enter` queues the message and sends it after the current turn
finishes. Use `Ctrl+J` to insert a newline without submitting (some
terminals don't pass `Shift+Enter` to programs).

**Persistence:** model, provider, and theme changes persist immediately to the
active config file. MCP edits made outside the TUI are picked up the next time
you open `/mcp` or run `/mcp reconnect`.

**Logs:** the TUI redirects all loguru output to the log file for its
lifetime. To watch logs live, `tail -f` the file in another terminal.

## `pythinker doctor`

Diagnose install, config, and authentication state. Returns a non-zero
exit code when something is broken so it can be wired into CI / health
checks.

```
pythinker doctor [--non-interactive]
```

| Flag | Default | Notes |
|---|---|---|
| `--non-interactive` | off | Terse output suitable for CI / scripting. |

## `pythinker update`

Check for and install pythinker updates from PyPI. Detects how
pythinker was installed (pip, uv tool, pipx) and picks the matching
upgrade command; refuses to auto-upgrade installs it can't safely
manage and prints the suggested manual command instead.

```
pythinker update [--check] [-y/--yes] [--restart] [--prerelease] [--target VERSION]
```

| Flag | Default | Notes |
|---|---|---|
| `--check` | off | Check only; don't install. |
| `-y`, `--yes` | off | Skip confirmation. |
| `--restart` | off | POSIX only: re-exec pythinker after a successful upgrade. |
| `--prerelease` | off | Include pre-releases when picking the latest version. |
| `--target VERSION` | unset | Install **exactly** this PEP 440 version (e.g. `2.0.0`). Refused on editable / container / unknown installs. **Required** to cross a major version — `pythinker upgrade` will refuse `1.x → 2.x` without an explicit `--target`. |

Concurrent runs are guarded by a file lock under
`~/.pythinker/update/.lock` so two upgrades can't race.

### Command semantics — exact-version vs latest-stable

The two paths differ in **what** they install:

| Intent | Command |
|---|---|
| Install / pin exactly `2.0.0` | `pythinker update --target 2.0.0 -y` |
| Stay at the latest stable release | `pythinker upgrade` |
| Just check what's out there | `pythinker update --check` |

Per install method, `--target` translates to:

| Install method | Resolved command |
|---|---|
| uv tool | `uv tool install --reinstall "pythinker-ai==2.0.0"` |
| pipx | `pipx install --force "pythinker-ai==2.0.0"` |
| pip in a venv | `python -m pip install --force-reinstall "pythinker-ai==2.0.0"` |
| system pip | *refused* — printed only: `python -m pip install --force-reinstall "pythinker-ai==2.0.0"` |
| editable / container / unknown | *refused* — printed only with the manual rebuild path |

Plain `pip install -U pythinker-ai==2.0.0` works too, but it's
semantically noisy: the **exact pin controls the version**, not `-U`.

## `pythinker upgrade`

Convenience alias of `pythinker update -y --restart`: by default this
upgrades and restarts in one step. Always picks the latest stable; will
**refuse to cross a major version** (e.g. `1.9.x → 2.0.0`) — re-run with
`pythinker update --target 2.0.0` to opt in explicitly.

```
pythinker upgrade [-y/--yes] [--no-restart] [--prerelease]
```

| Flag | Default | Notes |
|---|---|---|
| `-y`, `--yes` | off | Kept for CLI symmetry; an implicit `-y` is always passed to `update`. |
| `--no-restart` | off | Don't re-exec pythinker after upgrading. |
| `--prerelease` | off | Include pre-releases when picking the latest version. |

## `pythinker release`

Maintainer-only. Runs the same release-readiness gate as the
publish workflow.

```
pythinker release check [--build] [--strict]
```

Cheap checks always run:

- **pep440-version** — `pyproject.toml` `[project] version` parses as PEP 440.
- **init-fallback** — `pythinker/__init__.py` hardcoded fallback equals the
  `pyproject.toml` version (drift makes `pythinker --version` lie in
  source-only checkouts).
- **changelog-section** — `CHANGELOG.md` has a `## [VERSION] - YYYY-MM-DD`
  header for the current version (or `## [VERSION]`). Promotes a stalled
  `[Unreleased]` into a hard error with a fix hint.
- **git-tag-match** — when HEAD is tagged, the tag must equal `v{version}`
  (skips when HEAD is untagged; the publish workflow re-checks this gate
  on the `release` event).

Heavy checks (opt-in via `--build`):

- **build** — `python -m build` succeeds.
- **twine-check** — `twine check dist/*` passes.
- **wheel-filename** — the built wheel filename embeds the resolved version.

Exit codes: `0` on pass (warnings allowed unless `--strict`), `1` on any
fail. Run before `git tag v…` and before `gh release create`.

## `pythinker token`

Generate a cryptographically strong random token, suitable for
`channels.websocket.token` / `channels.websocket.token_issue_secret`.
Uses `secrets.token_urlsafe`, so the result is URL-safe and can be
passed in a query string on the WebSocket handshake.

```
pythinker token [--bytes N]
```

| Flag | Default | Notes |
|---|---|---|
| `--bytes`, `-b` | `32` | Byte length before url-safe encoding (16–64). 32 = 256 bits. |

## `pythinker auth`

Provider authentication state. Read-only inspection plus an explicit
logout for OAuth providers.

```
pythinker auth list   [--config PATH]
pythinker auth logout <provider> [-y/--yes]
```

- **`auth list`** — Show authentication state for every provider in the
  registry. Never triggers an OAuth flow; tokens are inspected from
  on-disk storage only. State column values: `AUTH`, `MISSING`,
  `ERROR`, `NOT-CONFIGURED`. Use `pythinker provider login <name>` to
  refresh missing or expired credentials.
- **`auth logout <provider>`** — Delete the stored OAuth token for
  `provider` (e.g. `openai_codex`, `github_copilot`). Confirms before
  unlinking unless `-y` is given. API-key providers are not
  applicable; use `pythinker config unset providers.<name>.api_key`
  instead.

## `pythinker config`

Get / set / unset a single config field by dotted path. Reads and
writes `~/.pythinker/config.json` directly.

```
pythinker config get <path>
pythinker config set <path> <value>
pythinker config unset <path>
```

- Dotted paths accept both snake_case and camelCase at every segment
  (e.g. `agents.defaults.model` or `agents.defaults.idleCompactAfterMinutes`).
- **`set`** JSON-parses the value when possible so booleans and integers
  are stored typed (`true`, not `"true"`). The full config is
  re-validated through the schema after the edit; type errors surface
  immediately, not at the next gateway boot.
- **`unset`** resets a Pydantic field to its schema default; for `dict`
  entries (e.g. an MCP server) the entry is removed.
- All three commands print a "restart the gateway/api" hint after
  writes — config is loaded once at startup.

## `pythinker restart`

Stop a running pythinker service and re-exec a fresh one in the
foreground. Locates listeners with `ss -ltnp`; SIGTERMs them and
escalates to SIGKILL after `--timeout`.

```
pythinker restart gateway [-p/--port N] [-c/--config PATH] [--no-start]
pythinker restart api     [-p/--port N] [-c/--config PATH] [--no-start]
```

| Flag | Default | Notes |
|---|---|---|
| `-p`, `--port` | from config | Override gateway/api port. |
| `-c`, `--config` | `~/.pythinker/config.json` | Config file. |
| `--no-start` | off | Stop only — don't restart. |

## `pythinker backup`

Snapshot, list, verify, and restore `~/.pythinker/config.json`.
Backups live under `~/.pythinker/backups/` as
`config.<YYYYMMDD-HHMMSS>[.<label>].json`. The list view also surfaces
wizard-generated `config.json.bak.<ts>` files in the parent directory
so both stores are visible at once.

```
pythinker backup create  [-l/--label NAME]
pythinker backup list
pythinker backup verify  <path>
pythinker backup restore <path> [-y/--yes]
```

- **`create`** atomically copies via `shutil.copy2` (mtime preserved)
  and prints the destination path plus a 12-char SHA-256 prefix.
- **`verify`** round-trips the backup through the same loader the
  gateway uses; rejects corrupt JSON, schema-incompatible legacy
  fields, and missing required keys.
- **`restore`** verifies the backup before swapping; refuses if it
  would produce an invalid config. Always saves a safety backup
  (`config.pre-restore.<ts>.json`) of the current config first; the
  swap is atomic via `os.replace`.

## `pythinker cleanup`

Plan or run destructive resets. Mirrors the wizard's `--reset` flow.

```
pythinker cleanup plan [-s/--scope SCOPE]
pythinker cleanup run  [-s/--scope SCOPE] [--confirm reset] [--backup/--no-backup]
```

| Flag | Default | Notes |
|---|---|---|
| `-s`, `--scope` | `config` | One of `config`, `credentials`, `sessions`, `full` (cumulative — each scope includes the previous one). |
| `--confirm` | (empty) | **Required for `run`**: must be the literal lowercase string `reset`. `RESET` / `" reset "` etc. are rejected. |
| `--backup` / `--no-backup` | `--backup` | Snapshot the current config to `backups/config.<ts>.pre-cleanup.json` before deleting. On for safety; pass `--no-backup` to skip. |

Use `pythinker cleanup plan --scope full` first to see exactly what
`run` would delete; the plan is the single source of truth for the
target list, so the dry-run cannot lie about what `run` does.
